"""
tg_bot.py — Telegram push-отправщик для TeragisPulse с обходом блокировки через Xray.

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
import requests
import threading
import logging
import subprocess
import os
import time
from datetime import datetime, timedelta
from utils import get_formatted_date, get_time_with_date

logger = logging.getLogger(__name__)

SEND_TIMEOUT = 15
MAX_FAILURES = 5
DIVIDER = "─────────────────────"

PROXIES = {
    'https': 'socks5h://127.0.0.1:1080',
    'http': 'socks5h://127.0.0.1:1080'
}

class TelegramBot:
    def __init__(self, config, list_callback=None, status_callback=None, on_treatment_callback=None):
        self._token = os.getenv('TG_BOT_TOKEN', config.get('telegram', 'bot_token', fallback='')).strip()
        raw_ids = os.getenv('TG_ADMIN_IDS', config.get('telegram', 'admin_ids', fallback=''))
        self._admin_ids = [int(x.strip()) for x in raw_ids.split(',') if x.strip().lstrip('-').isdigit()]

        self._interval_sec = config.getint('telegram', 'interval_seconds', fallback=0) or \
                            config.getint('telegram', 'interval_minutes', fallback=5) * 60
        
        # Сохраняем БЕЗ подчеркивания, чтобы совпадало с вызовами в start/stop
        self.list_callback = list_callback
        self.status_callback = status_callback
        self.on_treatment_callback = on_treatment_callback

        self._running = False
        self._enabled = False 
        self._thread = None
        self._stop_evt = threading.Event()

        self._list_message_ids = {}
        self._last_event = ""
        self._last_list_text = None
        self._started_at = None
        self._current_on_treatment = "свободно"
        self._current_completed_count = 0
        self._current_shift_end = "—"
        
        self._last_msg_date = None  # Хранит дату последнего отправленного сообщения

        # Пути Xray
        curr_dir = os.path.dirname(os.path.abspath(__file__))
        self._xray_exe = os.path.join(curr_dir, "xray", "xray.exe")
        self._xray_config = os.path.join(curr_dir, "xray", "config.json")
        self._proxy_process = None

    def set_on_treatment(self, patient_name):
        """Метод-приемник данных из GUI."""
        self._current_on_treatment = patient_name if patient_name else "свободно"

    def set_shift_end(self, time_str):
        """Устанавливает время окончания смены."""
        self._current_shift_end = time_str if time_str else "—"

    def set_completed_count(self, count):
        """Устанавливает количество отлеченных пациентов."""
        self._current_completed_count = count

    def trigger_force_update(self):
        """Для совместимости: в HTTP версии цикл и так крутится, доп. логика не нужна."""
        pass

    def start(self):
        if self._running: return
        if not self._token or not self._admin_ids:
            logger.warning("TelegramBot: Недостаточно данных для запуска (token/ids)")
            return

        self._running = True
        self._enabled = True
        self._stop_evt.clear()
        self._started_at = datetime.now()
        self._last_event = "🟢 Рассылка (HTTP) запущена"

        # Теперь ошибки не будет
        if self.status_callback:
            self.status_callback(True)

        self._thread = threading.Thread(target=self._send_loop, daemon=True, name="TGBot_HTTP")
        self._thread.start()

    def toggle(self):
        """Метод для переключения состояния из GUI."""
        if self._running:
            self.stop()
        else:
            self.start()
        return self._running
        
    def stop(self, reason="вручную"):
        self._running = False
        self._enabled = False
        self._stop_evt.set()
        self._last_event = f"🔴 Рассылка (HTTP) остановлена\n({reason})"
        
        # Уведомляем GUI об остановке
        if self.status_callback:
            self.status_callback(False)

        # Отправляем финальный статус в ТГ
        try:
            self._push_update(list_text=self._last_list_text)
        except:
            pass

        if self._proxy_process:
            try: 
                self._proxy_process.terminate()
            except: 
                pass

    def _build_message(self, list_text, on_treatment):
        """Собирает сообщение с разделителями."""
        # Блок 1: Список
        block1 = list_text if list_text else f"📝 Список на {get_formatted_date()}:\nПациентов нет"
        
        # Блок 2: Событие (если пусто — можно ставить пробел или невидимый символ, 
        # но лучше просто всегда выводить разделитель и событие, если оно есть)
        block2 = self._last_event
        
        # Блок 3: Инфо
        if self.on_treatment_callback:
            try:
                fresh_status = self.on_treatment_callback()
                if fresh_status:
                    self.set_on_treatment(fresh_status)
            except Exception as e:
                logger.error(f"[Bot_HTTP]: Ошибка актуализации статуса: {e}")

        on_treatment = self._current_on_treatment
        started_str = self._started_at.strftime('%H:%M (%d.%m)') if self._started_at else "—"
        block3 = (f"☢️ На аппарате: {on_treatment}\n"
                  f"✅ Отлечено: {self._current_completed_count}\n"
                  f"🏁 Конец смены: {self._current_shift_end}\n"
                  f"🕐 Бот запущен в {started_str}\n"
                  f"🔄 Обновлено в {get_time_with_date()}")
        
        # Собираем части. Чтобы разделители не пропадали, 
        # используем фиксированную структуру.
        parts = []
        parts.append(block1)
        parts.append(DIVIDER)
        
        if block2 and block2.strip():
            parts.append(block2)
            parts.append(DIVIDER)
        
        parts.append(block3)
        
        # Используем два переноса строки для четкого разделения блоков
        return "\n".join(parts)

    def _push_update(self, list_text):
        # 1. Определяем текущую дату
        current_date = datetime.now().strftime("%d.%m")
        
        # Если пришел None, берем последний удачный текст
        if list_text is None:
            list_text = self._last_list_text
        else:
            self._last_list_text = list_text

        on_treatment = self._current_on_treatment
        full_text = self._build_message(list_text, on_treatment)
        
        for uid in self._admin_ids:
            mid = self._list_message_ids.get(uid)
            try:
                # ГЛАВНОЕ УСЛОВИЕ: 
                # Если сообщение есть И дата совпадает — редактируем.
                # Иначе (даты нет, она другая или сообщения нет) — шлем новое.
                if mid and self._last_msg_date == current_date:
                    if not self._edit(uid, mid, full_text):
                        new_mid = self._send(uid, full_text)
                        if new_mid: 
                            self._list_message_ids[uid] = new_mid
                else:
                    new_mid = self._send(uid, full_text)
                    if new_mid: 
                        self._list_message_ids[uid] = new_mid
            except Exception as e:
                logger.error(f"TelegramBot: Ошибка пуша для {uid}: {e}")
        
        # Запоминаем дату после успешной итерации
        self._last_msg_date = current_date

    def _send_loop(self):
        """Эта функция выполняется в отдельном потоке."""
        # 1. Запуск прокси внутри потока
        if os.path.exists(self._xray_exe):
            try:
                self._proxy_process = subprocess.Popen(
                    [self._xray_exe, "-c", self._xray_config],
                    cwd=os.path.dirname(self._xray_exe),
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                time.sleep(1.5) 
            except Exception as e:
                logger.error(f"TelegramBot: Ошибка прокси: {e}")

        # 2. Инициализация переменных
        last_text = None
        last_on_treatment = None
        last_completed_count = None
        first_run = True

        # 3. Основной цикл
        while self._running:
            try:
                # Получаем свежие данные из GUI через колбэк
                raw_data = self.list_callback()
                
                if isinstance(raw_data, dict):
                    current_text = raw_data.get('list_text', '')
                    gui_event = raw_data.get('last_event', '')
                    
                    # ПРАВКА ТУТ: Обновляем событие, только если оно реально ПРИШЛО (не пустое)
                    # Это гарантирует, что "Рассылка запущена" не исчезнет просто так
                    if gui_event and gui_event.strip():
                        self._last_event = gui_event
                else:
                    current_text = raw_data

                current_on_treatment = self._current_on_treatment
                current_completed_count = self._current_completed_count
                
                # 4. Условие отправки: 
                if (first_run or current_text != last_text or 
                    current_on_treatment != last_on_treatment or 
                    current_completed_count != last_completed_count):
                    self._last_list_text = current_text
                    # Вызываем push_update. Важно: внутри push_update 
                    # должен вызываться _build_message, который мы правили выше!
                    self._push_update(current_text)
                    
                    last_text = current_text
                    last_on_treatment = current_on_treatment
                    last_completed_count = current_completed_count
                
                first_run = False

            except Exception as e:
                logger.error(f"TelegramBot Loop Error: {e}")

            # Ожидание следующей итерации или сигнала об остановке
            if self._stop_evt.wait(timeout=self._interval_sec): 
                break

    def _send(self, chat_id, text):
        try:
            r = requests.post(f"https://api.telegram.org/bot{self._token}/sendMessage", 
                              json={"chat_id": chat_id, "text": text}, proxies=PROXIES, timeout=SEND_TIMEOUT)
            return r.json()['result']['message_id'] if r.json().get('ok') else None
        except: return None

    def _edit(self, chat_id, message_id, text):
        try:
            r = requests.post(f"https://api.telegram.org/bot{self._token}/editMessageText", 
                              json={"chat_id": chat_id, "message_id": message_id, "text": text}, proxies=PROXIES, timeout=SEND_TIMEOUT)
            return r.json().get('ok') or 'not modified' in r.json().get('description', '').lower()
        except: return False

    def _set_status(self, run):
        if self.status_callback: 
            self.status_callback(run)