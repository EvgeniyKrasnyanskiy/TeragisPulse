# -*- coding: utf-8 -*-
"""
Teragis Notifier — АРМ-клиент уведомлений.
Полностью на стандартном tkinter (без customtkinter) для 100% совместимости
с любым масштабированием DPI в Windows и Linux.
"""
import os
import sys
import platform
import queue
import shutil
import subprocess
import time
import threading
import tkinter as tk
from tkinter import font as tkfont
import pystray
from PIL import Image, ImageDraw

try:
    import winsound
except ImportError:
    winsound = None


# Настройка логирования до импорта db_listener
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("db_notifier")

from db_listener import DBListener
from auto_discovery import log_debug

# Цветовая палитра (темная тема)
BG_CARD = "#1E1E1E"
BG_WINDOW = "#111111"
ACCENT_BLUE = "#1f538d"
TEXT_WHITE = "#FFFFFF"
TEXT_GREY = "#DDDDDD"
TEXT_MUTED = "#777777"
HOVER_DARK = "#333333"


def _detect_ui_font() -> str:
    """Определяет лучший UI-шрифт для текущей ОС."""
    system = platform.system()
    if system == "Windows":
        return "Segoe UI"
    elif system == "Darwin":
        return "Helvetica Neue"
    # Linux: «Sans» — универсальный алиас fontconfig,
    # автоматически разрешается в лучший sans-serif шрифт системы
    # (Ubuntu на Xubuntu, Noto Sans на Fedora, DejaVu Sans и т.д.)
    return "Sans"


UI_FONT = _detect_ui_font()


class NotificationCard(tk.Frame):
    """Виджет отдельной карточки уведомления (чистый tkinter)."""

    def __init__(self, master, fio: str, details: str, on_close_callback, **kwargs):
        super().__init__(
            master,
            bg=BG_CARD,
            highlightbackground=ACCENT_BLUE,
            highlightcolor=ACCENT_BLUE,
            highlightthickness=1,
            width=320,
            height=145,
            **kwargs
        )
        self.on_close_callback = on_close_callback
        self.pack_propagate(False)

        # Синяя полоска-индикатор слева
        tk.Frame(self, width=4, bg=ACCENT_BLUE).pack(side="left", fill="y", padx=(0, 8))

        # Контейнер для текста
        content = tk.Frame(self, bg=BG_CARD)
        content.pack(side="left", fill="both", expand=True, padx=(0, 28), pady=8)

        # Заголовок
        tk.Label(
            content, text="Пациент на лечении!",
            bg=BG_CARD, fg=ACCENT_BLUE,
            font=(UI_FONT, 9, "bold"), anchor="w"
        ).pack(fill="x")

        # ФИО
        tk.Label(
            content, text=f"👤 {fio}",
            bg=BG_CARD, fg=TEXT_WHITE,
            font=(UI_FONT, 11, "bold"), anchor="w"
        ).pack(fill="x", pady=(2, 0))

        # Детали (многострочный)
        tk.Label(
            content, text=details,
            bg=BG_CARD, fg=TEXT_GREY,
            font=(UI_FONT, 9), anchor="w", justify="left"
        ).pack(fill="x", pady=(2, 0))

        # Кнопка закрытия «×»
        close_btn = tk.Label(
            self, text="×",
            bg=BG_CARD, fg=TEXT_MUTED,
            font=(UI_FONT, 14, "bold"), cursor="hand2"
        )
        close_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-6, y=4)
        close_btn.bind("<Button-1>", lambda e: self.close())
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg=TEXT_WHITE))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg=TEXT_MUTED))

        # Автозакрытие через 5 минут
        self.timer_id = self.after(300_000, self.close)

    def close(self):
        """Немедленное удаление карточки."""
        try:
            self.after_cancel(self.timer_id)
        except (ValueError, AttributeError):
            pass
        self.on_close_callback(self)


class NotifierApp:
    """Основной класс АРМ-клиента (чистый tkinter.Tk для 100% совместимости с DPI)."""

    def __init__(self):
        log_debug("main.py: Инициализация NotifierApp...")

        # Главное окно — стандартный tkinter.Tk (без customtkinter!)
        self.root = tk.Tk()
        self.root.withdraw()  # Полностью скрыть до первого уведомления
        self.root.configure(bg=BG_WINDOW)

        # Размеры карточки и стека
        self.card_width = 320
        self.card_height = 145
        self.spacing = 8
        self.margin_x = 20
        self.margin_y = 60

        self.cards: list[NotificationCard] = []
        self.event_queue: queue.Queue = queue.Queue()

        # Трей
        self.tray_icon = None
        self._init_tray()

        # Прослушивание БД
        self.db_listener = DBListener(
            event_queue=self.event_queue,
            on_status_change=self._on_db_status_changed
        )
        self.db_listener.start()

        # Опрос очереди событий
        self.root.after(100, self._poll_events)
        log_debug("main.py: Инициализация NotifierApp завершена успешно")

    # --- Трей ---

    def _create_tray_image(self) -> Image.Image:
        """Генерирует изображение для иконки трея."""
        img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "tray_icon.png")
        if os.path.exists(img_path):
            try:
                return Image.open(img_path)
            except Exception:
                pass
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse([4, 4, 60, 60], outline=ACCENT_BLUE, width=5)
        draw.ellipse([16, 16, 48, 48], fill=ACCENT_BLUE)
        draw.ellipse([26, 26, 38, 38], fill="#4CAF50")
        try:
            os.makedirs(os.path.dirname(img_path), exist_ok=True)
            image.save(img_path)
        except Exception:
            pass
        return image

    def _init_tray(self):
        """Запускает системный трей в отдельном потоке."""
        log_debug("main.py: Запуск фонового трея...")

        def tray_worker():
            try:
                image = self._create_tray_image()
                menu = pystray.Menu(pystray.MenuItem('Выход', self.on_exit))
                self.tray_icon = pystray.Icon(
                    "TeragisNotifier", image,
                    "Teragis Notifier: Поиск сервера...", menu
                )
                self.tray_icon.run()
            except Exception as err:
                log_debug(f"main.py: Ошибка в потоке трея: {err}")

        threading.Thread(target=tray_worker, daemon=True).start()

    def _on_db_status_changed(self, is_connected: bool, status_msg: str):
        """Обновляет подсказку иконки трея."""
        if self.tray_icon:
            self.tray_icon.title = f"Teragis Notifier: {status_msg}"

    # --- Карточки уведомлений ---

    def add_notification(self, fio: str, details: str):
        """Добавляет новое уведомление сверху стека."""
        log_debug(f"main.py: Добавление уведомления для {fio}...")
        try:
            # Удалить самую старую, если превышен лимит
            if len(self.cards) >= 5:
                self.cards[-1].close()

            card = NotificationCard(
                self.root, fio=fio, details=details,
                on_close_callback=self._remove_card
            )
            self.cards.insert(0, card)
            log_debug("main.py: Карточка создана и добавлена в список")
            self._repack_cards()
        except Exception as e:
            log_debug(f"main.py: Ошибка в add_notification: {e}")

    def _remove_card(self, card: NotificationCard):
        """Удаляет карточку из стека."""
        log_debug("main.py: Удаление карточки...")
        try:
            if card in self.cards:
                self.cards.remove(card)
                card.destroy()
                self._repack_cards()
        except Exception as e:
            log_debug(f"main.py: Ошибка в _remove_card: {e}")

    def _repack_cards(self):
        """Перестраивает стек карточек и позиционирует окно в правом нижнем углу."""
        log_debug(f"main.py: Перепаковка карточек. Всего: {len(self.cards)}")
        try:
            for c in self.root.winfo_children():
                c.pack_forget()

            if not self.cards:
                log_debug("main.py: Нет карточек — скрываем окно")
                self.root.withdraw()
                return

            for i, card in enumerate(self.cards):
                pad_bottom = self.spacing if i < len(self.cards) - 1 else 0
                card.pack(fill="x", pady=(0, pad_bottom))

            num = len(self.cards)
            total_h = num * self.card_height + (num - 1) * self.spacing

            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = sw - self.card_width - self.margin_x
            y = sh - total_h - self.margin_y

            log_debug(f"main.py: Геометрия: {self.card_width}x{total_h}+{x}+{y}")

            self.root.overrideredirect(True)
            self.root.wm_attributes("-topmost", True)
            self.root.attributes("-alpha", 0.95)
            self.root.geometry(f"{self.card_width}x{total_h}+{x}+{y}")
            self.root.deiconify()
            self.root.update_idletasks()
            self.root.lift()
            self.root.wm_attributes("-topmost", True)
            log_debug("main.py: Окно отображено (deiconify + lift)")
        except Exception as e:
            log_debug(f"main.py: Ошибка в _repack_cards: {e}")

    # --- Звуковое оповещение ---

    def _play_notification_sound(self) -> None:
        """Воспроизводит короткий звуковой сигнал (кроссплатформенно)."""
        def _sound_worker():
            try:
                # Windows: winsound.Beep через системный спикер
                if winsound:
                    winsound.Beep(2000, 150)
                    return

                # Linux: воспроизводим системный звук уведомления
                if platform.system() == "Linux":
                    # Ищем подходящий аудио-плейер
                    player: str | None = None
                    for cmd in ("paplay", "pw-play", "aplay"):
                        if shutil.which(cmd):
                            player = cmd
                            break

                    if player:
                        # Стандартные звуки freedesktop (Ubuntu/Xubuntu)
                        for sound_file in (
                            "/usr/share/sounds/freedesktop/stereo/message.oga",
                            "/usr/share/sounds/freedesktop/stereo/bell.oga",
                            "/usr/share/sounds/freedesktop/stereo/complete.oga",
                        ):
                            if os.path.exists(sound_file):
                                subprocess.run(
                                    [player, sound_file],
                                    timeout=5, capture_output=True
                                )
                                return

                    # Фолбек: BEL-символ через терминал
                    subprocess.run(
                        ["bash", "-c", "printf '\\a'"],
                        timeout=2, capture_output=True
                    )
            except Exception:
                pass

        threading.Thread(target=_sound_worker, daemon=True).start()

    # --- Опрос очереди ---

    def _poll_events(self):
        """Проверяет очередь событий и выводит новые уведомления."""
        try:
            while True:
                event = self.event_queue.get_nowait()
                fio = event.get('fio')
                details = event.get('details')
                log_debug(f"main.py: Извлечено событие из очереди для FIO={fio}")
                self.add_notification(fio, details)
                self._play_notification_sound()
                self.event_queue.task_done()
        except queue.Empty:
            pass
        except Exception as e:
            log_debug(f"main.py: Ошибка при обработке очереди: {e}")

        self.root.after(100, self._poll_events)

    # --- Завершение ---

    def on_exit(self, icon=None, item=None):
        """Корректное завершение работы."""
        log_debug("main.py: Завершение работы приложения...")
        try:
            self.db_listener.stop()
        except Exception:
            pass
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        try:
            self.root.after(0, self.root.destroy)
        except Exception:
            pass

    def run(self):
        """Запускает главный цикл Tkinter."""
        self.root.mainloop()


if __name__ == '__main__':
    try:
        import locale
        locale.setlocale(locale.LC_ALL, '')
    except Exception:
        pass

    log_debug("main.py: Точка входа в приложение (main)")
    try:
        app = NotifierApp()
        app.run()
    except Exception as run_err:
        log_debug(f"main.py: Критический сбой: {run_err}")
