import psycopg2
from psycopg2 import pool, extensions
import logging
import os
import configparser
import threading
from typing import Any, List, Optional, Union, Tuple

# Кастомное исключение для ошибок БД
class DatabaseError(Exception):
    """Базовое исключение для ошибок базы данных."""
    pass

# Настройка Логгирования
log_dir = os.path.dirname(os.path.abspath(__file__))
log_file = os.path.join(log_dir, "teragispulse.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Загрузка Конфигурации БД
config = configparser.ConfigParser()
config_path = os.path.join(log_dir, 'config.ini')

if not os.path.exists(config_path):
    logger.error(f"[DBConnect] Configuration file not found: {config_path}")

config.read(config_path, encoding='utf-8-sig')

# Пул соединений и замок
_pool: Optional[pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()

def _get_pool() -> Optional[pool.ThreadedConnectionPool]:
    global _pool
    if _pool is not None:
        return _pool
        
    with _pool_lock:
        if _pool is not None:
            return _pool
            
        DB_SECTION = 'postgresql'
        if DB_SECTION not in config:
            logger.error("[DBConnect] Configuration error: [%s] section not found in config.ini.", DB_SECTION)
            return None
            
        db_config = config[DB_SECTION]
        try:
            _pool = pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=10,
                dbname=db_config.get('database'),
                user=db_config.get('user'),
                password=db_config.get('password'),
                host=db_config.get('host'),
                port=db_config.get('port'),
                connect_timeout=5,
                options='-c statement_timeout=10000'
            )
            return _pool
        except Exception as e:
            logger.error(f"[DBConnect]: ошибка создания пула: {e}")
            return None

def close_db_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
                _pool = None
                logger.info("[DBConnect]: Пул соединений успешно закрыт.")
            except Exception as e:
                logger.error(f"[DBConnect]: Ошибка при закрытии пула: {e}")

def execute_query(query: str, params: Optional[Union[Tuple[Any, ...], List[Any]]] = None) -> List[Any]:
    """
    Выполняет SQL запрос и возвращает результат.
    Для SELECT возвращает список строк, для INSERT/UPDATE возвращает пустой список.
    В случае ошибки выбрасывает DatabaseError.
    """
    p = _get_pool()
    if p is None:
        raise DatabaseError("Не удалось инициализировать пул соединений")

    conn = None
    try:
        try:
            conn = p.getconn()
            if conn.closed != 0:
                raise psycopg2.OperationalError("Connection is closed")
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            logger.warning("[DBConnect]: Пул соединений невалиден, пытаюсь пересоздать...")
            if conn and p:
                p.putconn(conn, close=True)
            
            with _pool_lock:
                global _pool
                _pool = None 
            
            p = _get_pool() 
            if p is None: 
                raise DatabaseError("Не удалось пересоздать пул соединений")
            conn = p.getconn()

        conn.autocommit = True
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            
            # Если запрос возвращает данные (SELECT, RETURNING)
            if cursor.description:
                result = cursor.fetchall()
            else:
                result = []
                
            return result

    except Exception as e:
        logger.error("[DBConnect] Database query error: %s", e)
        err_lower = str(e).lower()
        # Если ошибка связана с потерей соединения, сбрасываем пул
        if any(kw in err_lower for kw in ("connection", "timeout", "shutting down", "ssl", "closed")):
            with _pool_lock:
                _pool = None
        raise DatabaseError(f"Ошибка выполнения запроса: {e}") from e
    finally:
        if conn and p:
            try:
                conn.autocommit = False
                p.putconn(conn)
            except:
                pass