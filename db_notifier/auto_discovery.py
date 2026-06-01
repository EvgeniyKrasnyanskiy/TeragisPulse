# -*- coding: utf-8 -*-
import os
import socket
import sys
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
import psycopg2
from dotenv import load_dotenv

# Загрузка настроек из файлов (.env в текущей или родительской папке)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)

load_dotenv(os.path.join(current_dir, '.env'))
load_dotenv(os.path.join(parent_dir, '.env'))

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
    """Проверяет реальное подключение к PostgreSQL."""
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
        return True
    except Exception:
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
    Каскад: Кэш -> Локальный -> Прописанный в .env -> Сканирование сети
    """
    creds = get_db_credentials()
    
    # 1. Проверяем кэш
    cached = get_cached_host()
    if cached and test_pg_connection(cached, creds):
        return cached

    # 2. Проверяем localhost
    if test_pg_connection('127.0.0.1', creds):
        set_cached_host('127.0.0.1')
        return '127.0.0.1'

    # 3. Проверяем хост, прописанный в .env (если он отличен от локального)
    env_host = os.getenv('DB_HOST')
    if env_host and env_host not in ('127.0.0.1', 'localhost'):
        if test_pg_connection(env_host, creds):
            set_cached_host(env_host)
            return env_host

    # 4. Сканируем локальную сеть
    local_ip = get_local_ip()
    if local_ip == '127.0.0.1':
        return None # Мы не в сети

    parts = local_ip.split('.')
    if len(parts) != 4:
        return None
        
    subnet_prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
    
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
