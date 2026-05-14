"""
tg_bot_ws.py — Telegram push-отправщик для TeragisPulse с обходом блокировки через TgWsProxy_windows.

Структура сообщения:
    📝 Список на среду (dd.mm):
    1. ...
    Всего: x / +y / -z из nn
    ─────────────────────
    [последнее событие]
    ─────────────────────
    ☢️ На аппарате: Фамилия И.О.
    🕐 Бот запущен в hh:mm
    🔄 Обновлено в hh:mm
"""

# -*- coding: utf-8 -*-
import os
import time
import html
import asyncio
import logging
import threading
from datetime import datetime
from telethon import TelegramClient, connection
from utils import get_formatted_date, get_time_with_date, format_name_short

# ИМПОРТ ИЗ ТВОЕГО ПРОЕКТА
try:
    from identification import identification_func
except ImportError:
    # Если файла нет в папке, бот продолжит работать, но без отправки новых серий
    identification_func = None
    
from tg_bot_ws_cmd import BotCommands

logger = logging.getLogger(__name__)
DIVIDER = "─────────────────────"

class TelegramBot:
    """
    Класс управления Telegram-ботом. 
    Работает в отдельном потоке со своим asyncio loop.
    """
    # Задержки переподключения: 15с, 30с, 60с, 120с, 300с, затем 300с бесконечно
    _RECONNECT_DELAYS = [15, 30, 60, 120, 300]

    def __init__(self, config, list_callback=None, status_callback=None, on_treatment_callback=None):
        # 1. Настройки из конфига и .env (приоритет .env)
        self._config = config
        self._token = os.getenv('TG_BOT_TOKEN', config.get('telegram', 'bot_token', fallback=''))
        self._admin_ids_raw = os.getenv('TG_ADMIN_IDS', config.get('telegram', 'admin_ids', fallback='0'))
        self._admin_ids = self._parse_admin_ids(self._admin_ids_raw)
        self._api_id = int(os.getenv('TG_API_ID', config.get('telegram', 'api_id', fallback='0')))
        self._api_hash = os.getenv('TG_API_HASH', config.get('telegram', 'api_hash', fallback=''))
        
        # 2. Настройки прокси
        self._proxy_host = os.getenv('PROXY_HOST', config.get('telegram', 'proxy_host', fallback='127.0.0.1'))
        self._proxy_port = int(os.getenv('PROXY_PORT', config.get('telegram', 'proxy_port', fallback='8888')))
        self._proxy_secret = os.getenv('PROXY_SECRET', config.get('telegram', 'proxy_secret', fallback=''))

        # 3. Каналы для рассылок
        self.channel_1ro = os.getenv('TG_CHANNEL_ID_1RO', config.get('telegram', 'channel_id_1ro', fallback='-1001876615218'))
        self.channel_2ro = os.getenv('TG_CHANNEL_ID_2RO', config.get('telegram', 'channel_id_2ro', fallback='-1001529879326'))
        
        # 4. Интервалы обновления (из config.ini)
        self._day_interval = config.getint('bot_intervals', 'day_update_sec', fallback=600)
        self._night_interval = config.getint('bot_intervals', 'night_update_sec', fallback=1800)

        # 4. Обратные вызовы (Callbacks) для связи с GUI и БД
        self.list_callback = list_callback
        self.status_callback = status_callback
        self.on_treatment_callback = on_treatment_callback

        # 5. Состояние и кэш
        self._last_msg_id = None
        self._last_text = ""
        self._last_event = ""
        self._current_on_treatment = "свободно"
        self._current_completed_count = 0
        self._current_shift_end = "—"
        self._date_lock = threading.Lock()
        # Восстанавливаем дату из файла при старте, чтобы не потерять смену дня
        self._last_msg_date = self._load_persisted_date()
        # Восстанавливаем ID последнего сообщения из файла при старте, чтобы его редактировать
        self._last_msg_id = self._load_persisted_id()
        
        # 6. Asyncio и Сеть
        self._loop = None
        self.client = None
        self._enabled = False
        self._started_at = None
        self._update_task = None
        self._last_was_empty = False
        self._connection_state = "stopped" # "connected" | "reconnecting" | "stopped"
        
        # 7. Подготавливаем объект команд (БЕЗ await здесь)
        self.commands = BotCommands(
            client=None, 
            admin_ids=self._admin_ids, 
            list_callback=self.list_callback,
            build_message_func=self._build_message,
            channel_1ro=self.channel_1ro,
            channel_2ro=self.channel_2ro,
            add_admin_callback=self.add_admin_id, # Для команды /add_admin
            config=self._config
        )

    # --- РАЗДЕЛ 1: УПРАВЛЕНИЕ ЗАПУСКОМ/ОСТАНОВКОЙ ---
    
    def start(self):
        """Публичный метод для запуска бота."""
        # 1. Если уже запущен — выходим
        if self._enabled: 
            return
            
        # 2. Сначала ставим флаг работы
        self._enabled = True
        self._started_at = datetime.now()
        self._last_event = "🟢 Рассылка (WebS) запущена"
        
        # 3. Проверка пациента перед запуском
        # Опрашиваем основной интерфейс, чтобы сразу подставить фамилию пациента на аппарате
        if self.on_treatment_callback:
            try:
                # Пытаемся получить имя через колбэк
                current_val = self.on_treatment_callback()
                if current_val:
                    self.set_on_treatment(current_val)
            except Exception as e:
                logger.error(f"[Bot_WS]: Ошибка пред-загрузки статуса аппарата: {e}")
        
        # 4. КРИТИЧНО: Сначала уведомляем GUI (меняем цвет кнопки), 
        # и только потом запускаем тяжелый поток
        if self.status_callback:
            self.status_callback(True)
            
        # 5. Запускаем поток, который будет подключаться к прокси и Telegram
        # Это происходит в фоне, поэтому GUI не "виснет"
        threading.Thread(target=self._run_in_thread, daemon=True).start()
        
    def toggle(self):
        """Переключатель состояния (Вкл/Выкл) для кнопки в GUI."""
        if self._enabled: 
            self.stop()
            # Даём системе 0.5 сек, чтобы поток реально завершился
            time.sleep(0.5) 
        else: 
            self.start()
            
        if self.status_callback: 
            self.status_callback(self._enabled)
        return self._enabled

    def stop(self, reason="вручную"):
        """Публичный метод для плановой остановки."""
        if not self._enabled:
            return
        
        self._enabled = False
        self._connection_state = "disconnected" 
        self._last_event = f"🔴 Рассылка (WebS) остановлена\n({reason})"
        
        loop = self._loop
        if loop and loop.is_running():
            stop_task = asyncio.run_coroutine_threadsafe(
                self._update_and_disconnect(), 
                loop
            )
            try:
                # Даем 5 секунд (было 3), чтобы завершить сетевые операции
                stop_task.result(timeout=5)
            except Exception as e:
                logger.warning(f"[Bot_WS]: Не успел чисто закрыться: {e}") 
        
        self._loop = None

    # --- РАЗДЕЛ 2: СЕТЕВАЯ ЛОГИКА (Asyncio) ---
    
    def _run_in_thread(self):
        """Точка входа в поток asyncio. 
        Создает НОВЫЙ цикл событий и держит его живым до явного stop()."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main_logic())
        except Exception as e:
            logger.error(f"[Bot_WS] Ошибка в цикле событий: {e}")
        finally:
            # Петля закрывается только здесь — после выхода из _main_logic,
            # а _main_logic выходит только когда self._enabled == False
            self._loop.close()
            self._loop = None
            
    async def _main_logic(self):
        """Главный цикл переподключения и поддержания связи."""
        # Принудительный перезапуск данных из файлов при каждом старте потока
        with self._date_lock:
            self._last_msg_id = self._load_persisted_id()
            self._last_msg_date = self._load_persisted_date()
        
        attempt = 0

        while self._enabled:
            self._set_state("reconnecting")

            # Логика ожидания перед повторной попыткой
            if attempt > 0:
                delay = self._RECONNECT_DELAYS[min(attempt - 1, len(self._RECONNECT_DELAYS) - 1)]
                logger.warning(f"[Bot_WS]: Переподключение через {delay} сек (попытка #{attempt})...")
                for _ in range(delay):
                    if not self._enabled:
                        return
                    await asyncio.sleep(1)

            if not self._enabled:
                return

            try:
                # Если старый клиент остался, пробуем его закрыть окончательно
                if self.client:
                    try:
                        await self.client.disconnect()
                    except:
                        pass
                    self.client = None
                
                # logger.info(f"[Bot_WS] Инициализация Telethon (Proxy: {self._proxy_host}:{self._proxy_port})")
                # Создание нового сеанса (MTProxy через WebSocket)
                self.client = TelegramClient(
                    'bot_session', 
                    self._api_id, 
                    self._api_hash,
                    connection=connection.ConnectionTcpMTProxyIntermediate,
                    proxy=(self._proxy_host, self._proxy_port, self._proxy_secret),
                    timeout=20,
                    connection_retries=None, # Бесконечные попытки при обрыве сокета
                    auto_reconnect=True,     # Автоматическое восстановление
                    retry_delay=5            # Пауза между попытками
                )

                # logger.info("[Bot_WS] Попытка авторизации в Telegram")
                await self.client.connect()
                if not await self.client.is_user_authorized():
                    await self.client.sign_in(bot_token=self._token)
                
                # Здесь инициализируем команды
                self.commands.client = self.client
                await self.commands.init_commands()
                
                attempt = 0
                self._set_state("connected")
                # logger.info("[Bot_WS]: Успешно подключено по WebSocket!")
                
                # 1. Сразу принудительно обновляем данные
                await self._update_cycle(force=True)
                
                # 2. Запускаем фоновую задачу периодических обновлений (если не запущена)
                if not self._update_task or self._update_task.done():
                    self._update_task = asyncio.create_task(self._periodic_update_loop())

                # 3. Внутренний цикл: просто следим за тем, что сокет жив
                while self._enabled:
                    try:
                        # Легкая проверка связи
                        await asyncio.wait_for(self.client.get_me(), timeout=10)
                        await asyncio.sleep(60) 
                    except Exception as ping_err:
                        logger.warning(f"[Bot_WS]: Связь потеряна: {ping_err}")
                        break
                
            except Exception as e:
                attempt += 1
                logger.error(f"[Bot_WS] Telethon (попытка #{attempt}): {e}")
            finally:
                # Чистим за собой при любом выходе
                if self.client:
                    try:
                        await self.client.disconnect()
                    except:
                        pass
                    self.client = None

        self._set_state("stopped")    

    async def _periodic_update_loop(self):
        """Отдельная задача: обновляет данные в ТГ раз в XXX минут."""
        while self._enabled:
            success = False
            try:
                # Обновляем только если есть соединение
                if self._connection_state == "connected" and self.client:
                    await self._update_cycle()
                    success = True # Отмечаем, что обновление прошло успешно
            except Exception as e:
                logger.error(f"[Bot_WS] Ошибка в фоновом цикле обновления: {e}")
            
            # --- ЛОГИКА АДАПТИВНОЙ ПАУЗЫ ---
            if not success and self._enabled:
                # Если не обновились (нет сети), проверяем чаще — раз в 30 секунд
                await asyncio.sleep(30)
                continue # Идем на новую попытку обновления

            now = datetime.now()
            if 0 <= now.hour < 8:
                wait_time = self._night_interval # Ночь
            else:
                wait_time = self._day_interval   # День
                
            await asyncio.sleep(wait_time)
            
    # --- РАЗДЕЛ 3: ОСНОВНОЙ ЦИКЛ ОБНОВЛЕНИЯ ТЕКСТА ---  

    async def _update_cycle(self, force=False):
        """Основной цикл обновления данных в Telegram."""
        if not self._enabled and not force:
            return
        
        # Восстановление данных, если память пуста
        self._last_msg_id = self._last_msg_id or self._load_persisted_id()
        self._last_msg_date = self._last_msg_date or self._load_persisted_date()
        
        try:
            # 1. Сбор данных (SQL запрос в отдельном потоке, чтобы не фризить боту сеть)
            # Теперь SQL-запрос выполняется в фоне, не мешая асинхронному циклу
            if self.list_callback:
                raw_data = await asyncio.to_thread(self.list_callback)
            else:
                raw_data = ""
            
            data = raw_data if isinstance(raw_data, dict) else {'list_text': raw_data}
            list_text = data.get('list_text', '').strip()
            
            # ЛОГИКА: ПРОВЕРКА ПУСТОГО СПИСКА
            is_empty = not list_text or "Пациентов нет" in list_text
            
            if is_empty:
                data['list_text'] = f"📝 Список на {get_formatted_date()}:\nПациентов нет"
                # Если мы уже один раз написали, что пациентов нет, и это не принудительный запуск (force), 
                # и не новый день — просто выходим.
                if hasattr(self, '_last_was_empty') and self._last_was_empty and not force:
                    # Проверяем, не сменился ли день, прежде чем выйти
                    current_date = datetime.now().strftime("%d.%m").strip()
                    if self._last_msg_date == current_date:
                        return 
                self._last_was_empty = True
            else:
                self._last_was_empty = False

            # 2. Формируем текст сообщения
            new_text = self._build_message(data)

            # 3. Проверка смены даты с защитой от одновременного доступа 
            current_date = datetime.now().strftime("%d.%m").strip()
            
            is_new_day = False
            with self._date_lock:
                # Если дата в памяти есть И она не совпадает с текущей — это реальная смена дня
                if self._last_msg_date is not None and self._last_msg_date != current_date:
                    self._last_msg_id = None # Сбрасываем ID сообщения, чтобы создать новое сообщение
                    is_new_day = True
                    logger.info(f"[Bot_WS] Смена дня: {self._last_msg_date} -> {current_date}. ID сброшен.")

                # Если даты вообще нет в памяти или она старая — обновляем дату
                if self._last_msg_date != current_date:
                    self._last_msg_date = current_date
                    self._save_persisted_date(current_date)

            # 4. Анти-спам
            if new_text == self._last_text and not force and not is_new_day:
                return

            # 5. Отправка или редактирование
            chat_id = int(self._admin_ids[0]) if self._admin_ids else 0
            if self._last_msg_id is None:
                # Отправляем новое, если ID нет (новый день или первый запуск без файла)
                msg = await self.client.send_message(chat_id, new_text, parse_mode='html')
                self._last_msg_id = msg.id
                self._save_persisted_id(self._last_msg_id)
                # print(f"[INFO][Bot_WS] Отправлено первое сообщение дня: {self._last_msg_id}")
                logger.info(f"[Bot_WS] Отправлено первое сообщение дня: {self._last_msg_id}")
            else:
                # Редактируем старое
                try:
                    await self.client.edit_message(chat_id, self._last_msg_id, new_text, parse_mode='html')
                    # print(f"[Bot_WS] Сообщение {self._last_msg_id} обновлено") # Лог в консоль
                except Exception as e:
                    err_str = str(e).lower()
                    if "message_id_invalid" in err_str or "not found" in err_str:
                        # Если сообщение удалили в чате — создаем заново
                        # logger.warning(f"[Bot_WS]: Старое сообщение потеряно, создаю новое. Ошибка: {e}")
                        msg = await self.client.send_message(chat_id, new_text, parse_mode='html')
                        self._last_msg_id = msg.id
                        self._save_persisted_id(self._last_msg_id)
                        # print(f"[Bot_WS] Сообщение было удалено, создано новое: {self._last_msg_id}")
                    else:
                        # print(f"[Bot_WS] [{datetime.now().strftime('%H:%M:%S')}] Ошибка связи: {e}. Жду восстановления...")
                        return

            self._last_text = new_text
                
        except Exception as e:
            logger.error(f"[Bot_WS] Непредвиденная ошибка в основном цикле обновления данных в Telegram: {e}")

    # --- РАЗДЕЛ 4: ВНЕШНИЕ СОБЫТИЯ (Пациент и БД) ---

    def _build_message(self, data):
        """Собирает блоки текста в одно HTML-сообщение."""
        
        body = data.get('list_text', '') 
        new_event = data.get('last_event')
        
        if new_event and new_event.strip(): 
            self._last_event = new_event

        # 1. Текущий статус уже сокращен через set_on_treatment
        on_treatment = self._current_on_treatment
        
        # 2. Экранируем спецсимволы, чтобы бот не "падал"
        safe_name = html.escape(str(on_treatment))

        # 3. Формируем временные метки
        started_str = self._started_at.strftime('%H:%M (%d.%m)') if self._started_at else "—"
        updated_str = get_time_with_date()

        # 4. Собираем блоки
        block1 = body if body else f"📝 Список на {get_formatted_date()}:\nПациентов нет"
        block2 = self._last_event
        
        # Формируем подвал (на аппарате будет жирным)
        block3 = (f"☢️ На аппарате: <b>{safe_name}</b>\n"
                  f"✅ Отлечено: <b>{self._current_completed_count}</b>\n"
                  f"🏁 Конец смены в: <b>{self._current_shift_end}</b>\n"
                  f"🕐 Бот запущен в {started_str}\n"
                  f"🔄 Обновлено в {updated_str}")

        parts = [block1, DIVIDER]
        if block2 and block2.strip():
            parts.append(block2)
            parts.append(DIVIDER)
            
        parts.append(block3)
        return "\n".join(parts)

    def set_on_treatment(self, name_text):
        """
        Устанавливает текст для строки 'На аппарате'.
        Автоматически приводит ФИО к формату 'Фамилия И.О.'
        """
        # 1. Проверяем на пустоту и приводим к строке
        raw_name = str(name_text).strip() if name_text else ""
        
        if not raw_name or raw_name.lower() == "свободно":
            target_text = "свободно"
        else:
            # 2. Форматируем (сокращаем до И.О. + добавляем доктора, если он там был)
            # Используем твою функцию из utils.py
            target_text = format_name_short(raw_name)

        # 3. Сравниваем: если текст реально изменился — обновляем ТГ
        if self._current_on_treatment != target_text:
            self._current_on_treatment = target_text
            
            # Проверяем, что бот в сети, прежде чем слать задачу в поток
            if self._enabled and self._loop and self._connection_state == "connected":
                try:
                    # Отправляем задачу в работающий цикл asyncio
                    asyncio.run_coroutine_threadsafe(self._update_cycle(), self._loop)
                except RuntimeError:
                    pass # Цикл событий уже остановлен, обновление не требуется

    def set_shift_end(self, time_str):
        """Устанавливает время окончания смены для вывода в Telegram."""
        # logger.debug(f"[Bot_WS] Установка времени окончания: {time_str}")
        if self._current_shift_end != time_str:
            self._current_shift_end = time_str
            # Обновление сообщения теперь происходит только при смене пациента, 
            # изменении списка или по таймеру (мягкая интеграция).

    def set_completed_count(self, count):
        """Устанавливает количество отлеченных пациентов."""
        if self._current_completed_count != count:
            self._current_completed_count = count
            if self._enabled and self._loop and self._connection_state == "connected":
                try:
                    asyncio.run_coroutine_threadsafe(self._update_cycle(), self._loop)
                except RuntimeError:
                    pass


    def _parse_admin_ids(self, raw_str) -> list:
        """Парсит строку с ID админов, добавляя тех, что сохранены в файле."""
        ids = [s.strip() for s in str(raw_str).split(',') if s.strip()]
        
        # Загружаем из доп. файла (динамически добавленные)
        persisted = self._load_trusted_users()
        for pid in persisted:
            if pid not in ids:
                ids.append(pid)
        return ids

    def add_admin_id(self, new_id):
        """Добавляет новый ID в список доверенных и сохраняет в файл."""
        new_id = str(new_id).strip()
        if new_id not in self._admin_ids:
            self._admin_ids.append(new_id)
            self._save_trusted_users(self._admin_ids)
            # Обновляем список в объекте команд
            if hasattr(self, 'commands'):
                self.commands.admin_ids = self._admin_ids
            return True
        return False

    def _load_trusted_users(self) -> list:
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".trusted_users")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return [line.strip() for line in f if line.strip()]
        except Exception as e:
            logger.error(f"[Bot_WS] Ошибка чтения доверенных пользователей: {e}")
        return []

    def _save_trusted_users(self, ids_list):
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".trusted_users")
            # Сохраняем только те, которых нет в базовом конфиге/env, 
            # но для простоты сохраним всё, что было добавлено сверх базового
            with open(path, "w", encoding="utf-8") as f:
                # Фильтруем те, что изначальные (чтобы не дублировать)
                base_ids = [s.strip() for s in str(self._admin_ids_raw).split(',') if s.strip()]
                for uid in ids_list:
                    if uid not in base_ids:
                        f.write(f"{uid}\n")
        except Exception as e:
            logger.error(f"[Bot_WS] Ошибка сохранения доверенных пользователей: {e}")

    def _set_state(self, state: str):
        """
        Обновляет внутреннее состояние соединения и уведомляет GUI.
        (state: "connected" | "reconnecting" | "stopped")
        """
        self._connection_state = state
        if self.status_callback:
            self.status_callback(state)

    def trigger_force_update(self):
        """Мгновенно запускает цикл обновления вне расписания."""
        if self._enabled and self._loop and self._connection_state == "connected":
            try:
                asyncio.run_coroutine_threadsafe(self._update_cycle(), self._loop)
            except RuntimeError as e: 
                logger.error(f"[Bot_WS]: Ошибка принудительного обновления: {e}")
                pass

    async def _update_and_disconnect(self):
        """Эта корутина выполняется в потоке asyncio. Финальный аккорд перед выключением."""
        try:
            # Принудительно вызываем цикл обновления
            await self._update_cycle(force=True) # Чтобы ушло "Рассылка остановлена"
            # Даем крошечную паузу, чтобы пакет ушел из буфера сетевой карты
            await asyncio.sleep(0.2) 
            if self.client:
                await self.client.disconnect()
        except Exception as e:
            logger.error(f"[Bot_WS]: Сетевая ошибка при попытке отправить финальный статус: {e}")

# --- РАЗДЕЛ 5: СОХРАНЕНИЕ СОСТОЯНИЯ (ФАЙЛЫ) ---

    def _load_persisted_date(self) -> str | None:
        """Читает дату последнего сообщения. Очищает от пробелов."""
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tg_last_date")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    val = f.read().strip()
                    if val:
                        return val
            else:
                logger.warning(f"[Bot_WS] Файл даты не найден.")
        except Exception as e:
            logger.error(f"[Bot_WS] Ошибка чтения даты: {e}")
        return None

    def _save_persisted_date(self, date_str: str):
        """Сохраняет дату в файл."""
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tg_last_date")
            with open(path, "w", encoding="utf-8") as f:
                f.write(date_str.strip())
        except Exception as e:
            logger.error(f"[Bot_WS] Ошибка сохранения даты: {e}")

    def _load_persisted_id(self) -> int | None:
        """Читает ID сообщения из файла."""
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tg_last_id")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content and content.isdigit(): # Проверка, что там число
                        val = int(content)
                        return val
        except Exception as e:
            logger.error(f"[Bot_WS] Ошибка чтения ID: {e}")
        return None

    def _save_persisted_id(self, msg_id: int):
        """ВАЖНО: Сохраняет ID последнего сообщения в файл."""
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tg_last_id")
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(msg_id))
        except Exception as e:
            logger.error(f"[Bot_WS] Ошибка сохранения ID: {e}")

    # --- РАЗДЕЛ 6: ОТПРАВКИ НОВЫХ СЕРИЙ В КАНАЛЫ ОТДЕЛЕНИЙ ---

    async def _async_db_notification_send(self, msg_text, office, plan_name):
        """Логика маршрутизации уведомлений."""
        try:
            # Превращаем ID из конфига в числа сразу
            ch1 = int(self.channel_1ro.strip())
            ch2 = int(self.channel_2ro.strip())
            
            target_chat = ch1
            
            if plan_name.lower().startswith('test'):
                target_chat = int(self._config.get('telegram', 'channel_id_test', fallback='-1001925265025'))
            elif office == '1':
                target_chat = ch1
            elif office == '2':
                target_chat = ch2
            elif office == '0':
                if plan_name.startswith('2'):
                    target_chat = ch2
                elif plan_name.startswith('1'):
                    target_chat = ch1
                else:
                    await self.client.send_message(ch1, msg_text, parse_mode='html')
                    target_chat = ch2

            # Отправка
            await self.client.send_message(target_chat, msg_text, parse_mode='html')
            
        except Exception as e:
            logger.error(f"[Bot_WS] Ошибка отправки сообщения в группу: {e}")
            
    def send_notification_from_db(self, msg_text, office, plan_name=""):
        """
        Основной метод. Вызывается из App (через LISTEN/NOTIFY БД).
        Принимает уже готовый, красиво оформленный текст.
        """
        if self._enabled and self._loop and self.client and self._connection_state == "connected":
            try:
                asyncio.run_coroutine_threadsafe(
                    self._async_db_notification_send(msg_text, office, plan_name), 
                    self._loop
                )
            except RuntimeError:
                pass  # Петля закрыта — игнорируем, переподключение уже идёт