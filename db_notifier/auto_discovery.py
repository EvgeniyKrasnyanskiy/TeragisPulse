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

# Ограничение размера лога: удаляем его, если он больше 1 МБ
try:
    _debug_file = os.path.join(work_dir, "notifier_debug.txt")
    if os.path.exists(_debug_file) and os.path.getsize(_debug_file) > 1024 * 1024:
        os.remove(_debug_file)
except Exception:
    pass

def log_debug(msg):
    """Записывает отладочные сообщения в консоль и в файл notifier_debug.txt рядом с .exe с ротацией в 1 МБ."""
    try:
        print("[DEBUG] {}".format(msg))
    except Exception:
        pass
    try:
        debug_path = os.path.join(work_dir, "notifier_debug.txt")
        # Проверяем размер лог-файла перед каждой записью
        mode = "a"
        try:
            if os.path.exists(debug_path) and os.path.getsize(debug_path) > 1024 * 1024:
                mode = "w"  # Очищаем файл, если он превысил 1 МБ
        except Exception:
            pass
            
        with open(debug_path, mode, encoding="utf-8") as f:
            f.write("{} - {}\n".format(time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except Exception:
        pass

# Каскадная загрузка .env из папки запуска, папки скрипта или родительских каталогов
env_path1 = os.path.join(work_dir, '.env')
env_path2 = os.path.join(os.path.dirname(work_dir), '.env')
log_debug("Поиск .env по путям:\n  1: {} (найден: {})\n  2: {} (найден: {})".format(env_path1, os.path.exists(env_path1), env_path2, os.path.exists(env_path2)))

load_dotenv(env_path1)
load_dotenv(env_path2)

# Если скрипт лежит в подпапке db_notifier, корень будет на 2 уровня выше
if not getattr(sys, 'frozen', False):
    env_path3 = os.path.join(os.path.dirname(os.path.dirname(work_dir)), '.env')
    log_debug("  3: {} (найден: {})".format(env_path3, os.path.exists(env_path3)))
    load_dotenv(env_path3)

# Дефолтные реквизиты БД
DEFAULT_DB_NAME = os.getenv('DB_NAME', 'veri')
DEFAULT_DB_USER = os.getenv('DB_USER', 'tera')
DEFAULT_DB_PASS = os.getenv('DB_PASS', 'tera123')
DEFAULT_DB_PORT = os.getenv('DB_PORT', '5432')

CACHE_DIR = os.path.expanduser('~/.config/teragis_notifier')
CACHE_FILE = os.path.join(CACHE_DIR, 'db_host.cache')

def mask_fio(fio: str) -> str:
    """Маскирует ФИО пациента для безопасности (например, ИВАНОВ ИВАН ИВАНОВИЧ -> ИВАНОВ И. И.)."""
    if not fio:
        return "НЕИЗВЕСТНЫЙ ПАЦИЕНТ"
    try:
        parts = fio.split()
        if len(parts) >= 3:
            return "{} {}. {}.".format(parts[0], parts[1][0], parts[2][0])
        elif len(parts) == 2:
            return "{} {}.".format(parts[0], parts[1][0])
        return fio
    except Exception:
        return fio

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
    """Сохраняет работающий IP-адрес в кэш с ограничением прав доступа."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'host': host}, f)
        if os.name != 'nt':
            try:
                os.chmod(CACHE_FILE, 0o600)
            except Exception:
                pass
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
        log_debug("Успешное подключение к {}!".format(host))
        return True
    except Exception as e:
        log_debug("Не удалось подключиться к {}. Ошибка скрыта для безопасности".format(host))
        return False

async def scan_port(ip, port, timeout=0.5):
    """Быстрая асинхронная проверка доступности TCP порта с закрытием сокета."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        writer.close()
        if hasattr(writer, 'wait_closed'):
            try:
                await writer.wait_closed()
            except Exception:
                pass
        return ip
    except Exception:
        return None

async def find_postgres_in_subnet(subnet_prefix, port, executor, creds):
    """Асинхронно сканирует подсеть на наличие порта 5432 и проверяет авторизацию."""
    tasks = []
    # Сканируем хосты от 1 до 254
    for i in range(1, 255):
        ip = "{}{}".format(subnet_prefix, i)
        tasks.append(scan_port(ip, port))
    
    # Ждем завершения всех проверок портов
    results = await asyncio.gather(*tasks)
    active_ips = [ip for ip in results if ip is not None]
    
    if not active_ips:
        return None

    # Для найденных активных IP проверяем авторизацию Postgres (в пуле потоков, т.к. psycopg2 блокирующий)
    try:
        loop = asyncio.get_running_loop()
    except AttributeError:
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

_net_scan_count = 0
MAX_NET_SCAN_ATTEMPTS = 3

def discover_db_host():
    """
    Основной метод автоопределения хоста базы данных.
    Каскад приоритетов: Хост из .env -> Кэш -> Локальный localhost -> Сканирование сети
    """
    global _net_scan_count
    log_debug("--- Запуск автоопределения сервера БД ---")
    creds = get_db_credentials()
    log_debug("Используемые реквизиты авторизации: dbname={}, port={} (авторизация по паролю)".format(creds['dbname'], creds['port']))
    
    # 1. Проверяем хост, прописанный в .env (наивысший приоритет)
    env_host = os.getenv('DB_HOST')
    log_debug("1. Проверка хоста из настроек (DB_HOST): {}".format(env_host))
    if env_host:
        if test_pg_connection(env_host, creds):
            log_debug("-> Хост {} из .env успешно подключен!".format(env_host))
            set_cached_host(env_host)
            return env_host
        else:
            log_debug("-> Хост {} из .env НЕ отвечает.".format(env_host))

    # 2. Проверяем кэш
    cached = get_cached_host()
    log_debug("2. Проверка кэшированного хоста: {}".format(cached))
    if cached:
        if test_pg_connection(cached, creds):
            log_debug("-> Кэш {} рабочий. Возвращаем его.".format(cached))
            return cached
        else:
            log_debug("-> Кэш {} не отвечает.".format(cached))

    # 3. Проверяем localhost
    log_debug("3. Проверка подключения к localhost (127.0.0.1)...")
    if test_pg_connection('127.0.0.1', creds):
        log_debug("-> Локальный хост рабочий!")
        set_cached_host('127.0.0.1')
        return '127.0.0.1'

    # 4. Сканируем локальную сеть
    if _net_scan_count >= MAX_NET_SCAN_ATTEMPTS:
        log_debug("4. Проверка сканирования сети: Лимит попыток сканирования сети исчерпан ({}/{}). Пропускаем.".format(_net_scan_count, MAX_NET_SCAN_ATTEMPTS))
        return None

    local_ip = get_local_ip()
    log_debug("4. Проверка сканирования сети. Локальный IP: {}. Попытка {}/{}.".format(local_ip, _net_scan_count + 1, MAX_NET_SCAN_ATTEMPTS))
    if local_ip == '127.0.0.1':
        log_debug("-> Локальный IP 127.0.0.1, сканирование отменено.")
        return None # Мы не в сети

    _net_scan_count += 1

    parts = local_ip.split('.')
    if len(parts) != 4:
        log_debug("-> Некорректный локальный IP {}, сканирование отменено.".format(local_ip))
        return None
        
    subnet_prefix = "{}.{}.{}.".format(parts[0], parts[1], parts[2])
    log_debug("-> Запускаем асинхронное сканирование подсети: {}0/24...".format(subnet_prefix))
    
    # Запуск асинхронного сканера
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        with ThreadPoolExecutor(max_workers=10) as executor:
            found_host = loop.run_until_complete(
                find_postgres_in_subnet(subnet_prefix, int(creds['port']), executor, creds)
            )
            if found_host:
                set_cached_host(found_host)
                return found_host
    except Exception as e:
        log_debug("Ошибка при сканировании подсети: {}".format(e))
    finally:
        try:
            loop.close()
        except Exception:
            pass

    return None

if __name__ == '__main__':
    print("Ищем сервер базы данных...")
    host = discover_db_host()
    if host:
        print("Успешно найден хост БД: {}".format(host))
    else:
        print("Не удалось автоматически обнаружить сервер базы данных.")
