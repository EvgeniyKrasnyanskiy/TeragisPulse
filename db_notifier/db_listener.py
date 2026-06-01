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
    """Фоновый поток для отслеживания смены статуса пациентов на 'Идет лечение' (статус 3)."""
    
    def __init__(self, event_queue, on_status_change=None):
        super().__init__()
        self.daemon = True
        self.event_queue = event_queue
        self.on_status_change = on_status_change  # Callback для изменения статуса соединения (bool, str)
        self._stop_event = threading.Event()
        self._creds = get_db_credentials()
        self.STATUS_ON_TREATMENT = 3
        
    def stop(self):
        """Останавливает поток прослушивания."""
        self._stop_event.set()

    def run(self):
        logger.info("[DBListener]: Запуск фонового мониторинга статусов СУБД...")
        reconnect_delay = 5
        
        query_active = """
        SELECT tp.surname, tp.forename, ts.name, tp.patient_id, tc.calendar_id, ts.note
        FROM tcalendar tc
        INNER JOIN tseries ts ON tc.series_id = ts.series_id
        INNER JOIN tpatient tp ON ts.patient_id = tp.patient_id
        WHERE tc.visitdate::date = CURRENT_DATE 
          AND tc.calendar_status_id = %s;
        """
        
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
                
            reconnect_delay = 5  # Сброс задержки
            
            if self.on_status_change:
                self.on_status_change(False, "Подключение...")

            # 2. Подключение к БД и первоначальная загрузка
            try:
                conn = psycopg2.connect(
                    host=host,
                    dbname=self._creds['dbname'],
                    user=self._creds['user'],
                    password=self._creds['password'],
                    port=self._creds['port'],
                    connect_timeout=3
                )
                conn.autocommit = True
                cursor = conn.cursor()
                
                logger.info(f"[DBListener]: Успешно подключено к СУБД {host} для отслеживания смены статусов.")
                if self.on_status_change:
                    self.on_status_change(True, f"Подключено ({host})")

                # Множество для отслеживания уже известных сессий лечения за сегодня
                known_calendar_ids = set()
                
                # Первоначальный сбор уже активных на сегодня пациентов, чтобы не спамить ими при запуске
                try:
                    cursor.execute(query_active, (self.STATUS_ON_TREATMENT,))
                    rows = cursor.fetchall()
                    for r in rows:
                        known_calendar_ids.add(r[4]) # Добавляем calendar_id в известные
                    logger.info(f"[DBListener]: Инициализация завершена. Известных записей на сегодня: {len(known_calendar_ids)}")
                except Exception as init_err:
                    logger.error(f"[DBListener]: Ошибка инициализации списка активных: {init_err}")

                # 3. Цикл периодического опроса
                while not self._stop_event.is_set():
                    try:
                        cursor.execute(query_active, (self.STATUS_ON_TREATMENT,))
                        rows = cursor.fetchall()
                        
                        for row in rows:
                            surname, forename, series_name, patient_id, calendar_id, note = row
                            
                            # Если обнаружили новый сеанс лечения, которого не было в известных
                            if calendar_id not in known_calendar_ids:
                                known_calendar_ids.add(calendar_id)
                                
                                fio = format_fio_short(surname, forename)
                                current_time = time.strftime('%H:%M')
                                
                                event = {
                                    "fio": fio,
                                    "start_date": f"Сегодня в {current_time}"
                                }
                                logger.info(f"[DBListener]: Пациент пошел на лечение: {fio} в {current_time}")
                                # Отправляем событие в очередь GUI
                                self.event_queue.put(event)
                                
                    except (psycopg2.OperationalError, psycopg2.InterfaceError) as query_err:
                        logger.error(f"[DBListener]: Сбой соединения во время запроса: {query_err}")
                        break  # Выходим во внешний цикл для реконнекта
                        
                    # Опрос каждые 3 секунды (оптимально для real-time и без нагрузки на СУБД)
                    time.sleep(3.0)
                    
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
