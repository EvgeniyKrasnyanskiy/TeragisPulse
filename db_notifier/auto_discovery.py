import os
import socket
import sys
import json
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
import psycopg2
from dotenv import load_dotenv

# Умное определение рабочей директории (для работы как в виде скрипта, так и в скомпилированном .exe)
def get_real_work_dir():
    if getattr(sys, 'frozen', False):
        # Если запущено как скомпилированный бинарник, берем папку, где лежит сам .exe
        return os.path.dirname(sys.executable)
    # Если запущен скрипт, берем папку скрипта
    return os.path.dirname(os.path.abspath(__file__))

work_dir = get_real_work_dir()

def log_debug(msg):
    """Записывает отладочные сообщения в файл notifier_debug.txt рядом с .exe"""
    try:
        debug_path = os.path.join(work_dir, "notifier_debug.txt")
        with open(debug_path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
    except Exception:
        pass

# Каскадная загрузка .env из папки запуска, папки скрипта или родительских каталогов
env_path1 = os.path.join(work_dir, '.env')
env_path2 = os.path.join(os.path.dirname(work_dir), '.env')
log_debug(f"Поиск .env по путям:\n  1: {env_path1} (найден: {os.path.exists(env_path1)})\n  2: {env_path2} (найден: {os.path.exists(env_path2)})")

load_dotenv(env_path1)
load_dotenv(env_path2)

# Если скрипт лежит в подпапке db_notifier, корень будет на 2 уровня выше
if not getattr(sys, 'frozen', False):
    env_path3 = os.path.join(os.path.dirname(os.path.dirname(work_dir)), '.env')
    log_debug(f"  3: {env_path3} (найден: {os.path.exists(env_path3)})")
    load_dotenv(env_path3)

# Дефолтные реквизиты БД
DEFAULT_DB_NAME = os.getenv('DB_NAME', 'veri')
DEFAULT_DB_USER = os.getenv('DB_USER', 'tera')
DEFAULT_DB_PASS = os.getenv('DB_PASS', 'tera123')
DEFAULT_DB_PORT = os.getenv('DB_PORT', '5432')

CACHE_DIR = os.path.expanduser('~/.config/teragis_notifier')
CACHE_FILE = os.path.join(CACHE_DIR, 'db_host.cache')

def get_db_credentials():
    """Возвращает реквизиты авторизации БД."""
    return {
        "dbname": DEFAULT_DB_NAME,
        "user": DEFAULT_DB_USER,
        "password": DEFAULT_DB_PASS,
        "port": DEFAULT_DB_PORT
    }

def get_cached_host():
    """Читает кэшированный IP-адрес из домашней папки."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('host')
        except Exception:
            pass
    return None

def set_cached_host(host):
    """Сохраняет работающий IP-адрес в кэш."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'host': host}, f)
    except Exception:
        pass

def test_pg_connection(host, creds):
    """Проверяет реальное подключение к PostgreSQL с подробным логированием ошибок."""
    try:
        conn = psycopg2.connect(
            host=host,
            dbname=creds['dbname'],
            user=creds['user'],
            password=creds['password'],
            port=creds['port'],
            connect_timeout=2
        )
        conn.close()
        log_debug(f"Успешное подключение к {host}!")
        return True
    except Exception as e:
        log_debug(f"Не удалось подключиться к {host}. Ошибка: {e}")
        return False

async def scan_port(ip, port, timeout=0.5):
    """Быстрая асинхронная проверка доступности TCP порта."""
    try:
        conn = asyncio.open_connection(ip, port)
        await asyncio.wait_for(conn, timeout=timeout)
        return ip
    except Exception:
        return None

async def find_postgres_in_subnet(subnet_prefix, port, executor, creds):
    """Асинхронно сканирует подсеть на наличие порта 5432 и проверяет авторизацию."""
    tasks = []
    # Сканируем хосты от 1 до 254
    for i in range(1, 255):
        ip = f"{subnet_prefix}{i}"
        tasks.append(scan_port(ip, port))
    
    # Ждем завершения всех проверок портов
    results = await asyncio.gather(*tasks)
    active_ips = [ip for ip in results if ip is not None]
    
    if not active_ips:
        return None

    # Для найденных активных IP проверяем авторизацию Postgres (в пуле потоков, т.к. psycopg2 блокирующий)
    loop = asyncio.get_event_loop()
    for ip in active_ips:
        is_ok = await loop.run_in_executor(executor, test_pg_connection, ip, creds)
        if is_ok:
            return ip
            
    return None

def get_local_ip():
    """Определяет собственный локальный IP-адрес."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Не выполняет реального сетевого обмена, просто определяет интерфейс
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def discover_db_host():
    """
    Основной метод автоопределения хоста базы данных.
    Каскад приоритетов: Хост из .env -> Кэш -> Локальный localhost -> Сканирование сети
    """
    log_debug("--- Запуск автоопределения сервера БД ---")
    creds = get_db_credentials()
    log_debug(f"Используемые реквизиты авторизации: dbname={creds['dbname']}, user={creds['user']}, port={creds['port']}")
    
    # 1. Проверяем хост, прописанный в .env (наивысший приоритет)
    env_host = os.getenv('DB_HOST')
    log_debug(f"1. Проверка хоста из настроек (DB_HOST): {env_host}")
    if env_host:
        if test_pg_connection(env_host, creds):
            log_debug(f"-> Хост {env_host} из .env успешно подключен!")
            set_cached_host(env_host)
            return env_host
        else:
            log_debug(f"-> Хост {env_host} из .env НЕ отвечает.")

    # 2. Проверяем кэш
    cached = get_cached_host()
    log_debug(f"2. Проверка кэшированного хоста: {cached}")
    if cached:
        if test_pg_connection(cached, creds):
            log_debug(f"-> Кэш {cached} рабочий. Возвращаем его.")
            return cached
        else:
            log_debug(f"-> Кэш {cached} не отвечает.")

    # 3. Проверяем localhost
    log_debug("3. Проверка подключения к localhost (127.0.0.1)...")
    if test_pg_connection('127.0.0.1', creds):
        log_debug("-> Локальный хост рабочий!")
        set_cached_host('127.0.0.1')
        return '127.0.0.1'

    # 4. Сканируем локальную сеть
    local_ip = get_local_ip()
    log_debug(f"4. Проверка сканирования сети. Локальный IP: {local_ip}")
    if local_ip == '127.0.0.1':
        log_debug("-> Локальный IP 127.0.0.1, сканирование отменено.")
        return None # Мы не в сети

    parts = local_ip.split('.')
    if len(parts) != 4:
        log_debug(f"-> Некорректный локальный IP {local_ip}, сканирование отменено.")
        return None
        
    subnet_prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
    log_debug(f"-> Запускаем асинхронное сканирование подсети: {subnet_prefix}0/24...")
    
    # Запуск асинхронного сканера
    try:
        # Python 3.7+
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with ThreadPoolExecutor(max_workers=10) as executor:
            found_host = loop.run_until_complete(
                find_postgres_in_subnet(subnet_prefix, int(creds['port']), executor, creds)
            )
        loop.close()
        
        if found_host:
            set_cached_host(found_host)
            return found_host
    except Exception:
        pass

    return None

if __name__ == '__main__':
    print("Ищем сервер базы данных...")
    host = discover_db_host()
    if host:
        print(f"Успешно найден хост БД: {host}")
    else:
        print("Не удалось автоматически обнаружить сервер базы данных.")
