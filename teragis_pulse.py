# TeragisPulse.py
import tkinter as tk
from tkinter import messagebox
import customtkinter
from database import execute_query, logger, close_db_pool, DatabaseError
from datetime import datetime, date, timedelta
import configparser
import gc
from collections import Counter
import winsound 
import threading
import os
import sys
import psutil
import tempfile
import time
import logging
from utils import get_formatted_date, get_time_with_date, format_name_short
from reports import generate_daily_report
from dotenv import load_dotenv
import pyperclip
from translit_manager import TranslitManager

# Загрузка переменных окружения
load_dotenv()

# Глобальная переменная для удержания файла блокировки
_lock_f = None

def ensure_single_instance():
    # Создаем имя файла блокировки во временной директории ОС
    lock_file = os.path.join(tempfile.gettempdir(), "teragis_pulse.lock")
    
    try:
        # Пытаемся открыть файл. Если он есть — пробуем его удалить (проверка на "зависание")
        if os.path.exists(lock_file):
            os.remove(lock_file)
    except OSError:
        # Если удалить не удалось — значит, файл открыт другой копией программы
        print("Система: TeragisPulse уже запущен. Выход...")
        sys.exit(0)

    # Открываем файл и держим его открытым до конца работы скрипта
    # В Windows это заблокирует файл для удаления другими процессами
    global _lock_f 
    _lock_f = open(lock_file, "w")
    _lock_f.write(str(os.getpid()))
    # Не закрываем файл! Он закроется сам при выходе из программы.

# Блокируем повторный запуск программы
ensure_single_instance()

# Подавляем болтливость Telethon
logging.getLogger('telethon').setLevel(logging.WARNING)
# Если используете специфические расширения для прокси, их тоже можно приглушить:
logging.getLogger('telethon.network.mtprotosender').setLevel(logging.ERROR)

# БЛОК УМНОГО ИМПОРТА
config_init = configparser.ConfigParser()
config_init.read('config.ini', encoding='utf-8-sig')

USE_WS = config_init.getboolean('telegram', 'use_ws', fallback=False)

if USE_WS:
    from tg_bot_ws import TelegramBot
else:
    from tg_bot_xr import TelegramBot

from report_manager import ReportManager
from proxy_manager import ProxyManager
from alarm_manager import AlarmManager
# ---------------------------

customtkinter.set_appearance_mode("Dark")
customtkinter.set_default_color_theme("blue")

class App(customtkinter.CTk):
    
    def _load_config_constants(self):
        """Загружает константы из config.ini"""
        # СТАТУСЫ БД (tcalendar_status)
        self.STATUS_PLANNED_ID = self.cfg.getint('db_status', 'calendar_planned', fallback=1)
        self.STATUS_ON_TREATMENT_ID = self.cfg.getint('db_status', 'calendar_on_treatment', fallback=3)
        self.STATUS_COMPLETED_ID = self.cfg.getint('db_status', 'calendar_completed', fallback=4)
        
        # СТАТУСЫ СЕРИЙ (tseries_status - с картинки)
        self.SERIES_ON_TREATMENT = self.cfg.getint('db_status', 'series_on_treatment', fallback=1)
        self.SERIES_NOT_STARTED = self.cfg.getint('db_status', 'series_not_started', fallback=2)
        self.SERIES_ON_BREAK = self.cfg.getint('db_status', 'series_on_break', fallback=3)
        self.SERIES_SUSPENDED = self.cfg.getint('db_status', 'series_suspended', fallback=4)
        self.SERIES_STOPPED = self.cfg.getint('db_status', 'series_stopped', fallback=5)
        self.SERIES_COMPLETED = self.cfg.getint('db_status', 'series_completed', fallback=6)

        # ЛОГИКА
        self.HISTORY_DAYS = self.cfg.getint('logic', 'history_days', fallback=10)
        self.FUTURE_DAYS = self.cfg.getint('logic', 'future_days', fallback=10)
        self.TOTAL_DISPLAY_ROWS = self.cfg.getint('logic', 'total_display_rows', fallback=21)
        self.LOW_PATIENTS_THRESHOLD = self.cfg.getint('logic', 'low_patients_threshold', fallback=1)
        self.AVG_TIME_PER_PATIENT = self.cfg.getint('logic', 'avg_time_per_patient', fallback=7)
        
        # ЦВЕТА
        self.COLOR_DATE_DEFAULT = self.cfg.get('colors', 'date_default', fallback="#555")
        self.COLOR_DATE_TODAY = self.cfg.get('colors', 'date_today', fallback="#FFFF00")
        self.COLOR_DATE_FUTURE = self.cfg.get('colors', 'date_future', fallback="#4A90E2")
        self.COLOR_DATE_DUPLICATE = self.cfg.get('colors', 'date_duplicate', fallback="#FF0000")
        self.COLOR_DUPLICATE_PATIENT = self.cfg.get('colors', 'duplicate_patient', fallback="#FF6B6B")
        self.COLOR_TODAY_BORDER = self.cfg.get('colors', 'today_border', fallback="#990000")
        self.COLOR_NEW_PATIENT = self.cfg.get('colors', 'new_patient', fallback="#A5D6A7")
        self.COLOR_NEW_PATIENT_REPEAT = self.cfg.get('colors', 'new_patient_repeat', fallback="#4CAF50")
        self.COLOR_LAST_FRACTION = self.cfg.get('colors', 'last_fraction', fallback="#FFCC80")
        self.COLOR_LAST_FRACTION_REPEAT = self.cfg.get('colors', 'last_fraction_repeat', fallback="#FF9800")
        self.COLOR_WEEKEND_ZERO = self.cfg.get('colors', 'weekend_zero', fallback="#777777")
        self.COLOR_WEEKEND_ZERO_TEXT = self.cfg.get('colors', 'weekend_zero_text', fallback="#3A3A3A")
        
        self.FIXED_CONTROLS_HEIGHT = 115 
    
    def debug_print_columns(self):
        """Ищет колонки, содержащие время, в связанных таблицах."""
        tables = ['tcalendar', 'tseries', 'tcalendar_status']
        print("\n=== ПОИСК КОЛОНОК ВРЕМЕНИ ===")
        
        for table in tables:
            query = f"""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = '{table}'
            AND (data_type LIKE '%%timestamp%%' OR column_name LIKE '%%time%%');
            """
            try:
                data = execute_query(query, ())
            except DatabaseError:
                continue
            print(f"\nТаблица [{table}]:")
            if data:
                for row in data:
                    print(f"  - {row[0]} ({row[1]})")
            else:
                print("  Временные колонки не найдены.")
        print("\n==============================\n")

    def __init__(self):
        super().__init__()
        
        # 0. Удаляем файлы сессии Telethon (основной и временные)
        for f in ["bot_session.session", "bot_session.session-journal", "bot_session.session-wal"]:
            if os.path.exists(f):
                try: os.remove(f)
                except: pass

        # 0.5. Базовые переменные состояния (должны быть созданы ПЕРВЫМИ)
        self.sounds_enabled = False
        self.last_notified_date = None 
        self.low_patients_notified = False
        
        self._bot_first_run = True
        self.tg_bot = None
        
        self.last_system_tick = datetime.now()
        self.idle_start_time = datetime.now()
        
        # Внутренние технические переменные
        self.current_data = []
        self.padding = 10
        self.gc_counter = 0
        self.geometry_set = False
        self.duplicate_dates = {}
        self.last_duplicate_count = -1
        
        # Менеджеры (прокси и будильник) создаются позже, после создания виджетов

        # 1. Загрузка конфигурации
        self.cfg = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.ini')
        self.cfg.read(config_path, encoding='utf-8-sig')

        # Загружаем константы из конфига
        self._load_config_constants()

        # Настройки окна
        try:
            self.initial_w = self.cfg.getint('window', 'width', fallback=1780)
            self.initial_h = self.cfg.getint('window', 'height', fallback=660)
            self.geometry(f"{self.initial_w}x{self.initial_h}")
            self.title(self.cfg.get('window', 'title', fallback='TeragisPulse'))
            self.resizable(False, False)
        except Exception:
            self.initial_w = 1780
            self.initial_h = 660
            self.geometry(f"{self.initial_w}x{self.initial_h}")
            self.title("TeragisPulse")
            self.resizable(False, False)

        # 2. Загрузка визуальных параметров (цвета, шрифты)
        self.colors = self.load_colors()
        self.status_colors_cfg = {
            'Completed': self.cfg.get('status_colors', 'Completed', fallback='#ff8e8a'),
            'On_Treatment': self.cfg.get('status_colors', 'On_Treatment', fallback='green')
        }
        
        # Параметры размеров таблиц
        self.col_width = self.cfg.getint('table_main', 'col_width', fallback=135)
        self.row_height = self.cfg.getint('table_main', 'row_height', fallback=40)
        self.font_header_size = self.cfg.getint('table_main', 'header_font_size', fallback=13)
        self.font_cell_size = self.cfg.getint('table_main', 'cell_font_size', fallback=18)

        self.client_col_width = self.cfg.getint('table_clients', 'col_width', fallback=140)
        self.client_row_height = self.cfg.getint('table_clients', 'row_height', fallback=30)
        self.client_header_font_size = self.cfg.getint('table_clients', 'header_font_size', fallback=12)
        self.client_cell_font_size = self.cfg.getint('table_clients', 'cell_font_size', fallback=14)
        self.limit_fio = self.cfg.getint('table_clients', 'limit_fio', fallback=20)
        self.limit_comment = self.cfg.getint('table_clients', 'limit_comment', fallback=40)
        
        # 3. Построение GUI (Сначала создаем всё визуальное)
        # В старой версии тут было только табло
        self.on_treatment_frame = customtkinter.CTkFrame(self)
        self.on_treatment_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 5))

        # ТАБЛО (Слева)
        self.on_treatment_label = customtkinter.CTkLabel(
            self.on_treatment_frame, 
            text="Пациенты в лечении: Загрузка...",
            font=("Helvetica", 16),
            anchor="w",
            text_color="white"
        )
        self.on_treatment_label.pack(side=tk.LEFT, padx=10, pady=5)

        # Сначала ВСЕ RIGHT элементы (в верхней панели!)
        self.log_btn = customtkinter.CTkButton(
            self.on_treatment_frame, # ИСПРАВЛЕНО: привязываем к верхней панели
            text="Логи",
            width=70,
            command=self._open_log_file,
            fg_color="#444",
            hover_color="#666"
        )
        self.log_btn.pack(side=tk.RIGHT, padx=(0, 10))

        self.log_label = customtkinter.CTkLabel(
            self.on_treatment_frame, # ИСПРАВЛЕНО: привязываем к верхней панели
            text="",
            font=("Courier", 11),
            anchor="e",
            text_color="white",
            wraplength=0
        )
        self.log_label.pack(side=tk.RIGHT, padx=10, fill=tk.X, expand=True)

        # Теперь создаем нижнюю панель настроек
        self.create_settings_frame()
        self.create_canvas()
        
        # Вспомогательная отладка
        if hasattr(self, 'debug_print_columns'):
            self.debug_print_columns()

        # 4. Запуск фоновых задач и защиты
        # Запуск Watchdog (Защита от перевода времени)
        def watchdog():
            last_t = time.time()
            while True:
                time.sleep(1)
                curr_t = time.time()
                delta = curr_t - last_t - 1
                if abs(delta) > 10:
                    logger.error(f"[TPulse] Watchdog: Обнаружен скачок времени ({delta:.1f} сек), программа будет закрыта")
                    messagebox.showerror("Критическая ошибка", 
                                       f"Обнаружен скачок времени ({delta:.1f} сек),\nПрограмма будет закрыта")
                    os._exit(0)
                last_t = curr_t
        
        threading.Thread(target=watchdog, daemon=True).start()

        # Инициализация менеджеров
        self.proxy_manager = ProxyManager(USE_WS)
        self.proxy_manager.start()
        
        self.alarm_manager = AlarmManager(self.clock_label, self.after)
        
        # Инициализация фоновой транслитерации
        self.translit_manager = TranslitManager()
        self.after(3000, self.translit_manager.start) # Запуск через 3 сек после старта
        
        # Передаем ID статусов в менеджер отчетов
        status_ids = {
            'COMPLETED': self.STATUS_COMPLETED_ID,
            'PLANNED': self.STATUS_PLANNED_ID
        }
        self.report_manager = ReportManager(self.after, status_ids)

        # TELEGRAM БОТ (Инициализируем ДО запуска циклов обновления)
        self.tg_bot = TelegramBot(
            config=self.cfg,
            list_callback=self._get_today_list_for_bot,
            status_callback=self._on_bot_status_changed,
            on_treatment_callback=self._get_on_treatment_for_bot
        )

        # Запуск циклов обновления через небольшую задержку, когда окно уже готово
        self.after(100, self.update_data)                
        self.update_on_treatment_label_loop() # Сразу запускаем первый круг обновления
        self.after(200, self.alarm_manager.tick)
        self.after(500, self.report_manager.schedule_reports)

        # 1. Запускаем "движок" бота (создает поток)
        if self.tg_bot:
            self.after(1000, self.tg_bot.start)  
            
            # 2. Через 2 секунды (когда прокси и поток точно готовы) переключаем в ВКЛ
            self.after(2000, self._auto_enable_bot)

        # self._schedule_auto_reports() # Устарело, теперь управляется ReportManager
        
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.after(300, self.iconify)
        
        # Запускаем фоновое прослушивание базы для отправки уведомлений в группы 1РО и 2РО
        self.start_db_listener()

    def _auto_enable_bot(self):
        """Принудительно включает рассылку при старте программы."""
        if hasattr(self, 'tg_bot') and not self.tg_bot._enabled:
            self.tg_bot.toggle() # Это само вызовет смену цвета кнопки и индикатора

    def _toggle_translit(self):
        """Переключает состояние фоновой транслитерации."""
        if not hasattr(self, 'translit_manager'):
            return
            
        if self.translit_manager.enabled:
            self.translit_manager.stop()
            self.translit_toggle_btn.configure(text="▶", fg_color="#444", hover_color="#666")
            self.translit_status_label.configure(text="⚪ Tr ", text_color="#AAAAAA")
        else:
            self.translit_manager.start()
            self.translit_toggle_btn.configure(text="⏸", fg_color="#1f538d", hover_color="#14375e")
            self.translit_status_label.configure(text="🔡 Tr ", text_color="#4CAF50")

    # НОВЫЙ БЛОК ДЛЯ НОВЫХ СЕРИЙ
    
    def start_db_listener(self):
        """Запускает поток прослушивания уведомлений от PostgreSQL"""
        listener_thread = threading.Thread(target=self._db_listener_worker, daemon=True)
        listener_thread.start()

    def _db_listener_worker(self):
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        import json
        import time
        from identification import identification_func

        # Параметры подключения из .env с фолбеком на старый конфиг
        conn_params = {
            "user": os.getenv('DB_USER', config_init.get('db', 'db_user', fallback='')),
            "password": os.getenv('DB_PASS', config_init.get('db', 'db_password', fallback='')),
            "dbname": os.getenv('DB_NAME', config_init.get('db', 'database', fallback='')),
            "host": os.getenv('DB_HOST', config_init.get('db', 'db_host', fallback='')),
            "port": os.getenv('DB_PORT', config_init.get('db', 'db_port', fallback=''))
        }

        while True:
            try:
                conn = psycopg2.connect(**conn_params)
                conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                cursor = conn.cursor()
                cursor.execute("LISTEN series_changes")
                # logger.info("[TPulse] БД-Слушатель: Канал поиска новых планов активен")

                while True:
                    conn.poll()
                    while conn.notifies:
                        notify = conn.notifies.pop(0)
                        notif_data = json.loads(notify.payload)
                        
                        # Твоя старая логика формирования текста
                        try:
                            doc, phys, lay, off = identification_func(notif_data.get('note', ''))
                        except:
                            doc, phys, lay, off = '🧑‍⚕ —', '☢ —', '—', '0'

                        # Сборка сообщения
                        dose_val = notif_data.get('totaldose', 0)
                        frac_val = notif_data.get('fractionsnumber', 1)
                        dose_step = round((dose_val / frac_val), 2)
                        
                        msg = (f"👤 <b>{notif_data.get('surname', '')} {notif_data.get('forename', '')}</b>\n\n"
                               f"📊 {dose_step}Гр x {frac_val}фр = {dose_val}Гр\n"
                               f"{doc}\n{phys}")
                        
                        if lay != '—':
                            msg += f"\n\n📝 {lay}"

                        # ОТПРАВКА через наш WebSocket бот
                        if USE_WS and hasattr(self, 'tg_bot'):
                            self.tg_bot.send_notification_from_db(msg, off, notif_data.get('name', ''))
                    
                    time.sleep(2) # Пауза опроса очереди уведомлений
            except Exception as e:
                logger.error(f"[TPulse] Ошибка БД-Слушателя: {e}. Реконнект через 10 сек")
                time.sleep(10)
                
    # КОНЕЦ БЛОКА НОВЫХ СЕРИЙ

    def load_colors(self):
        """Загружает цветовую схему из конфигурации."""
        defaults = {
            'Planned_1': "#E0FFFF", 'Completed_1': "#AFEEEE", 'Total_1': "#7FFFD4",
            'Planned_2': "#ccff99", 'Completed_2': "#b3ff66", 'Total_2': "#99ff33",
            'Planned_Other': "#FFFACD", 'Completed_Other': "#EEE8AA", 'Total_Other': "#FFD700",
            'Planned_All': "#E6E6FA", 'Completed_All': "#D8BFD8", 'Total_All': "#DDA0DD",
            'Default': "#ffffff"
        }
        colors = {}
        if 'colors' in self.cfg:
            for key in defaults:
                colors[key] = self.cfg.get('colors', key, fallback=defaults[key])
        else:
            colors = defaults
        return colors

    # ПАНЕЛЬ НАСТРОЕК
    def create_settings_frame(self):
        settings_frame = customtkinter.CTkFrame(self)
        settings_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 0))

        # Место для вставки поля логов и кнопки логов

        # Потом ВСЕ LEFT элементы
        customtkinter.CTkLabel(settings_frame, text="Ширина:").pack(side=tk.LEFT, padx=5)
        self.width_slider = customtkinter.CTkSlider(settings_frame, from_=80, to=200, command=self.update_col_width)
        self.width_slider.set(self.col_width)
        self.width_slider.pack(side=tk.LEFT, padx=5, pady=10)

        customtkinter.CTkLabel(settings_frame, text="Высота:").pack(side=tk.LEFT, padx=5)
        self.height_slider = customtkinter.CTkSlider(settings_frame, from_=30, to=60, command=self.update_row_height)
        self.height_slider.set(self.row_height)
        self.height_slider.pack(side=tk.LEFT, padx=5, pady=10)

        self.sound_switch = customtkinter.CTkSwitch(
            settings_frame,
            text="Звук",
            command=self.toggle_sounds,
            font=("Arial", 12)
        )
        self.sound_switch.deselect()
        self.sound_switch.pack(side=tk.LEFT, padx=20)

        # ЧАСЫ / БУДИЛЬНИК
        self.clock_label = customtkinter.CTkLabel(
            settings_frame,
            text="00:00",
            font=("Courier", 15, "bold"),
            text_color="#4CAF50",  # зелёный = часы
            cursor="hand2"
        )
        self.clock_label.pack(side=tk.LEFT, padx=(0, 10))
        self.clock_label.bind("<Button-1>", lambda e: self.alarm_manager.open_dialog(self))

        # ИНДИКАТОР И КНОПКА TELEGRAM-БОТА
        self.bot_status_label = customtkinter.CTkLabel(
            settings_frame,
            text="🔴 Bot  ",
            font=("Arial", 12),
            text_color="#FF4444",
        )
        self.bot_status_label.pack(side=tk.LEFT, padx=(10, 2))

        self.bot_toggle_btn = customtkinter.CTkButton(
            settings_frame,
            text="▶",
            width=36,
            fg_color="#444",
            hover_color="#666",
            command=self._toggle_bot
        )
        self.bot_toggle_btn.pack(side=tk.LEFT, padx=(0, 10))

        # ИНДИКАТОР И КНОПКА ТРАНСЛИТЕРАЦИИ (Shift+ЛКМ)
        self.translit_status_label = customtkinter.CTkLabel(
            settings_frame,
            text="🔡 Tr ",
            font=("Arial", 12),
            text_color="#4CAF50",
        )
        self.translit_status_label.pack(side=tk.LEFT, padx=(10, 2))

        self.translit_toggle_btn = customtkinter.CTkButton(
            settings_frame,
            text="⏸",
            width=36,
            fg_color="#1f538d", # Темно-синий для активного состояния
            hover_color="#14375e",
            command=self._toggle_translit
        )
        self.translit_toggle_btn.pack(side=tk.LEFT, padx=(0, 10))

        # ИНДИКАТОР СТАТУСА БАЗЫ ДАННЫХ
        self.db_status_label = customtkinter.CTkLabel(
            settings_frame,
            text="DB: OK",
            font=("Arial", 12),
            text_color="#4CAF50"
        )
        self.db_status_label.pack(side=tk.LEFT, padx=(10, 10))

        # ПРЕДПОЛАГАЕМОЕ ВРЕМЯ ЗАВЕРШЕНИЯ (Задача 13)
        self.shift_end_label = customtkinter.CTkLabel(
            settings_frame, text="🏁 Конец в: расчет...",
            font=("Arial", 13, "bold"),
            text_color="#FFD700"
        )
        self.shift_end_label.pack(side=tk.LEFT, padx=(10, 5))

        self._attach_log_handler()

    def _set_db_status(self, ok: bool) -> None:
        """Обновляет индикатор статуса БД в GUI."""
        if ok:
            self.db_status_label.configure(text="DB: OK", text_color="#4CAF50")
        else:
            self.db_status_label.configure(text="DB: ERR", text_color="#FF4444")


    def _attach_log_handler(self):
        """Подключает GUI-handler к logger для отображения последней записи."""
        app_ref = self

        class GuiLogHandler(logging.Handler):
            def emit(self, record):
                msg = self.format(record)
                level = record.levelno
                color = "#FF4444" if level >= logging.ERROR else (
                        "#FFA500" if level >= logging.WARNING else "white")
                # Безопасное обновление из любого потока
                try:
                    app_ref.after(0, lambda m=msg, c=color: app_ref._update_log_label(m, c))
                except Exception:
                    pass

        handler = GuiLogHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%H:%M:%S'))
        handler.setLevel(logging.INFO)
        # Добавляем к тому же logger, что импортирован из DBConnect
        logger.addHandler(handler)

    def toggle_sounds(self):
        """Метод вызывается при переключении свитча."""
        # Получаем состояние напрямую из виджета и сохраняем в переменную
        self.sounds_enabled = self.sound_switch.get() == 1
        status = "включены" if self.sounds_enabled else "выключены"
        # logger.info(f"[TPulse] Звуковые оповещения {status}")
        if self.sounds_enabled:
            winsound.Beep(1000, 200)

    # def _update_log_label(self, msg, color):
        # """Обновляет лейбл последнего лога (вызывается только из главного потока)."""
        # try:
            # self.log_label.configure(text=msg, text_color=color)
        # except Exception:
            # pass
            
    def _update_log_label(self, msg, color):
        """Обновляет лейбл последнего лога с ограничением длины для шапки."""
        try:
            # Ограничиваем длину сообщения, чтобы оно не перекрывало фамилию
            # 80-90 символов — оптимально для большинства мониторов
            max_chars = 133
            if len(msg) > max_chars:
                # Берем конец сообщения, так как там самая свежая инфо
                display_msg = "..." + msg[-(max_chars - 3):]
            else:
                display_msg = msg

            if hasattr(self, 'log_label') and self.log_label:
                self.log_label.configure(text=display_msg, text_color=color)
        except Exception:
            pass

    def _open_log_file(self):
        """Открывает файл лога в стандартном редакторе Windows."""
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'teragispulse.log')
        if os.path.exists(log_path):
            os.startfile(log_path)
        else:
            logger.warning(f"[TPulse] Файл лога не найден: {log_path}")

    # TELEGRAM БОТ 
    def _toggle_bot(self):
        """Запуск/остановка бота по кнопке."""
        self.tg_bot.toggle()

    def _on_bot_status_changed(self, state):
        """Вызывается из потока бота — обновляем индикатор через after().
        state: "connected" | "reconnecting" | "stopped"  (или bool для обратной совместимости)
        """
        try:
            # Обратная совместимость с bool
            if isinstance(state, bool):
                state = "connected" if state else "stopped"
            self.after(0, lambda s=state: self._update_bot_indicator(s))
        except Exception:
            pass

    def _update_bot_indicator(self, state):
        """Обновляет лейбл и кнопку бота в GUI (только из главного потока).
        state: "connected" | "reconnecting" | "stopped"
        """
        try:
            if state == "connected":
                self.bot_status_label.configure(text="🟢 Bot  ", text_color="#4CAF50")
                self.bot_toggle_btn.configure(
                    text="⏹", fg_color="#8B0000", hover_color="#B22222", state="normal"
                )
            elif state == "reconnecting":
                self.bot_status_label.configure(text="🟡 Bot  ", text_color="#FFA500")
                self.bot_toggle_btn.configure(
                    text="⟳", fg_color="#555", hover_color="#555", state="disabled"
                )
            else:  # stopped
                self.bot_status_label.configure(text="🔴 Bot  ", text_color="#FF4444")
                self.bot_toggle_btn.configure(
                    text="▶", fg_color="#444", hover_color="#666", state="normal"
                )
            
            # ПРИНУДИТЕЛЬНО ПЕРЕРИСОВАТЬ (особенно важно для CustomTkinter)
            self.update_idletasks() 
        except Exception:
            pass

    def _get_on_treatment_for_bot(self) -> str:
        """
        Просто отдает последнюю готовую строку для бота.
        Если её нет (самый-самый старт), пробует вернуть 'свободно'.
        """
        return getattr(self, 'last_bot_status', 'свободно')

    def _get_today_list_for_bot(self, target_date_str: str = None) -> dict:
        """
        Универсальная версия: возвращает список на сегодня или на указанную дату (dd.mm).
        """
        try:
            # 1. Определяем целевую дату
            if target_date_str:
                # Превращаем "17.04" в "2026-04-17"
                current_year = datetime.now().year
                try:
                    target_dt = datetime.strptime(f"{target_date_str}.{current_year}", "%d.%m.%Y")
                except ValueError:
                    return {'list_text': "❌ Неверный формат даты. Используйте ДД.ММ", 'last_event': "Ошибка ввода"}
            else:
                target_dt = datetime.now()

            target_sql_str = target_dt.strftime('%Y-%m-%d')
            window_date = target_dt.date()

            # 2. Основной запрос (visitdate теперь зависит от target_sql_str)
            query = '''
                SELECT tu.surname, tu.forename, ts.name, tcs.calendar_status_id,
                       tcs.name, ts.note, tu.patient_id, tc.series_id
                FROM tcalendar tc
                INNER JOIN tseries ts ON tc.series_id = ts.series_id
                INNER JOIN tpatient tu ON ts.patient_id = tu.patient_id
                INNER JOIN tcalendar_status tcs ON tc.calendar_status_id = tcs.calendar_status_id
                WHERE tc.visitdate = %s
                  AND tc.calendar_status_id = %s
                ORDER BY tu.surname, tu.forename;
            '''
            try:
                data = execute_query(query, (target_sql_str, self.STATUS_PLANNED_ID))
            except DatabaseError:
                return {'list_text': "⚠️ Ошибка подключения к базе данных", 'last_event': "Проверьте сеть"}

            
            new_p_count, end_p_count = 0, 0
            lines = []
            total_p = len(data) if isinstance(data, list) else 0

            if total_p > 0:
                series_ids = list({int(row[7]) for row in data})
                patient_ids = list({int(row[6]) for row in data})
                
                placeholders_s = ','.join(['%s'] * len(series_ids))
                placeholders_p = ','.join(['%s'] * len(patient_ids))

                # Доп. запросы тоже должны учитывать контекст серий, активных на ту дату
                # Вариант с CAST — самый надежный, работает во всех версиях Postgres и не путает Python
                min_date_raw = execute_query(
                    f"SELECT series_id, CAST(MIN(visitdate) AS DATE) FROM tcalendar WHERE series_id IN ({placeholders_s}) GROUP BY series_id", 
                    tuple(series_ids)
                )

                last_ins_raw = execute_query(
                    f"SELECT series_id, CAST(MAX(visitdate) AS DATE) FROM tcalendar WHERE series_id IN ({placeholders_s}) AND calendar_status_id = %s GROUP BY series_id", 
                    tuple(series_ids) + (self.STATUS_PLANNED_ID,)
                )
                first_ever_raw = execute_query(f"SELECT patient_id, MIN(series_id) FROM tseries WHERE patient_id IN ({placeholders_p}) GROUP BY patient_id", tuple(patient_ids))
                
                first_series_map = {int(r[0]): int(r[1]) for r in first_ever_raw} if isinstance(first_ever_raw, list) else {}
                first_dates = {int(r[0]): (r[1].date() if isinstance(r[1], datetime) else r[1]) for r in min_date_raw} if isinstance(min_date_raw, list) else {}
                last_dates = {int(r[0]): (r[1].date() if isinstance(r[1], datetime) else r[1]) for r in last_ins_raw} if isinstance(last_ins_raw, list) else {}

                for i, row in enumerate(data, 1):
                    surname, forename, series_name, _, _, note, pid, sid = row
                    
                    f_parts = forename.strip().split()
                    inits = "".join([p[0].upper() + "." for p in f_parts if p])
                    display_name = f"{surname.upper()} {inits}"

                    emoji = ""
                    # Сравниваем даты начала/конца именно с window_date (целевой датой)
                    is_start = (first_dates.get(sid) == window_date)
                    is_end = (last_dates.get(sid) == window_date)
                    
                    if is_end:
                        emoji = "🏁"
                        end_p_count += 1
                    elif is_start:
                        new_p_count += 1
                        emoji = "🆕②" if sid > first_series_map.get(pid, sid) else "🆕"
                    
                    limit = 4 if str(series_name).startswith('2') else 3
                    doc_parts = (note or "").strip().split()
                    doc_display = f"({doc_parts[0].capitalize()[:limit]})" if doc_parts else "(-)"
                    
                    lines.append(f"{i}. {emoji}{display_name} {doc_display}")
                
                body = "\n".join(lines) + "\n"
            else:
                body = "Пациентов нет\n"

            # 3. Заголовок и Футер (используем target_dt)
            header = f"📝 Список на {get_formatted_date(target_dt)}:\n"
            
            total_today_data = execute_query("SELECT COUNT(DISTINCT ts.patient_id) FROM tcalendar tc JOIN tseries ts ON tc.series_id = ts.series_id WHERE tc.visitdate = %s", (target_sql_str,))
            total_today = total_today_data[0][0] if isinstance(total_today_data, list) and total_today_data else 0

            footer = f"Всего: {total_p} / +{new_p_count} / -{end_p_count} из {total_today}"
            
            return {
                'list_text': header + body + footer,
                'last_event': getattr(self, 'last_event_text', '') if not target_date_str else "Запрос списка"
            }

        except Exception as e:
            logger.error(f"[TPulse] Критическая ошибка формирования списка: {e}")
            return {'list_text': f"⚠️ Ошибка: {e}", 'last_event': "Сбой"}
        
    # ЧАСЫ И БУДИЛЬНИК

    def update_col_width(self, value):
        self.col_width = int(value)
        self.update_data(force=True)

    def update_row_height(self, value):
        self.row_height = int(value)
        self.update_data(force=True)

    # CANVAS (ХОЛСТ) 
    def create_canvas(self):
        self.canvas_container = tk.Frame(self)
        self.canvas_container.pack(fill=tk.NONE, expand=False, padx=10, pady=10) 
        
        self.canvas = tk.Canvas(self.canvas_container, bg="#2E2E2E", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.NONE, expand=False)
        
        self.canvas.bind("<Double-1>", self.on_canvas_click) 

    def calculate_required_width(self):
        num_columns = 13
        cw = self.col_width
        pad = self.padding
        
        total_table_content_width = pad + (num_columns * cw) + pad 
        canvas_container_padx = 20
        required_width = total_table_content_width + canvas_container_padx
        
        return max(required_width, self.initial_w)
        
    def calculate_required_height(self, num_data_rows):
        num_rows = num_data_rows + 1  # +1 для строки заголовка
        if num_rows < 1: 
            num_rows = 1
        
        rh = self.row_height
        pad = self.padding
        
        canvas_h_content = pad + num_rows * rh + pad
        required_height = canvas_h_content + self.FIXED_CONTROLS_HEIGHT
        
        return max(required_height, self.initial_h)

    def adjust_window_geometry(self, required_width, required_height):
        self.geometry(f"{required_width}x{required_height}")
        self.update_idletasks()

    def parse_date_safe(self, date_val):
        """
        Безопасное преобразование даты с обработкой различных форматов.
        
        Args:
            date_val: Значение даты (datetime, date, str или None)
            
        Returns:
            date object или None при ошибке
        """
        if isinstance(date_val, datetime):
            return date_val.date()
        elif isinstance(date_val, date):
            return date_val
        elif isinstance(date_val, str):
            try:
                return datetime.strptime(date_val, '%Y-%m-%d').date()
            except ValueError as e:
                logger.warning(f"[TPulse] Ошибка парсинга даты: {date_val}, ошибка: {e}")
                return None
        return None

    def get_date_cell_color(self, date_val, today):
        """
        Определяет цвет ячейки с датой на основе условий подсветки.
        
        Приоритет цветов (от высшего к низшему):
        1. Красный - дата с дублирующимися записями
        2. Жёлтый - текущая дата
        3. Голубой - будущая дата
        4. Серый - обычная дата
        
        Args:
            date_val: date object
            today: date object текущей даты
            
        Returns:
            str: HEX-код цвета
        """
        # Базовый цвет
        fill_color = self.COLOR_DATE_DEFAULT
        
        if date_val:
            # Будущая дата (самый низкий приоритет)
            if date_val > today:
                fill_color = self.COLOR_DATE_FUTURE
            
            # Текущая дата (средний приоритет)
            if date_val == today:
                fill_color = self.COLOR_DATE_TODAY
            
            # Дата с дублями (высший приоритет - переопределяет все)
            if date_val in self.duplicate_dates and self.duplicate_dates[date_val] > 0:
                fill_color = self.COLOR_DATE_DUPLICATE
        
        return fill_color

    def draw_table(self, data):
        """Отрисовка главной таблицы с данными и выделением текущей даты рамкой."""
        self.canvas.delete("all")
        self.current_data = data
        
        cw, rh = self.col_width, self.row_height
        font_header = ("Helvetica", self.font_header_size)
        font_cell = ("Helvetica", self.font_cell_size)
        pad = self.padding
        
        # Словарь дней недели на русском (без зависимости от локали Windows)
        WEEKDAY_RU = {0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'}
        
        columns = ["Дата", "1РО План", "1РО Отлеч", "1РО Всего",
                   "2РО План", "2РО Отлеч", "2РО Всего",
                   "Др. План", "Др. Отлеч", "Др. Всего",
                   "Σ План", "Σ Отлеч", "Σ ВСЕГО"]
        
        # Размеры холста
        num_columns = 13
        num_rows = len(data) + 1
        
        canvas_w = pad + num_columns * cw + pad
        canvas_h = pad + num_rows * rh + pad
        self.canvas.config(width=canvas_w, height=canvas_h)
        
        # Заголовки
        for i, col_name in enumerate(columns):
            x = pad + i * cw
            self.canvas.create_rectangle(x, pad, x + cw, pad + rh, fill="#444", outline="white")
            self.canvas.create_text(x + cw/2, pad + rh/2, text=col_name, fill="white", 
                                   font=font_header, width=cw-5)

        # Текущая дата
        today = datetime.now().date()
        today_row_y = None

        # Данные
        for r_idx, row in enumerate(data):
            y = pad + (r_idx + 1) * rh
            
            date_val = self.parse_date_safe(row[0])
            if date_val == today:
                today_row_y = y

            # ОПРЕДЕЛЯЕМ: выходной ИЛИ все нули
            is_weekend = date_val is not None and date_val.weekday() >= 5
            numeric_vals = row[1:]  # 12 числовых ячеек
            all_zeros = all((v is None or int(v) == 0) for v in numeric_vals)
            is_grey_row = is_weekend or all_zeros

            for c_idx, val in enumerate(row):
                x = pad + c_idx * cw

                if is_grey_row:
                    # Вся строка серая, дата чуть темнее
                    fill_color = self.COLOR_WEEKEND_ZERO
                    text_color = self.COLOR_WEEKEND_ZERO_TEXT
                elif c_idx == 0:
                    fill_color = self.get_date_cell_color(date_val, today)
                    text_color = "white" if fill_color == self.COLOR_DATE_DUPLICATE else "black"
                else:
                    series_key, status_key = self._get_keys_from_col_index(c_idx)
                    color_name = f"{status_key}_{series_key}"
                    fill_color = self.colors.get(color_name, self.colors['Default'])
                    text_color = "black"

                self.canvas.create_rectangle(x, y, x + cw, y + rh, fill=fill_color, outline="white")
                
                # Текст
                if c_idx == 0 and date_val:
                    # Новый формат: "20.02-Пт"
                    text_val = f"{date_val.strftime('%d.%m')}-{WEEKDAY_RU[date_val.weekday()]}"
                else:
                    text_val = str(val or 0)
                    
                self.canvas.create_text(x + cw/2, y + rh/2, text=text_val, fill=text_color, font=font_cell)

        # РАМКА ДЛЯ СЕГОДНЯ 
        if today_row_y is not None:
            table_width = num_columns * cw
            self.canvas.create_rectangle(
                pad, today_row_y, 
                pad + table_width, today_row_y + rh, 
                outline=self.COLOR_TODAY_BORDER,
                width=4,
                tags="today_highlight"
            )

    def _get_keys_from_col_index(self, c_idx):
        """Получение ключей серии и статуса из индекса колонки."""
        status_key = ""
        series_key = ""
        
        if c_idx in [1, 4, 7, 10]: 
            status_key = "Planned"
        elif c_idx in [2, 5, 8, 11]: 
            status_key = "Completed"
        elif c_idx in [3, 6, 9, 12]: 
            status_key = "Total"

        if c_idx in [1, 2, 3]: 
            series_key = "1"
        elif c_idx in [4, 5, 6]: 
            series_key = "2"
        elif c_idx in [7, 8, 9]: 
            series_key = "Other"
        elif c_idx in [10, 11, 12]: 
            series_key = "All"
        
        return series_key, status_key

    # ОБРАБОТЧИК КЛИКОВ 
    def on_canvas_click(self, event):
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        
        pad = self.padding
        
        # Игнорируем клики вне таблицы и на заголовке
        if canvas_x < pad or canvas_y < pad + self.row_height:
            return

        col_idx = int((canvas_x - pad) // self.col_width)
        row_idx = int((canvas_y - pad - self.row_height) // self.row_height)
        
        if 0 <= row_idx < len(self.current_data) and 0 <= col_idx < 13:
            # Клики на колонку дат не обрабатываем
            if col_idx == 0: 
                return
            
            row_data = self.current_data[row_idx]
            
            # Форматирование даты для SQL-запроса
            date_val = row_data[0]
            if isinstance(date_val, datetime):
                date_val = date_val.strftime('%Y-%m-%d')
            elif isinstance(date_val, date):
                date_val = date_val.strftime('%Y-%m-%d')
            else:
                date_val = str(date_val)
                
            series_key, status_key = self._get_keys_from_col_index(col_idx)
            
            color_name = f"{status_key}_{series_key}"
            fill_color = self.colors.get(color_name, self.colors['Default'])
            
            self.on_cell_double_click_logic(date_val, series_key, status_key, fill_color)

    def detect_duplicate_bookings(self):
        """
        Обнаруживает даты, где у одного или более пациентов есть несколько записей.
        
        Returns:
            dict: Словарь {дата: количество_пациентов_с_дублями}
        """
        query = f'''
        SELECT 
            tc.visitdate::date,
            ts.patient_id,
            COUNT(*) as visit_count
        FROM tcalendar tc
        JOIN tseries ts ON tc.series_id = ts.series_id
        WHERE tc.visitdate BETWEEN 
            CURRENT_DATE - INTERVAL '{self.HISTORY_DAYS} days' 
            AND CURRENT_DATE + INTERVAL '{self.FUTURE_DAYS} days'
        GROUP BY tc.visitdate::date, ts.patient_id
        HAVING COUNT(*) > 1
        ORDER BY tc.visitdate DESC;
        '''
        
        try:
            data = execute_query(query, ())
            self._set_db_status(True)
        except DatabaseError:
            self._set_db_status(False)
            return

        duplicate_dates = {}
        
        if isinstance(data, list):
            for row in data:
                visit_date = self.parse_date_safe(row[0])
                if visit_date:
                    if visit_date not in duplicate_dates:
                        duplicate_dates[visit_date] = 0
                    duplicate_dates[visit_date] += 1
            
            # ЛОГИРОВАНИЕ 
            current_count = len(duplicate_dates)
            
            # Логируем только если количество дат с дублями изменилось
            if current_count != self.last_duplicate_count:
                if current_count > 0:
                    logger.info(f"[TPulse] ВНИМАНИЕ: Обнаружено {current_count} дат с дублирующимися записями")
                elif self.last_duplicate_count > 0:
                    logger.info("[TPulse] ВСЕ ДУБЛИ ИСПРАВЛЕНЫ: Дублирующихся записей больше нет")
                
                self.last_duplicate_count = current_count
        
        return duplicate_dates

    # ЦИКЛ ОБНОВЛЕНИЯ ГЛАВНОЙ ТАБЛИЦЫ (5 сек) 
    def update_data(self, force=False):
        """Запуск фонового обновления данных главной таблицы."""
        threading.Thread(target=self._update_data_worker, args=(force,), daemon=True).start()

    def _update_data_worker(self, force=False):
        """Воркер для получения данных из БД в фоновом потоке."""
        self.gc_counter += 1
        if self.gc_counter >= 12: 
            gc.collect()
            self.gc_counter = 0

        query = f'''
        WITH RECURSIVE dates AS (
            SELECT CURRENT_DATE - INTERVAL '{self.HISTORY_DAYS} days' AS visitdate
            UNION ALL
            SELECT visitdate + INTERVAL '1 day' 
            FROM dates 
            WHERE visitdate < CURRENT_DATE + INTERVAL '{self.FUTURE_DAYS} days'
        ), all_dates AS ( SELECT visitdate::date FROM dates )
        SELECT
            all_dates.visitdate,
            COALESCE(planned."1РО", 0), COALESCE(completed."1РО", 0), COALESCE(total."1РО", 0),
            COALESCE(planned."2РО", 0), COALESCE(completed."2РО", 0), COALESCE(total."2РО", 0),
            COALESCE(planned."Другое", 0), COALESCE(completed."Другое", 0), COALESCE(total."Другое", 0),
            COALESCE(planned."Всего", 0), COALESCE(completed."Всего", 0), COALESCE(total."Всего", 0)
        FROM all_dates
        LEFT JOIN (
            SELECT tc.visitdate,
                COUNT(DISTINCT CASE WHEN ts.name LIKE %s THEN tu.patient_id END) AS "1РО",
                COUNT(DISTINCT CASE WHEN ts.name LIKE %s THEN tu.patient_id END) AS "2РО",
                COUNT(DISTINCT CASE WHEN ts.name NOT LIKE %s AND ts.name NOT LIKE %s THEN tu.patient_id END) AS "Другое",
                COUNT(DISTINCT tu.patient_id) AS "Всего"
            FROM tcalendar tc 
            JOIN tseries ts ON tc.series_id = ts.series_id 
            JOIN tpatient tu ON ts.patient_id = tu.patient_id
            WHERE tc.visitdate BETWEEN 
                CURRENT_DATE - INTERVAL '{self.HISTORY_DAYS} days' 
                AND CURRENT_DATE + INTERVAL '{self.FUTURE_DAYS} days' 
                AND tc.calendar_status_id = %s
            GROUP BY tc.visitdate
        ) AS planned ON all_dates.visitdate = planned.visitdate
        LEFT JOIN (
            SELECT tc.visitdate,
                COUNT(DISTINCT CASE WHEN ts.name LIKE %s THEN tu.patient_id END) AS "1РО",
                COUNT(DISTINCT CASE WHEN ts.name LIKE %s THEN tu.patient_id END) AS "2РО",
                COUNT(DISTINCT CASE WHEN ts.name NOT LIKE %s AND ts.name NOT LIKE %s THEN tu.patient_id END) AS "Другое",
                COUNT(DISTINCT tu.patient_id) AS "Всего"
            FROM tcalendar tc 
            JOIN tseries ts ON tc.series_id = ts.series_id 
            JOIN tpatient tu ON ts.patient_id = tu.patient_id
            WHERE tc.visitdate BETWEEN 
                CURRENT_DATE - INTERVAL '{self.HISTORY_DAYS} days' 
                AND CURRENT_DATE + INTERVAL '{self.FUTURE_DAYS} days' 
                AND tc.calendar_status_id = %s
            GROUP BY tc.visitdate
        ) AS completed ON all_dates.visitdate = completed.visitdate
        LEFT JOIN (
            SELECT tc.visitdate,
                COUNT(DISTINCT CASE WHEN ts.name LIKE %s THEN tu.patient_id END) AS "1РО",
                COUNT(DISTINCT CASE WHEN ts.name LIKE %s THEN tu.patient_id END) AS "2РО",
                COUNT(DISTINCT CASE WHEN ts.name NOT LIKE %s AND ts.name NOT LIKE %s THEN tu.patient_id END) AS "Другое",
                COUNT(DISTINCT tu.patient_id) AS "Всего"
            FROM tcalendar tc 
            JOIN tseries ts ON tc.series_id = ts.series_id 
            JOIN tpatient tu ON ts.patient_id = tu.patient_id
            WHERE tc.visitdate BETWEEN 
                CURRENT_DATE - INTERVAL '{self.HISTORY_DAYS} days' 
                AND CURRENT_DATE + INTERVAL '{self.FUTURE_DAYS} days'
            GROUP BY tc.visitdate
        ) AS total ON all_dates.visitdate = total.visitdate
        ORDER BY all_dates.visitdate DESC;
        '''
        
        try:
            # Все LIKE паттерны передаем явно
            params = (
                '1%', '2%', '1%', '2%', self.STATUS_PLANNED_ID,  # Для planned
                '1%', '2%', '1%', '2%', self.STATUS_COMPLETED_ID, # Для completed
                '1%', '2%', '1%', '2%'                           # Для total
            )
            data = execute_query(query, params)
            self.after(0, lambda: self._set_db_status(True))
        except DatabaseError:
            self.after(0, lambda: self._set_db_status(False))
            # Планируем следующую попытку даже при ошибке
            if not force:
                self.after(5000, self.update_data)
            return
            
        if isinstance(data, list):
            # Обнаружение дублей тоже в фоне
            self.duplicate_dates = self.detect_duplicate_bookings()
            
            if len(data) > self.TOTAL_DISPLAY_ROWS:
                data = data[:self.TOTAL_DISPLAY_ROWS]

            # Все операции с GUI (изменение размеров, отрисовка) отправляем в главный поток
            self.after(0, lambda d=data, f=force: self._update_gui_after_fetch(d, f))

    def _update_gui_after_fetch(self, data, force):
        """Обновление GUI после успешного получения данных из БД."""
        if force or not self.geometry_set:
            required_width = self.calculate_required_width()
            required_height = self.calculate_required_height(len(data))
            self.adjust_window_geometry(required_width, required_height)
            self.geometry_set = True
            
        self.draw_table(data)
        
        if not force:
            self.after(5000, self.update_data)

    # ЦИКЛ ОБНОВЛЕНИЯ ВЕРХНЕЙ ПАНЕЛИ (5 сек) 
    def update_on_treatment_label_loop(self):
        """Цикл обновления верхней панели."""
        try:
            self.update_on_treatment_label()
            self.after(5000, self.update_on_treatment_label_loop)
        except Exception as e:
            if "invalid command name" not in str(e):
                logger.error(f"[TPulse] Ошибка в цикле мониторинга: {e}")

    def update_on_treatment_label(self):
        """Запуск фонового обновления табло."""
        threading.Thread(target=self._update_on_treatment_label_worker, daemon=True).start()

    def _update_on_treatment_label_worker(self):
        """Воркер для обновления табло в фоновом потоке."""
        query_active = """
        SELECT tp.surname, tp.forename, ts.name, tp.patient_id, tc.calendar_id, ts.note
        FROM tcalendar tc
        INNER JOIN tseries ts ON tc.series_id = ts.series_id
        INNER JOIN tpatient tp ON ts.patient_id = tp.patient_id
        WHERE tc.visitdate::date = CURRENT_DATE 
          AND tc.calendar_status_id = %s;
        """
        
        query_count_planned = f"""
        SELECT COUNT(DISTINCT ts.patient_id)
        FROM tcalendar tc
        JOIN tseries ts ON tc.series_id = ts.series_id
        WHERE tc.visitdate::date = CURRENT_DATE AND tc.calendar_status_id = {self.STATUS_PLANNED_ID};
        """
        
        query_count_completed = f"""
        SELECT COUNT(DISTINCT ts.patient_id)
        FROM tcalendar tc
        JOIN tseries ts ON tc.series_id = ts.series_id
        WHERE tc.visitdate::date = CURRENT_DATE AND tc.calendar_status_id = {self.STATUS_COMPLETED_ID};
        """
        
        try:
            now = datetime.now()
            today_date = now.date()
            new_patient_str = ""
            bot_status_str = "" 
            current_patient_id = None
            
            # Инициализация переменных состояния (через after для безопасности)
            if not hasattr(self, 'active_calendar_id'): self.active_calendar_id = None
            if not hasattr(self, 'known_ids_today'): self.known_ids_today = set()
            if not hasattr(self, 'last_notified_date'): self.last_notified_date = None

            # КРИТИЧЕСКАЯ ПРАВКА: СМЕНА ДНЯ
            if self.last_notified_date != today_date:
                try:
                    data_startup = execute_query(query_active, (self.STATUS_ON_TREATMENT_ID,))
                    self.after(0, lambda: self._set_db_status(True))
                    
                    if data_startup and isinstance(data_startup, list):
                        startup_ids = {row[4] for row in data_startup}
                        self.known_ids_today = startup_ids
                        
                        if startup_ids:
                            self.active_calendar_id = max(startup_ids)
                            active_row = next(r for r in data_startup if r[4] == self.active_calendar_id)
                            surname, forename, s_name, p_id, c_id, note = active_row
                            
                            p_short = format_name_short(f"{surname} {forename}")
                            doc_raw = (note or "").strip().split()
                            limit = 4 if str(s_name).startswith('2') else 3
                            doc_display = f" ({doc_raw[0].capitalize()[:limit]})" if doc_raw else ""
                            
                            bot_status_init = f"{p_short}{doc_display}"
                            self.last_bot_status = bot_status_init
                            self.last_patient_name = f"{surname} {forename} ({s_name})"
                            
                            if hasattr(self, 'tg_bot'):
                                self.tg_bot.set_on_treatment(bot_status_init)
                    else:
                        self.known_ids_today = set()
                except DatabaseError:
                    data_startup = []
                    self.known_ids_today = set()
                
                self.low_patients_notified = False
                self.last_notified_date = today_date
                
                # Сообщаем боту, что наступил новый день
                if hasattr(self, 'tg_bot'):
                    self.tg_bot._last_msg_id = None
                    self.tg_bot._last_msg_date = None
                    if self.tg_bot._enabled and getattr(self.tg_bot, '_connection_state', '') == "connected":
                        self.tg_bot.trigger_force_update()


            # --- ПЕРЕНЕСЕНО ВЫШЕ (бывший блок 2) ---
            # 1.1 ЛОГИКА ОПРЕДЕЛЕНИЯ АКТУАЛЬНОГО ПАЦИЕНТА (Теперь ПЕРЕД расчетом времени)
            try:
                data_active = execute_query(query_active, (self.STATUS_ON_TREATMENT_ID,))
                self._set_db_status(True)
            except DatabaseError:
                self._set_db_status(False)
                data_active = []
            
            current_ids = [row[4] for row in data_active] if (data_active and data_active != 'Error') else []
            
            if current_ids:
                new_ids = [cid for cid in current_ids if cid not in self.known_ids_today]
                if new_ids:
                    self.active_calendar_id = new_ids[-1]
                    for cid in current_ids:
                        self.known_ids_today.add(cid)
                    print(f"[NEW SESSION] Обнаружен новый вход: ID {self.active_calendar_id}")
                
                if self.active_calendar_id in current_ids:
                    active_row = next(r for r in data_active if r[4] == self.active_calendar_id)
                else:
                    self.active_calendar_id = max(current_ids)
                    active_row = next(r for r in data_active if r[4] == self.active_calendar_id)

                # РАСПАКОВКА
                surname, forename, series_name, p_id, c_id, note = active_row
                
                new_patient_str = f"{surname} {forename} ({series_name})"
                current_patient_id = p_id

                # ФИО Доктора и форматирование имени
                full_name_string = f"{surname} {forename}"
                p_short = format_name_short(full_name_string)
                doc_raw = (note or "").strip().split()
                limit = 4 if str(series_name).startswith('2') else 3
                doc_display = f" ({doc_raw[0].capitalize()[:limit]})" if doc_raw else ""
                bot_status_str = f"{p_short}{doc_display}"
                self.last_bot_status = bot_status_str
            else:
                self.active_calendar_id = None
                new_patient_str = ""
                bot_status_str = "свободно"
                self.last_bot_status = "свободно"
                current_patient_id = None

            # ОБРАБОТКА СМЕНЫ ПАЦИЕНТА / ТРИГГЕР БОТА
            if not hasattr(self, 'last_patient_id'): self.last_patient_id = None
            
            if current_patient_id != self.last_patient_id:
                if self.last_patient_id is not None:
                    self.check_and_log_incomplete_dose(self.last_patient_id, getattr(self, 'last_patient_name', ''))
                    self.idle_start_time = now

                if current_patient_id is not None:
                    if self.sounds_enabled: winsound.Beep(1000, 400) 
                    logger.info(f"[TPulse] На аппарате: {new_patient_str}")
                    self.idle_start_time = None
                else:
                    logger.info("[TPulse] На аппарате: свободно")
                    self.idle_start_time = now

                self.last_patient_id = current_patient_id
                self.last_patient_name = new_patient_str
                
                # ОТПРАВКА СТАТУСА БОТУ
                if hasattr(self, 'tg_bot') and self.tg_bot and self.tg_bot._enabled:
                    bot_state = getattr(self.tg_bot, '_connection_state', None)
                    if bot_state == "connected":
                        self.tg_bot.set_on_treatment(bot_status_str)

            # 1. ПРОВЕРКА ОСТАТКА ПАЦИЕНТОВ (Planned) И РАСЧЕТ ОКОНЧАНИЯ
            try:
                # Количество оставшихся
                planned_res = execute_query(query_count_planned, ())
                
                # Количество отлеченных (Задача: Отлечено: ХХ)
                completed_res = execute_query(query_count_completed, ())

                # 1.2 Суммарная экспозиция ОСТАВШИХСЯ (для расчета окончания)
                query_exp_remaining = f'''
                    SELECT SUM(CAST(tp_last.value_plan AS FLOAT))
                    FROM (
                        SELECT DISTINCT ON (tf.field_id, tp.par_id) tp.value_plan
                        FROM tcalendar tc
                        JOIN tseries ts ON tc.series_id = ts.series_id
                        JOIN tfield tf ON ts.series_id = tf.series_id
                        JOIN tplan tp ON tf.field_id = tp.field_id
                        WHERE tc.visitdate = CURRENT_DATE
                          AND tc.calendar_status_id IN ({self.STATUS_PLANNED_ID}, {self.STATUS_ON_TREATMENT_ID})
                          AND tp.par_id = 'SH'
                        ORDER BY tf.field_id, tp.par_id, tp.insert_tms DESC
                    ) as tp_last
                '''
                exp_res = execute_query(query_exp_remaining, ())
                
                self._set_db_status(True)
            except DatabaseError:
                self._set_db_status(False)
                return
                
            # 1. ОБРАБОТКА РЕЗУЛЬТАТОВ ПЛАНИРОВАНИЯ
            remaining_count = 0
            if planned_res and isinstance(planned_res, list):
                remaining_count = int(planned_res[0][0])

                if not hasattr(self, 'last_remaining_count'): 
                    self.last_remaining_count = remaining_count

                # ПРОВЕРЯЕМ ИЗМЕНЕНИЕ СПИСКА (для Telegram)
                if remaining_count != self.last_remaining_count:
                    if hasattr(self, 'tg_bot') and self.tg_bot._enabled:
                        bot_state = getattr(self.tg_bot, '_connection_state', None)
                        if bot_state == "connected":
                            self.tg_bot.trigger_force_update()
                            self.last_remaining_count = remaining_count

                # Звуковое уведомление об окончании пациентов
                if remaining_count > self.LOW_PATIENTS_THRESHOLD:
                    self.low_patients_notified = False
                elif 0 < remaining_count <= self.LOW_PATIENTS_THRESHOLD and not self.low_patients_notified:
                    if self.sounds_enabled:
                        for _ in range(7):
                            winsound.Beep(1800, 200)
                            time.sleep(0.1)
                    self.low_patients_notified = True

            # 1.1 ОБРАБОТКА РЕЗУЛЬТАТОВ ВЫПОЛНЕННЫХ
            completed_count = 0
            if completed_res and isinstance(completed_res, list):
                completed_count = int(completed_res[0][0])
                
                if not hasattr(self, 'last_completed_count'):
                    self.last_completed_count = completed_count
                    if hasattr(self, 'tg_bot') and self.tg_bot._enabled:
                        if hasattr(self.tg_bot, 'set_completed_count'):
                            self.tg_bot.set_completed_count(completed_count)
                
                if completed_count != self.last_completed_count:
                    if hasattr(self, 'tg_bot') and self.tg_bot._enabled:
                        bot_state = getattr(self.tg_bot, '_connection_state', None)
                        # В XR версии нет _connection_state, поэтому проверяем наличие
                        if bot_state == "connected" or not hasattr(self.tg_bot, '_connection_state'):
                            if hasattr(self.tg_bot, 'set_completed_count'):
                                self.tg_bot.set_completed_count(completed_count)
                                # Для WS версии set_completed_count сам триггерит обновление, 
                                # для XR версии это произойдет в следующем цикле опроса.
                            self.last_completed_count = completed_count

            # 1.2 РАСЧЕТ ВРЕМЕНИ ЗАВЕРШЕНИЯ СМЕНЫ (Задача 13)
            try:
                raw_exp = float(exp_res[0][0]) if exp_res and exp_res[0][0] is not None else 0
                exp_sec = raw_exp / 10.0
                
                # Раньше было if remaining_count > 0, теперь считаем, если есть хоть какая-то экспозиция
                if exp_sec > 0 or remaining_count > 0:
                    # Формула: экспозиция + кол-во * среднее время (из конфига)
                    pauses_sec = remaining_count * self.AVG_TIME_PER_PATIENT * 60
                    total_rem_sec = exp_sec + pauses_sec
                    
                    finish_dt = datetime.now() + timedelta(seconds=total_rem_sec)
                    finish_str = finish_dt.strftime("%H:%M")
                    self.after(0, lambda s=finish_str: self.shift_end_label.configure(text=f"🏁 Конец в: {s}"))
                    
                    # Отправляем в Telegram только если HH:MM изменилось
                    if hasattr(self, 'tg_bot') and self.tg_bot and self.tg_bot._enabled:
                        if hasattr(self.tg_bot, 'set_shift_end'):
                            if finish_str != getattr(self, '_last_tg_shift_end', ''):
                                self.tg_bot.set_shift_end(finish_str)
                                self._last_tg_shift_end = finish_str
                else:
                    self.after(0, lambda: self.shift_end_label.configure(text="🏁 Конец в: --:--"))
                    if hasattr(self, 'tg_bot') and self.tg_bot and self.tg_bot._enabled:
                        if hasattr(self.tg_bot, 'set_shift_end'):
                            if "--:--" != getattr(self, '_last_tg_shift_end', ''):
                                self.tg_bot.set_shift_end("--:--")
                                self._last_tg_shift_end = "--:--"
            except Exception as e:
                logger.debug(f"[TPulse] Ошибка расчета времени окончания: {e}")
                self.after(0, lambda: self.shift_end_label.configure(text="🏁 Конец в: --:--"))
                if hasattr(self, 'tg_bot') and self.tg_bot and self.tg_bot._enabled:
                    if hasattr(self.tg_bot, 'set_shift_end'):
                        if "--:--" != getattr(self, '_last_tg_shift_end', ''):
                            self.tg_bot.set_shift_end("--:--")
                            self._last_tg_shift_end = "--:--"


            # 4. ОБНОВЛЕНИЕ ТЕКСТА В ИНТЕРФЕЙСЕ
            self.on_treatment_label.configure(
                text=f"Сейчас на аппарате: {new_patient_str}" if new_patient_str else "На аппарате: свободно",
                text_color="#FFD700" if new_patient_str else "white",
                font=("Helvetica", 16, "bold") if new_patient_str else ("Helvetica", 16)
            )
        except Exception as e:
            logger.error(f"[TPulse] Ошибка обновления табло: {e}")

    def check_and_log_incomplete_dose(self, patient_id, patient_name):
        """Проверяет пропущенные пучки. Пишет в лог ТОЛЬКО если они есть."""
        query = """
        SELECT ts.name FROM tcalendar tc
        JOIN tseries ts ON tc.series_id = ts.series_id
        WHERE ts.patient_id = %s AND tc.visitdate::date = CURRENT_DATE
          AND tc.calendar_status_id = %s;
        """
        try:
            try:
                incomplete = execute_query(query, (patient_id, self.STATUS_PLANNED_ID))
                self._set_db_status(True)
            except DatabaseError:
                self._set_db_status(False)
                incomplete = []
            if incomplete:
                logger.warning(f"[TPulse] !!! ДОЗА НЕ ПОЛНАЯ !!! Пациент: {patient_name}")
        except Exception as e:
            logger.error(f"[TPulse] Ошибка проверки дозы: {e}")

    # УПРАВЛЕНИЕ ПРОКРУТКОЙ (для окон клиентов) 
    def _bind_mousewheel_scroll(self, widget, canvas_or_frame):
        """Привязка прокрутки колёсиком мыши к виджету.
        
        Поддерживает как стандартный tk.Canvas / tk.Scrollbar,
        так и customtkinter.CTkScrollableFrame.
        """
        def _scroll(direction):
            """Универсальная прокрутка: пробуем yview_scroll, иначе ищем внутренний canvas."""
            target = canvas_or_frame
            # CTkScrollableFrame хранит реальный canvas в атрибуте _parent_canvas
            if not hasattr(target, 'yview_scroll'):
                if hasattr(target, '_parent_canvas'):
                    target = target._parent_canvas
                else:
                    return
            target.yview_scroll(direction, "unit")

        def on_mousewheel(event):
            if event.num == 5 or event.delta < 0:
                _scroll(1)
            elif event.num == 4 or event.delta > 0:
                _scroll(-1)
        
        widget.bind("<MouseWheel>", on_mousewheel)
        widget.bind("<Button-4>", on_mousewheel)
        widget.bind("<Button-5>", on_mousewheel)
        
        if hasattr(widget, 'winfo_children'):
            for child in widget.winfo_children():
                self._bind_mousewheel_scroll(child, canvas_or_frame)

    # ЛОГИКА ОКНА КЛИЕНТОВ 
    def on_cell_double_click_logic(self, date_val, series_key, status_key, color):
            """Обработка двойного клика с синхронизацией цвета и отчета по истории лечения."""
            if not status_key: 
                return
            
            status_id = self.STATUS_PLANNED_ID if status_key == 'Planned' else (self.STATUS_COMPLETED_ID if status_key == 'Completed' else None)
            status_filter = f"tc.calendar_status_id = {status_id}" if status_id else "1=1"
            
            ser_filter = "1=1"
            if series_key == "1": ser_filter = "ts.name LIKE '1%%'"
            elif series_key == "2": ser_filter = "ts.name LIKE '2%%'"
            elif series_key == "Other": ser_filter = "ts.name NOT LIKE '1%%' AND ts.name NOT LIKE '2%%'"

            # 1. Основной запрос данных + сумма АКТУАЛЬНОЙ экспозиции (последние значения 'SH')
            query = f'''
                SELECT tu.surname, tu.forename, ts.name, tcs.calendar_status_id, 
                       tcs.name, ts.note, tu.patient_id, tc.series_id,
                       (SELECT SUM(CAST(tp_last.value_plan AS FLOAT))
                        FROM (
                            SELECT DISTINCT ON (field_id, par_id) value_plan
                            FROM tplan
                            WHERE field_id IN (SELECT field_id FROM tfield WHERE series_id = ts.series_id)
                              AND par_id = 'SH'
                            ORDER BY field_id, par_id, insert_tms DESC
                        ) as tp_last) as total_exposure
                FROM tcalendar tc
                INNER JOIN tseries ts ON tc.series_id = ts.series_id
                INNER JOIN tpatient tu ON ts.patient_id = tu.patient_id
                INNER JOIN tcalendar_status tcs ON tc.calendar_status_id = tcs.calendar_status_id
                WHERE tc.visitdate = %s AND ({status_filter}) AND ({ser_filter})
                ORDER BY tu.surname, tu.forename;
            '''
            try:
                data = execute_query(query, (date_val,))
            except DatabaseError:
                return

            series_ids = list({row[7] for row in data if row[7]})
            patient_ids = list({row[6] for row in data if row[6]})

            new_patient_dict = {}   
            last_fraction_dict = {} 
            first_series_by_patient = {} 

            if series_ids and patient_ids:
                placeholders_s = ','.join(['%s'] * len(series_ids))
                placeholders_p = ','.join(['%s'] * len(patient_ids))

                # Запросы вспомогательных дат
                try:
                    min_date_data = execute_query(f"SELECT series_id, MIN(visitdate)::date FROM tcalendar WHERE series_id IN ({placeholders_s}) GROUP BY series_id", tuple(series_ids))
                    last_inserted_data = execute_query(f"SELECT series_id, MAX(visitdate)::date FROM tcalendar WHERE series_id IN ({placeholders_s}) AND calendar_status_id = %s GROUP BY series_id", tuple(series_ids) + (self.STATUS_PLANNED_ID,))
                    max_any_data = execute_query(f"SELECT series_id, MAX(visitdate)::date FROM tcalendar WHERE series_id IN ({placeholders_s}) GROUP BY series_id", tuple(series_ids))
                except DatabaseError:
                    min_date_data, last_inserted_data, max_any_data = [], [], []

                # УМНЫЙ ЗАПРОС ИСТОРИИ (Второй план)
                has_real_history = {}
                query_history = f'''
                    SELECT DISTINCT ts.patient_id
                    FROM tseries ts
                    INNER JOIN tcalendar tc ON ts.series_id = tc.series_id
                    WHERE ts.patient_id IN ({placeholders_p})
                      AND ts.series_id NOT IN ({placeholders_s})
                      AND tc.calendar_status_id = {self.STATUS_COMPLETED_ID}
                '''
                history_res = execute_query(query_history, tuple(patient_ids) + tuple(series_ids))
                if isinstance(history_res, list):
                    has_real_history = {int(r[0]): True for r in history_res}

                # Сбор словарей для логики
                first_date_by_series = {r[0]: r[1] for r in min_date_data} if isinstance(min_date_data, list) else {}
                last_inserted_by_series = {r[0]: r[1] for r in last_inserted_data} if isinstance(last_inserted_data, list) else {}
                max_any_by_series = {r[0]: r[1] for r in max_any_data} if isinstance(max_any_data, list) else {}
                
                window_date = self.parse_date_safe(date_val)
                today_obj = date.today()

                # Списки для разделения отчетов
                new_patients_1ro = []
                new_patients_2ro = []

                for row in data:
                    pid, sid = int(row[6]), int(row[7])
                    series_name = str(row[2]).strip()

                    # ЛОГИКА НОВЫХ
                    if first_date_by_series.get(sid) == window_date:
                        new_patient_dict[pid] = sid
                        is_repeat = has_real_history.get(pid, False)

                        if not is_repeat:
                            first_series_by_patient[pid] = sid 

                        # Данные для CSV
                        if window_date == today_obj:
                            # --- ИСПРАВЛЕННЫЙ БЛОК ФОРМИРОВАНИЯ ИМЕНИ ---
                            surname = str(row[0]).strip()
                            forename_full = str(row[1]).strip() if row[1] else ""
                            
                            name_parts = forename_full.split()
                            i = f"{name_parts[0][0].upper()}." if len(name_parts) > 0 else ""
                            o = f"{name_parts[1][0].upper()}." if len(name_parts) > 1 else ""
                            
                            # Собираем ФИО с двумя инициалами
                            full_patient_name = f"{surname} {i}{o}".strip().upper()

                            patient_info = {
                                "patient_name": full_patient_name,
                                "comment": str(row[5]) if row[5] else "",
                                "is_repeat_plan": is_repeat,
                            }
                            
                            # Распределение по спискам на основе ПЕРВОЙ цифры серии
                            if series_name.startswith("2"):
                                new_patients_2ro.append(patient_info)
                            else:
                                new_patients_1ro.append(patient_info)

                    # ЛОГИКА СИНЕГО (Финал)
                    l_date = last_inserted_by_series.get(sid)
                    if (l_date and window_date == l_date) or (l_date is None and max_any_by_series.get(sid) == window_date):
                        last_fraction_dict[pid] = sid

                # ОБНОВЛЕНИЕ ОТЧЕТОВ (Точечное, в зависимости от выбранной колонки)
                if window_date == today_obj:
                    try:
                        if series_key == "1":
                            # Обновляем только отчет 1РО
                            generate_daily_report(new_patients_1ro, report_date=window_date, subfolder="1RO")
                        elif series_key == "2":
                            # Обновляем только отчет 2РО
                            generate_daily_report(new_patients_2ro, report_date=window_date, subfolder="2RO")
                        else:
                            # Если выбраны все (Other), обновляем оба
                            generate_daily_report(new_patients_1ro, report_date=window_date, subfolder="1RO")
                            generate_daily_report(new_patients_2ro, report_date=window_date, subfolder="2RO")
                    except Exception as e:
                        logger.error(f"[TPulse] Ошибка синхронизации отчетов при клике: {e}")

            # Открываем окно
            self.open_client_window(data, date_val, status_key, series_key, color,
                                    new_patient_dict, last_fraction_dict,
                                    first_series_by_patient)
                                    
    def open_client_window(self, data, date_val, status_key, series_key, bg_color,
                           new_patient_dict=None, last_fraction_dict=None,
                           first_series_by_patient=None):
        """Открытие окна со списком клиентов с кнопкой копирования и статистикой."""
        if new_patient_dict is None:
            new_patient_dict = {}
        if last_fraction_dict is None:
            last_fraction_dict = {}
        if first_series_by_patient is None:
            first_series_by_patient = {}

        if hasattr(self, 'client_window') and self.client_window.winfo_exists():
            self.client_window.destroy()
            
        self.client_window = customtkinter.CTkToplevel(self)
        self.client_window.title(f"Клиенты: {status_key} ({series_key}) - {date_val}")
        self.client_window.geometry("1100x560") 
        self.client_window.resizable(False, False)
        self.client_window.lift()
        self.client_window.focus_force()
        self.client_window.attributes('-topmost', True) 
        self.client_window.after(100, lambda: self.client_window.attributes('-topmost', False))
        
        # ПАНЕЛЬ ИНСТРУМЕНТОВ (КНОПКА И СТАТИСТИКА)
        tool_frame = customtkinter.CTkFrame(self.client_window, fg_color="transparent")
        tool_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 0))
        
        # 1. Кнопка копирования
        copy_btn = customtkinter.CTkButton(
            tool_frame, 
            text="Копировать список (Фамилия И.О. (док))", 
            command=lambda: self.copy_clients_to_clipboard(
                data, date_val, new_patient_dict, last_fraction_dict),
            width=250,
            fg_color="#1f538d",
            hover_color="#14375e"
        )
        copy_btn.pack(side=tk.LEFT, padx=5)

        # 2. ПОДСЧЕТ СТАТИСТИКИ (XX/YY/ZZ)
        total_p = len(data)
        new_p = 0
        ending_p = 0
        
        for row in data:
            pid = row[6] # ID пациента (как в вашей логике отрисовки)
            if pid in new_patient_dict:
                new_p += 1
            if pid in last_fraction_dict:
                ending_p += 1
        
        stats_text = f"Кол-во пациентов: всего {total_p} / новых {new_p} / заканчивают {ending_p}"
        
        # 3. Элемент с текстом статистики
        stats_label = customtkinter.CTkLabel(
            tool_frame, 
            text=stats_text,
            font=("Helvetica", 14, "bold"),
            text_color="#AAAAAA" # Светло-серый цвет
        )
        stats_label.pack(side=tk.LEFT, padx=15)

        # 4. СУММАРНАЯ ЭКСПОЗИЦИЯ (Задача 12)
        # Делим на 10, так как в Teragis параметр 'SH' хранится в децисекундах (0.1 сек)
        raw_val = sum(float(row[8]) for row in data if row[8] is not None)
        total_seconds = raw_val / 10.0
        
        # Используем математическое округление для точности и возвращаем формат ЧЧ:ММ (Задача 15)
        total_minutes = round(total_seconds / 60)
        h = total_minutes // 60
        m = total_minutes % 60
        
        # РАСЧЕТ ВРЕМЕНИ ОПЕРАТОРА (Экспозиция + паузы между пациентами)
        # Применяется только для списка "Запланированных"
        operator_info = ""
        if status_key == 'Planned' and total_p > 0:
            # Паузы: (кол-во - 1) * 7 минут
            pauses_seconds = (total_p - 1) * 7 * 60
            op_total_seconds = total_seconds + pauses_seconds
            
            # Округляем время с укладкой также через round()
            op_total_minutes = round(op_total_seconds / 60)
            op_h = op_total_minutes // 60
            op_m = op_total_minutes % 60
            operator_info = f"  (с укладкой ~{op_h:02d}:{op_m:02d})"

        exposure_text = f"Сумма времени: {h:02d}:{m:02d}{operator_info}"
        
        exposure_label = customtkinter.CTkLabel(
            tool_frame,
            text=exposure_text,
            font=("Helvetica", 14, "bold"),
            text_color="#FFD700" # Золотистый цвет
        )
        exposure_label.pack(side=tk.LEFT, padx=15)

        # ОБЛАСТЬ ТАБЛИЦЫ 
        canvas_frame = tk.Frame(self.client_window, bg="#2E2E2E")
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.canvas_clients = tk.Canvas(canvas_frame, bg="#2E2E2E", highlightthickness=0)
        self.canvas_clients.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        v_scroll = tk.Scrollbar(canvas_frame, command=self.canvas_clients.yview)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas_clients.configure(yscrollcommand=v_scroll.set)
        
        # Вызов отрисовки таблицы (оставляем ваш текущий метод без изменений)
        self.draw_client_table(data, bg_color, new_patient_dict, last_fraction_dict,
                               first_series_by_patient)
        
        self._bind_mousewheel_scroll(self.canvas_clients, self.canvas_clients)

    def draw_client_table(self, data, parent_bg_color, new_patient_dict=None,
                          last_fraction_dict=None, first_series_by_patient=None):
        """Отрисовка таблицы клиентов с подсветкой дублей, новых пациентов и последних фракций."""
        if new_patient_dict is None:
            new_patient_dict = {}
        if last_fraction_dict is None:
            last_fraction_dict = {}
        if first_series_by_patient is None:
            first_series_by_patient = {}

        self.canvas_clients.delete("all")
        cw, rh = self.client_col_width, self.client_row_height
        font_head = ("Helvetica", self.client_header_font_size, "bold")
        font_cell = ("Helvetica", self.client_cell_font_size)
        
        cols = ["Фамилия", "Имя", "Серия", "Статус", "Коммент"]
        col_widths = [cw - 50, cw + 40, cw - 40, cw - 100, cw * 2] 
        total_width = sum(col_widths)
        
        # Подсчёт дублирующихся пациентов
        patient_ids_list = [row[6] for row in data]
        patient_counts = Counter(patient_ids_list)
        duplicate_patients = {pid for pid, count in patient_counts.items() if count > 1}
        
        if duplicate_patients:
            logger.info(f"[TPulse] В окне клиентов обнаружено {len(duplicate_patients)} пациентов с дублями")
        
        # Заголовки
        x_offset = 5
        for i, c in enumerate(cols):
            width = col_widths[i]
            x = x_offset
            self.canvas_clients.create_rectangle(x, 5, x + width, 5 + rh, fill="#444", outline="white")
            self.canvas_clients.create_text(x + width/2, 5 + rh/2, text=c, fill="white", font=font_head)
            x_offset += width
            
        # Данные
        for r, row in enumerate(data):
            pid = int(row[6]) if row[6] is not None else None
            sid = int(row[7]) if row[7] is not None else None
            status_id = row[3] 
            status_text = str(row[4])
            comment_text = str(row[5])
            
            display_data = [row[0], row[1], row[2], status_text, comment_text]

            # Является ли текущая серия первой для этого пациента
            first_sid = first_series_by_patient.get(pid)
            is_first_series = (sid is not None and first_sid is not None and sid == first_sid)

            # ОПРЕДЕЛЯЕМ БАЗОВЫЙ ЦВЕТ СТРОКИ 
            # Приоритет: дубль > последняя фракция > новый пациент > переданный bg
            if pid in duplicate_patients:
                row_base_color = self.COLOR_DUPLICATE_PATIENT
            elif pid in last_fraction_dict:
                row_base_color = (self.COLOR_LAST_FRACTION
                                  if is_first_series
                                  else self.COLOR_LAST_FRACTION_REPEAT)
            elif pid in new_patient_dict:
                row_base_color = (self.COLOR_NEW_PATIENT
                                  if is_first_series
                                  else self.COLOR_NEW_PATIENT_REPEAT)
            else:
                row_base_color = parent_bg_color

            x_offset = 5
            for c_idx, val in enumerate(display_data):
                width = col_widths[c_idx]
                x = x_offset
                y = 5 + (r + 1) * rh
                
                text_val = str(val) if val else ""
                anchor_pos = "center" 
                x_text = x + width/2
                
                cell_bg = row_base_color
                
                # Цвет статуса — переопределяет только колонку статуса (c_idx==3),
                # но НЕ если пациент является дублем (дубль важнее)
                if c_idx == 3 and pid not in duplicate_patients:
                    if status_id == self.STATUS_COMPLETED_ID:
                        cell_bg = self.status_colors_cfg['Completed']
                    elif status_id == self.STATUS_ON_TREATMENT_ID:
                        cell_bg = self.status_colors_cfg['On_Treatment']

                # Форматирование комментариев и ФИО
                if c_idx == 4:
                    anchor_pos = "w"
                    x_text = x + 5
                    full_comment = text_val
                    text_val = text_val.replace('\n', ' ').replace('\r', ' ')
                    if len(text_val) > self.limit_comment: 
                        text_val = text_val[:self.limit_comment] + "..."
                elif c_idx in [0, 1]:
                    anchor_pos = "w"
                    x_text = x + 5
                    if len(text_val) > self.limit_fio: 
                        text_val = text_val[:self.limit_fio] + "..."
                
                # Отрисовка ячейки
                rect = self.canvas_clients.create_rectangle(x, y, x+width, y+rh, 
                                                            fill=cell_bg, outline="white")
                text_item = self.canvas_clients.create_text(x_text, y+rh/2, text=text_val, 
                                                            fill="black", font=font_cell, anchor=anchor_pos)
                
                # Привязка событий
                
                # 1. ПКМ — Копирование фамилии пациента (Задача 14)
                surname = str(row[0]) if row[0] else ""
                self.canvas_clients.tag_bind(rect, "<Button-3>", 
                                            lambda e, s=surname: self.copy_to_clipboard_with_hint(s, e))
                self.canvas_clients.tag_bind(text_item, "<Button-3>", 
                                            lambda e, s=surname: self.copy_to_clipboard_with_hint(s, e))

                # 2. Двойной клик — Попап комментария или Календарь
                if c_idx == 4:
                    self.canvas_clients.tag_bind(rect, "<Double-1>", 
                                                lambda e, txt=full_comment: self.show_comment_popup(txt))
                    self.canvas_clients.tag_bind(text_item, "<Double-1>", 
                                                lambda e, txt=full_comment: self.show_comment_popup(txt))
                else:
                    self.canvas_clients.tag_bind(rect, "<Double-1>", 
                                                lambda e, p=pid: self.open_patient_calendar_window(p))
                    self.canvas_clients.tag_bind(text_item, "<Double-1>", 
                                                lambda e, p=pid: self.open_patient_calendar_window(p))
                
                x_offset += width

        self.canvas_clients.config(scrollregion=(0, 0, 5 + total_width + 5, 5 + (len(data)+1)*rh + 5))

    def format_patient_list_for_clipboard(self, data, new_patient_dict, last_fraction_dict) -> str:
            """
            Форматирует список пациентов для буфера обмена.
            Логика сокращения докторов: 1 отд - 3 симв, 2 отд - 4 симв.
            """
            if not data:
                return "Пациентов нет"

            # Собираем ID пациентов, чтобы узнать, какие планы у них были раньше
            patient_ids = list({int(row[6]) for row in data})
            placeholders_p = ','.join(['%s'] * len(patient_ids))
            
            # Запрос на самые первые серии в истории
            try:
                first_ever_raw = execute_query(
                    f"SELECT patient_id, MIN(series_id) FROM tseries WHERE patient_id IN ({placeholders_p}) GROUP BY patient_id",
                    tuple(patient_ids))
                self._set_db_status(True)
            except DatabaseError:
                self._set_db_status(False)
                first_ever_raw = []
                
            first_series_map = {int(r[0]): int(r[1]) for r in first_ever_raw} if isinstance(first_ever_raw, list) else {}

            lines = []
            for i, row in enumerate(data, 1):
                # Структура row: surname[0], forename[1], series_name[2], ..., note[5], pid[6], sid[7]
                surname = row[0]
                forename = row[1]
                series_name = str(row[2])
                note = row[5]
                pid = int(row[6])
                sid = int(row[7])

                # 1. Форматируем ФИО (ИВАНОВ И. П.)
                f_parts = forename.strip().split()
                inits = "".join([p[0].upper() + "." for p in f_parts if p])
                display_name = f"{surname.upper()} {inits}"

                # 2. Определяем эмоджи
                emoji = ""
                if pid in last_fraction_dict:
                    emoji = "🏁"
                elif pid in new_patient_dict:
                    # Если текущий sid больше самого первого sid в истории — это 2-й план
                    if sid > first_series_map.get(pid, sid):
                        emoji = "🆕②"
                    else:
                        emoji = "🆕"

                # 3. Сокращаем фамилию доктора (1 отд = 3, 2 отд = 4)
                limit = 4 if series_name.startswith('2') else 3
                doc_parts = (note or "").strip().split()
                if doc_parts:
                    doc_name = doc_parts[0].capitalize()
                    doc_display = f"({doc_name[:limit]})"
                else:
                    doc_display = "(-)"

                lines.append(f"{i}. {emoji}{display_name} {doc_display}")

            return "\n".join(lines)

    def copy_clients_to_clipboard(self, data, date_val=None,
                                  new_patient_dict=None, last_fraction_dict=None):
        """Копирует отформатированный список в буфер обмена в стиле Telegram."""
        
        # 1. Формируем заголовок в стиле ТГ
        if date_val:
            try:
                # Пытаемся распарсить дату
                d = self.parse_date_safe(str(date_val))
                if d:
                    # Используем внешнюю функцию get_formatted_date, которая добавит день недели
                    header = f"📝 Список на {get_formatted_date(d)}:\n"
                else:
                    header = f"📝 Список на {date_val}:\n"
            except Exception:
                header = f"📝 Список на {date_val}:\n"
        else:
            # Если даты нет, берем текущую
            header = f"📝 Список на {get_formatted_date()}:\n"

        # 2. Формируем тело списка
        body = self.format_patient_list_for_clipboard(data, new_patient_dict, last_fraction_dict)
        
        # 3. Подсчет и подвал
        total_p  = len(data)
        new_p    = len(new_patient_dict) if new_patient_dict else 0
        ending_p = len(last_fraction_dict) if last_fraction_dict else 0
        
        # Добавим небольшой разделитель перед подвалом для красоты, как в ТГ
        footer   = f"\nВсего: {total_p} / +{new_p} / -{ending_p}"
        
        text_to_copy = header + body + footer
        
        # 4. Копирование в буфер
        try:
            pyperclip.copy(text_to_copy)
        except Exception as e:
            logger.debug(f"[Clipboard] pyperclip error (full list): {e}")

        self.clipboard_clear()
        self.clipboard_append(text_to_copy)
        self.update()
        # logger.info("[TPulse] Список пациентов скопирован в буфер обмена") 

    def show_comment_popup(self, text):
        """Показ полного текста комментария в отдельном окне."""
        if not text: 
            return
            
        popup = customtkinter.CTkToplevel(self)
        popup.title("Комментарий")
        popup.geometry("400x300")
        popup.resizable(False, False)
        popup.lift()
        popup.focus_force()
        popup.attributes('-topmost', True)
        
        textbox = customtkinter.CTkTextbox(popup, wrap="word")
        textbox.pack(fill="both", expand=True, padx=10, pady=10)
        textbox.insert("0.0", text)
        textbox.configure(state="disabled")

    # КАЛЕНДАРЬ ПАЦИЕНТА 
    def open_patient_calendar_window(self, patient_id):
        """Открытие окна с календарём визитов пациента."""
 
        fio_query = "SELECT surname, forename FROM tpatient WHERE patient_id = %s"
        try:
            patient_info = execute_query(fio_query, (patient_id,))
            self._set_db_status(True)
        except DatabaseError:
            self._set_db_status(False)
            patient_info = []

        patient_fio = f"ID: {patient_id}"
        if isinstance(patient_info, list) and patient_info and patient_info[0]:
            patient_fio = f"{patient_info[0][0]} {patient_info[0][1]}"
 
        # Устанавливаем курсор ожидания
        self.config(cursor="wait")
        self.update_idletasks()

        # ИЗМЕНЕНИЕ 1: Оптимизированный запрос через CTE (значительно быстрее)
        query = '''
            WITH fraction_info AS (
                SELECT 
                    tfield.series_id,
                    tf.fraction_order,
                    TO_CHAR(MAX(tfp.insert_tms), 'HH24:MI:SS') as fact_time,
                    (ARRAY_AGG(tfs.name ORDER BY (CASE WHEN tfs.name IN ('OK', 'NORMAL') THEN 1 ELSE 0 END) ASC, tfp.insert_tms DESC))[1] as detail_status
                FROM tfraction tf
                JOIN tfraction_part tfp USING (fraction_id)
                JOIN tfraction_status tfs ON tfp.fraction_status_id = tfs.fraction_status_id
                JOIN tfield ON tf.field_id = tfield.field_id
                WHERE tfield.series_id IN (SELECT series_id FROM tseries WHERE patient_id = %s)
                GROUP BY tfield.series_id, tf.fraction_order
            )
            SELECT 
                tc.visitdate,
                tc.visittime,
                ts.name,                  -- Серия
                tcs.name,                 -- Статус (календарный)
                fi.fact_time,             -- Время (факт) из CTE
                tc.note,
                fi.detail_status          -- Детальный статус из CTE
            FROM tcalendar tc
            JOIN tseries ts ON tc.series_id = ts.series_id
            JOIN tcalendar_status tcs ON tc.calendar_status_id = tcs.calendar_status_id
            LEFT JOIN fraction_info fi ON tc.series_id = fi.series_id AND tc.fraction_order = fi.fraction_order
            WHERE ts.patient_id = %s
            ORDER BY tc.visitdate ASC, tc.visittime ASC;
        '''
 
        try:
            # Передаем patient_id дважды (для CTE и для основного фильтра)
            data = execute_query(query, (patient_id, patient_id))
            self._set_db_status(True)
        except DatabaseError:
            self._set_db_status(False)
            data = []
        finally:
            # Возвращаем обычный курсор
            self.config(cursor="")
 
        cal_win = customtkinter.CTkToplevel(self)
        cal_win.title(f"Календарь пациента: {patient_fio} (ID: {patient_id})")
        cal_win.geometry("900x400")
        cal_win.resizable(False, False)
        cal_win.after(10, cal_win.lift)
        cal_win.after(20, cal_win.focus_force)
        cal_win.attributes('-topmost', True)
        cal_win.after(150, lambda: cal_win.attributes('-topmost', False))
 
        frame = customtkinter.CTkScrollableFrame(cal_win)
        frame.pack(fill="both", expand=True)
 
        # ИЗМЕНЕНИЕ 2: добавлены заголовки
        headers = ["Дата", "Время", "Серия", "Статус", "Время (факт)", "Примечание"]
        col_widths = [90, 80, 130, 130, 110, 250]
 
        for i, h in enumerate(headers):
            customtkinter.CTkLabel(
                frame,
                text=h,
                font=("Arial", 12, "bold"),
                width=col_widths[i],
                anchor="w"
            ).grid(row=0, column=i, padx=5, sticky="w")
 
        if isinstance(data, list):
            for r, row in enumerate(data):
 
                clr = "white"
 
                status_txt = str(row[3]) if row[3] else ""
                detail_txt = str(row[6]) if (len(row) > 6 and row[6]) else ""
                
                full_status_txt = status_txt
                is_completed = any(x in status_txt.lower() for x in ["отлечено", "completed", "заверш"])
                
                # Список "нормальных" статусов, которые не считаются вмешательством
                normal_statuses = ["OK", "NORMAL"]
                
                # Если завершено и есть детальный статус, не входящий в список нормальных
                if is_completed and detail_txt and detail_txt.upper() not in normal_statuses:
                    full_status_txt = f"{status_txt} / {detail_txt}"
                    clr = "#FF8C00" # Темно-оранжевый
                elif is_completed:
                    clr = "green"
                elif any(x in status_txt.lower() for x in ["план", "planned", "inserted"]):
                    clr = "yellow"
                elif "отмена" in status_txt.lower():
                    clr = "red"
                elif any(x in status_txt.lower() for x in ["лечени", "on"]):
                    clr = "#00BFFF"

                # Отрисовка ячеек строки
                for c in range(len(headers)):
                    val = row[c]
                    txt = str(val) if val else ""
                    
                    # Если это колонка Статус (индекс 3), используем расширенный текст и спец. цвет
                    if c == 3:
                        txt = full_status_txt
                        fg = clr
                    else:
                        fg = "white"
 
                    customtkinter.CTkLabel(
                        frame,
                        text=txt,
                        text_color=fg,
                        width=col_widths[c],
                        anchor="w"
                    ).grid(row=r+1, column=c, padx=5, sticky="w")

        self._bind_mousewheel_scroll(frame, frame)

    def transliterate(self, text):
        """Прямая транслитерация кириллицы в латиницу по ГОСТ 7.79-2000 (Система Б)."""
        mapping = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'jo',
            'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'j', 'к': 'k', 'л': 'l', 'м': 'm',
            'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
            'ф': 'f', 'х': 'x', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'shh',
            'ъ': '"', 'ы': 'y', 'ь': '`', 'э': 'eh', 'ю': 'yu', 'я': 'ya',
            'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'Jo',
            'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'Й': 'J', 'К': 'K', 'Л': 'L', 'М': 'M',
            'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
            'Ф': 'F', 'Х': 'X', 'Ц': 'C', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Shh',
            'Ъ': '"', 'Ы': 'Y', 'Ь': '`', 'Э': 'Eh', 'Ю': 'Yu', 'Я': 'Ya'
        }
        return "".join(mapping.get(c, c) for c in text)

    def copy_to_clipboard_with_hint(self, text, event):
        """Копирует фамилию в буфер обмена стандартным методом (Задача 14)."""
        try:
            text_str = str(text).strip()
            is_shift = bool(event.state & 0x0001)
            
            if is_shift:
                text_str = self.transliterate(text_str)
                hint_msg = "Транслит скопирован"
            else:
                hint_msg = "Фамилия скопирована"

            # Копирование через pyperclip (наиболее надежный кроссплатформенный способ)
            try:
                pyperclip.copy(text_str)
            except Exception as e:
                logger.debug(f"[Clipboard] pyperclip error: {e}")

            # Дублируем через встроенный метод Tkinter для максимальной совместимости с VNC
            self.clipboard_clear()
            self.clipboard_append(text_str)
            self.update()

            self._show_temp_hint(hint_msg, event.x_root, event.y_root)
        except Exception as e:
            logger.error(f"[TPulse] Ошибка копирования: {e}")

    def _show_temp_hint(self, message, x, y):
        """Всплывающее окно-подсказка возле курсора."""
        hint = customtkinter.CTkToplevel(self)
        hint.overrideredirect(True)
        hint.attributes("-topmost", True)
        # Смещение на 15 пикселей от курсора
        hint.geometry(f"+{x+15}+{y+15}")
        hint.configure(fg_color="#333333")
        
        label = customtkinter.CTkLabel(
            hint, text=message, corner_radius=6, 
            fg_color="#333333", text_color="white",
            font=("Arial", 12, "bold"),
            padx=10, pady=5
        )
        label.pack()
        
        # Удаление подсказки через 1.2 сек
        hint.after(1200, hint.destroy)

    def on_closing(self):
        """Вызывается при закрытии главного окна с подтверждением."""
        text = (
            "Вы уверены, что хотите закрыть программу?\n\n"
            "• Для повторного запуска может потребоваться\n"
            "  длительное время!\n\n"
            "• Рассылка уведомлений о новых планах\n"
            "  в Telegram будет остановлена!"
        )
        if not messagebox.askokcancel("Подтверждение выхода", text):
            return

        try:
            # 1. Остановка бота
            if hasattr(self, 'tg_bot') and self.tg_bot:
                try:
                    self.tg_bot.stop(reason="программа закрыта")
                except Exception as e:
                    logger.error(f"[TPulse] Ошибка при остановке бота: {e}")

            # 2. Закрытие пула базы данных
            try:
                close_db_pool()
            except Exception as e:
                logger.error(f"[TPulse] Ошибка при закрытии пула БД: {e}")

            # 3. Остановка прокси через менеджер
            try:
                self.proxy_manager.stop()
            except:
                pass

            # 4. Завершение работы окна
            logger.info("########## >>> SYSTEM STOPPED: TeragisPulse закрыт <<< ##########")
            
            self.destroy() 
                
        except Exception as e:
            logger.critical(f"[TPulse] Критическая ошибка в on_closing: {e}")
            os._exit(0)

if __name__ == "__main__":
    try:
        app = App()
        
        # Здесь обычно запускаются ваши таймеры или потоки
        logger.info("""########## >>> SYSTEM READY: TeragisPulse запущен <<< ##########""")
        
        app.mainloop()
    except Exception as e:
        logger.critical(f"[TPulse] Критическая ошибка при запуске TeragisPulse: {e}") 