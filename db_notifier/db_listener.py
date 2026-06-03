import json
import time
import select
import sys
import os
import threading
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import logging

# Добавляем родительскую директорию в sys.path для импорта модулей из основного проекта
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from auto_discovery import discover_db_host, get_db_credentials, log_debug, mask_fio
from identification import identification_func

logger = logging.getLogger(__name__)

class DBListener(threading.Thread):
    """Фоновый поток для прослушивания событий базы данных PostgreSQL."""
    
    def __init__(self, event_queue, on_status_change=None):
        super().__init__()
        self.daemon = True
        self.event_queue = event_queue
        self.on_status_change = on_status_change  # Callback для изменения статуса соединения (bool, str)
        self._stop_event = threading.Event()
        self._creds = get_db_credentials()
        
    def stop(self):
        """Останавливает поток прослушивания."""
        self._stop_event.set()

    def run(self):
        logger.info("[DBListener]: Запуск фонового прослушивания БД...")
        log_debug("Запуск фонового потока DBListener")
        reconnect_delay = 5
        
        while not self._stop_event.is_set():
            if self.on_status_change:
                self.on_status_change(False, "Поиск сервера...")
                
            # 1. Обнаружение сервера БД
            host = discover_db_host()
            if not host:
                logger.error("[DBListener]: Сервер базы данных не найден. Повторная попытка через {}с.".format(reconnect_delay))
                if self.on_status_change:
                    self.on_status_change(False, "Сервер не найден")
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
                continue
                
            reconnect_delay = 5  # Сброс задержки
            
            if self.on_status_change:
                self.on_status_change(False, "Подключение...")

            # 2. Подключение к БД
            try:
                conn = psycopg2.connect(
                    host=host,
                    dbname=self._creds['dbname'],
                    user=self._creds['user'],
                    password=self._creds['password'],
                    port=self._creds['port'],
                    sslmode='prefer'
                )
                conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                cursor = conn.cursor()
                
                # Подписываемся на канал изменений
                cursor.execute("LISTEN series_changes")
                logger.info("[DBListener]: Успешно подключено к СУБД {}. Слушаем канал series_changes...".format(host))
                log_debug("Успешный LISTEN series_changes на хосте {}".format(host))
                
                if self.on_status_change:
                    self.on_status_change(True, "Подключено ({})".format(host))

                # 3. Цикл ожидания событий (надежный опрос сокета, совместимый с Windows)
                while not self._stop_event.is_set():
                    conn.poll()
                    
                    while conn.notifies:
                        notify = conn.notifies.pop(0)
                        try:
                            notif_data = json.loads(notify.payload)
                            log_debug("Получено NOTIFY событие: {}".format(notif_data))
                            self._process_notification(cursor, notif_data)
                        except Exception as parse_err:
                            logger.error("[DBListener]: Ошибка парсинга события: {}".format(parse_err))
                            log_debug("Ошибка обработки NOTIFY: {}".format(parse_err))
                            
                    # Пауза в 2 секунды (точно так же как в teragis_pulse.py!)
                    time.sleep(2.0)
                            
            except Exception as conn_err:
                logger.error("[DBListener]: Ошибка соединения с БД: {}. Реконнект через {}с.".format(conn_err, reconnect_delay))
                log_debug("Сбой соединения или LISTEN: {}".format(conn_err))
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
        note = data.get('note', '')
        totaldose = data.get('totaldose', 0)
        fractionsnumber = data.get('fractionsnumber', 1)
        
        # Получаем данные о докторе, физике и укладке с помощью вашей оригинальной функции
        try:
            doc, phys, lay, office = identification_func(note)
        except Exception as ident_err:
            log_debug("Ошибка парсинга персонала через identification_func: {}".format(ident_err))
            doc, phys, lay, office = '🧑‍⚕ —', '☢ —', '—', '0'

        # Сборка подробной информации
        total_d = float(totaldose or 0)
        frac_n = int(fractionsnumber or 1)
        dose_step = round((total_d / frac_n), 2)
        
        # Получаем дату первого визита из tcalendar для "лечение с dd.mm.yyyy"
        start_date_str = None
        if series_id:
            try:
                cursor.execute(
                    "SELECT MIN(visitdate) FROM tcalendar WHERE series_id = %s",
                    (series_id,)
                )
                res = cursor.fetchone()
                if res and res[0]:
                    visitdate = res[0]
                    start_date_str = visitdate.strftime('%d.%m.%Y')
            except Exception as db_err:
                logger.error("[DBListener]: Ошибка запроса даты старта: {}".format(db_err))
                log_debug("Ошибка запроса даты из tcalendar: {}".format(db_err))
                
        if not start_date_str:
            start_date_str = time.strftime('%d.%m.%Y')
            
        fio = "{} {}".format(surname, forename).strip().upper()
        if not fio:
            fio = "НЕИЗВЕСТНЫЙ ПАЦИЕНТ"
            
        # Формируем строку деталей в точности как в Telegram-боте
        details_list = [
            "📊 {}Гр x {}фр = {}Гр".format(dose_step, frac_n, total_d),
            "{}".format(doc),
            "{}".format(phys)
        ]
        
        if lay and lay != '—':
            details_list.append("📝 {}".format(lay))
        details_list.append("📅 Пациент лечится с: {}".format(start_date_str))
            
        details = "\n".join(details_list)

        event = {
            "fio": fio,
            "details": details
        }
        
        log_debug("Сформировано GUI событие: FIO={}, Details={}".format(mask_fio(fio), details_list))
        self.event_queue.put(event)


