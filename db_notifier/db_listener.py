# -*- coding: utf-8 -*-
import json
import time
import select
import threading
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import logging

from auto_discovery import discover_db_host, get_db_credentials

logger = logging.getLogger(__name__)

def format_fio_short(surname, forename):
    """Превращает Фамилию и Имя/Отчество в формат 'Иванов И.И.'"""
    surname = str(surname).strip().upper()
    forename = str(forename).strip()
    
    if not surname:
        return "Неизвестный Пациент"
        
    parts = forename.split()
    initials = ""
    if len(parts) >= 1 and parts[0]:
        initials += f" {parts[0][0].upper()}."
    if len(parts) >= 2 and parts[1]:
        initials += f"{parts[1][0].upper()}."
        
    return f"{surname}{initials}"

class DBListener(threading.Thread):
    """Фоновый поток для прослушивания событий базы данных."""
    
    def __init__(self, event_queue, on_status_change=None):
        super().__init__()
        self.daemon = True
        self.event_queue = event_queue
        self.on_status_change = on_status_change  # Callback для изменения статуса соединения (bool)
        self._stop_event = threading.Event()
        self._creds = get_db_credentials()
        
    def stop(self):
        """Останавливает поток прослушивания."""
        self._stop_event.set()

    def run(self):
        logger.info("[DBListener]: Запуск фонового прослушивания БД...")
        reconnect_delay = 5
        
        while not self._stop_event.is_set():
            if self.on_status_change:
                self.on_status_change(False, "Поиск сервера...")
                
            # 1. Обнаружение сервера БД
            host = discover_db_host()
            if not host:
                logger.error(f"[DBListener]: Сервер базы данных не найден. Повторная попытка через {reconnect_delay}с.")
                if self.on_status_change:
                    self.on_status_change(False, "Сервер не найден")
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
                continue
                
            reconnect_delay = 5  # Сброс задержки при успешном обнаружении
            
            if self.on_status_change:
                self.on_status_change(False, "Подключение...")

            # 2. Подключение к БД
            try:
                conn = psycopg2.connect(
                    host=host,
                    dbname=self._creds['dbname'],
                    user=self._creds['user'],
                    password=self._creds['password'],
                    port=self._creds['port']
                )
                conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                cursor = conn.cursor()
                
                # Подписываемся на канал изменений
                cursor.execute("LISTEN series_changes")
                logger.info(f"[DBListener]: Успешно подключено к СУБД {host}. Слушаем канал series_changes...")
                
                if self.on_status_change:
                    self.on_status_change(True, f"Подключено ({host})")

                # 3. Цикл ожидания событий
                while not self._stop_event.is_set():
                    # Проверяем, есть ли данные в сокете (таймаут 1 секунда)
                    if select.select([conn], [], [], 1.0) == ([], [], []):
                        continue
                        
                    conn.poll()
                    
                    while conn.notifies:
                        notify = conn.notifies.pop(0)
                        try:
                            notif_data = json.loads(notify.payload)
                            self._process_notification(cursor, notif_data)
                        except Exception as parse_err:
                            logger.error(f"[DBListener]: Ошибка парсинга события: {parse_err}")
                            
            except Exception as conn_err:
                logger.error(f"[DBListener]: Ошибка соединения с БД: {conn_err}. Реконнект через {reconnect_delay}с.")
                if self.on_status_change:
                    self.on_status_change(False, "Сбой связи")
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
                
        logger.info("[DBListener]: Фоновый поток прослушивания остановлен.")

    def _process_notification(self, cursor, data):
        """Обрабатывает JSON событие и дозапрашивает необходимые поля."""
        surname = data.get('surname', '')
        forename = data.get('forename', '')
        series_id = data.get('series_id')
        
        fio = format_fio_short(surname, forename)
        
        start_date_str = "dd.mm"
        
        # Делаем запрос к tcalendar для получения даты первого визита (старта лечения)
        if series_id:
            try:
                cursor.execute(
                    "SELECT MIN(visitdate) FROM tcalendar WHERE series_id = %s",
                    (series_id,)
                )
                res = cursor.fetchone()
                if res and res[0]:
                    visitdate = res[0] # Это объект datetime.date
                    start_date_str = visitdate.strftime('%d.%m')
            except Exception as db_err:
                logger.error(f"[DBListener]: Ошибка запроса даты старта для series_id={series_id}: {db_err}")
                
        # Если дата старта так и не найдена, ставим сегодняшнюю
        if start_date_str == "dd.mm":
            start_date_str = time.strftime('%d.%m')

        event = {
            "fio": fio,
            "start_date": start_date_str
        }
        
        logger.info(f"[DBListener]: Обнаружен пациент: {fio}, старт: {start_date_str}")
        # Помещаем событие в потокобезопасную очередь
        self.event_queue.put(event)
