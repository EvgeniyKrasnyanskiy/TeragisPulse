# -*- coding: utf-8 -*-
"""
Teragis Notifier — АРМ-клиент уведомлений.
Полностью на стандартном tkinter (без customtkinter) для 100% совместимости
с любым масштабированием DPI в Windows и Linux.
"""
import os
import sys
import socket

# Защита от запуска нескольких копий приложения (Single Instance Lock)
try:
    _singleton_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _singleton_socket.bind(('127.0.0.1', 47285))
except socket.error:
    sys.exit(0)

import platform
import queue
import shutil
import subprocess
import time
import threading
import re
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

is_win = os.name == 'nt'

try:
    import winsound
except ImportError:
    winsound = None


def clean_emojis(text: str) -> str:
    """Удаляет Unicode-эмодзи и специальные графические символы на Linux для стабильности на старых X11."""
    if not text or is_win:
        return text
    try:
        lines = text.split('\n')
        cleaned_lines = []
        for line in lines:
            # Оставляем только символы из базовой многоязычной плоскости (BMP, <= 0xFFFF)
            chars = []
            for char in line:
                code = ord(char)
                if code > 0xFFFF:
                    continue
                if 0xD800 <= code <= 0xDFFF:
                    continue
                chars.append(char)
            line_bmp = "".join(chars)
            
            # Удаляем спецсимволы значков в пределах BMP (радиация, карандаши, стрелки и т.д.)
            pattern = re.compile(
                r'[\u25a0-\u27bf]|[\u2b50]|[\u2300-\u23ff]',
                re.UNICODE
            )
            cleaned_line = pattern.sub('', line_bmp)
            cleaned_lines.append(" ".join(cleaned_line.split()))
        return "\n".join(cleaned_lines)
    except Exception:
        return text


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
from auto_discovery import log_debug, mask_fio

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

    def __init__(self, master, fio: str, details: str, on_close_callback, on_settings_callback, width=400, **kwargs):
        super().__init__(
            master,
            bg=BG_CARD,
            highlightbackground=ACCENT_BLUE,
            highlightcolor=ACCENT_BLUE,
            highlightthickness=1,
            width=width,
            height=145,
            **kwargs
        )
        self.on_close_callback = on_close_callback
        self.on_settings_callback = on_settings_callback
        self._closed = False
        self.pack_propagate(False)

        # Синяя полоска-индикатор слева
        tk.Frame(self, width=4, bg=ACCENT_BLUE).pack(side="left", fill="y", padx=(0, 8))

        # Контейнер для текста
        content = tk.Frame(self, bg=BG_CARD)
        content.pack(side="left", fill="both", expand=True, padx=(0, 28), pady=8)

        # Заголовок
        tk.Label(
            content, text="Новый план добавлен в календарь!",
            bg=BG_CARD, fg=ACCENT_BLUE,
            font=(UI_FONT, 9, "bold"), anchor="w"
        ).pack(fill="x")

        # ФИО
        display_fio = fio if is_win else "Пациент: {}".format(fio)
        tk.Label(
            content, text=display_fio,
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

        # Кнопка настроек «⚙»
        settings_btn = tk.Label(
            self, text="⚙",
            bg=BG_CARD, fg=TEXT_MUTED,
            font=(UI_FONT, 12), cursor="hand2"
        )
        settings_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-30, y=6)
        settings_btn.bind("<Button-1>", self.on_settings_callback)
        settings_btn.bind("<Enter>", lambda e: settings_btn.config(fg=TEXT_WHITE))
        settings_btn.bind("<Leave>", lambda e: settings_btn.config(fg=TEXT_MUTED))

    def close(self):
        """Немедленное удаление карточки."""
        if self._closed:
            return
        self._closed = True
        self.on_close_callback(self)


class NotifierApp:
    """Основной класс АРМ-клиента (чистый tkinter.Tk для 100% совместимости с DPI)."""

    def __init__(self):
        log_debug("main.py: Инициализация NotifierApp...")

        # Главное окно — стандартный tkinter.Tk (без customtkinter!)
        self.root = tk.Tk()
        self.root.withdraw()  # Скрываем окно сразу, чтобы избежать мерцания и фризов при инициализации
        self.root.update_idletasks()  # Прокачиваем события для стабильности на старых системах
        
        try:
            self.root.tk.call('encoding', 'system', 'utf-8')
        except Exception:
            pass
        self.root.configure(bg=BG_WINDOW)

        # Размеры карточки и стека
        self.card_width = 400
        self.card_height = 145
        self.spacing = 8
        self.margin_x = 20
        self.margin_y = 60

        self.cards = []
        self.event_queue = queue.Queue()
        self.status_queue = queue.Queue()
        
        # Хранилище истории уведомлений за текущие сутки
        self.history = []

        self.cards_window = None
        self.overlay = None
        self.drag_data = {"x": 0, "y": 0, "dragged": False}
        self.shift_pressed = False

        log_debug("main.py: Вызов _init_overlay...")
        self._init_overlay()

        # Прослушивание БД
        log_debug("main.py: Создание DBListener...")
        self.db_listener = DBListener(
            event_queue=self.event_queue,
            on_status_change=self._on_db_status_changed
        )
        log_debug("main.py: Запуск DBListener...")
        self.db_listener.start()
        log_debug("main.py: DBListener успешно запущен!")

        # Опрос очереди событий
        self.root.after(100, self._poll_events)
        log_debug("main.py: Инициализация NotifierApp завершена успешно")

    def _init_overlay(self) -> None:
        """Инициализация плавающей кнопки управления (оверлея)."""
        log_debug("main.py: Создание оверлея...")
        self.overlay = tk.Toplevel(self.root)
        self.overlay.overrideredirect(True)
        try:
            self.overlay.attributes("-topmost", True)
        except Exception:
            pass
        self.overlay.configure(bg=BG_WINDOW)
        
        # Разрешаем прозрачность если поддерживается
        try:
            self.overlay.attributes("-alpha", 0.95)
        except Exception:
            pass

        # Инициализация настроек из кэша
        settings = self._get_cached_settings()
        self.sound_enabled = settings.get('sound_enabled', True)

        self.overlay_canvas = tk.Canvas(self.overlay, width=12, height=12, bg=BG_WINDOW, highlightthickness=0)
        self.overlay_canvas.pack()

        # Овал статуса и текст (размер 12x12)
        self.overlay_circle = self.overlay_canvas.create_oval(1, 1, 11, 11, fill=BG_CARD, outline=ACCENT_BLUE, width=1)
        self.overlay_text = self.overlay_canvas.create_text(6, 6, text="T", fill=TEXT_WHITE, font=(UI_FONT, 5, "bold"))

        # Биндинги
        self.overlay_canvas.bind("<Button-1>", self._on_overlay_click)
        self.overlay_canvas.bind("<ButtonRelease-1>", self._on_overlay_release)
        self.overlay_canvas.bind("<B1-Motion>", self._on_overlay_drag)
        self.overlay_canvas.bind("<Button-3>", self._show_context_menu)
        
        self.overlay_canvas.bind("<Enter>", self._on_overlay_enter)
        self.overlay_canvas.bind("<Leave>", self._on_overlay_leave)



        # Создаем меню один раз
        self.menu = tk.Menu(self.overlay, tearoff=0, bg=BG_WINDOW, fg=TEXT_WHITE,
                            activebackground=ACCENT_BLUE, activeforeground=TEXT_WHITE,
                            font=(UI_FONT, 10))
        # Создаем меню настроек карточки один раз
        self.card_menu = tk.Menu(self.root, tearoff=0, bg=BG_WINDOW, fg=TEXT_WHITE,
                                 activebackground=ACCENT_BLUE, activeforeground=TEXT_WHITE,
                                 font=(UI_FONT, 10))

        # Позиционирование
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = settings.get('x')
        y = settings.get('y')
        if x is None or y is None or x < 0 or x > sw - 12 or y < 0 or y > sh - 12:
            x = (sw - 12) // 2
            y = 35
            
        self.overlay.geometry("12x12+{}+{}".format(x, y))
        self.overlay_status_msg = "Инициализация..."
        self.tooltip_window = None

    def _get_cached_settings(self):
        try:
            cache_dir = os.path.expanduser('~/.config/teragis_notifier')
            if is_win:
                cache_dir = os.path.join(os.environ.get('APPDATA', ''), 'teragis_notifier')
            cache_file = os.path.join(cache_dir, 'overlay.cache')
            if os.path.exists(cache_file):
                import json
                with open(cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_cached_settings(self):
        try:
            cache_dir = os.path.expanduser('~/.config/teragis_notifier')
            if is_win:
                cache_dir = os.path.join(os.environ.get('APPDATA', ''), 'teragis_notifier')
            os.makedirs(cache_dir, exist_ok=True)
            cache_file = os.path.join(cache_dir, 'overlay.cache')
            
            x, y = 0, 0
            if self.overlay and self.overlay.winfo_exists():
                x = self.overlay.winfo_x()
                y = self.overlay.winfo_y()
            else:
                old = self._get_cached_settings()
                x = old.get('x', 0)
                y = old.get('y', 0)

            import json
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'x': x,
                    'y': y,
                    'sound_enabled': self.sound_enabled
                }, f)
            if not is_win:
                try:
                    os.chmod(cache_file, 0o600)
                except Exception:
                    pass
        except Exception:
            pass

    def _on_overlay_click(self, event):
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
        self.drag_data["dragged"] = False

    def _on_overlay_release(self, event):
        if not self.drag_data.get("dragged", False):
            self._show_context_menu(event)

    def _on_overlay_drag(self, event):
        self.drag_data["dragged"] = True
        deltax = event.x - self.drag_data["x"]
        deltay = event.y - self.drag_data["y"]
        x = self.overlay.winfo_x() + deltax
        y = self.overlay.winfo_y() + deltay

        sw = self.overlay.winfo_screenwidth()
        sh = self.overlay.winfo_screenheight()

        if x < 0: x = 0
        if x > sw - 12: x = sw - 12
        if y < 0: y = 0
        if y > sh - 12: y = sh - 12

        self.overlay.geometry("12x12+{}+{}".format(x, y))
        self._save_cached_settings()
        self._hide_tooltip()

    def _on_overlay_enter(self, event):
        self.overlay_canvas.itemconfig(self.overlay_circle, outline="#3a7ebd")
        self._show_tooltip(event)

    def _on_overlay_leave(self, event):
        self._update_circle_color()
        self._hide_tooltip()

    def _update_circle_color(self):
        status = self.overlay_status_msg
        if "Подключено" in status:
            color = "#4CAF50" # Зеленый
        elif "Сбой" in status or "не найден" in status:
            color = "#F44336" # Красный
        else:
            color = "#FFC107" # Оранжевый/Желтый
        self.overlay_canvas.itemconfig(self.overlay_circle, outline=color)

    def _show_tooltip(self, event):
        self._hide_tooltip()
        self.tooltip_window = tk.Toplevel(self.overlay)
        self.tooltip_window.overrideredirect(True)
        try:
            self.tooltip_window.attributes("-topmost", True)
        except Exception:
            pass
        self.tooltip_window.configure(bg="#222222")
        
        lbl = tk.Label(self.tooltip_window, text=self.overlay_status_msg, bg="#222222", fg=TEXT_WHITE, font=(UI_FONT, 9), padx=6, pady=4, bd=1, relief="solid")
        lbl.pack()
        
        # Позиционируем чуть ниже оверлея
        x = self.overlay.winfo_x() + 6 - (lbl.winfo_reqwidth() // 2)
        y = self.overlay.winfo_y() + 17
        self.tooltip_window.geometry("+{}+{}".format(x, y))

    def _hide_tooltip(self):
        if self.tooltip_window:
            try:
                self.tooltip_window.destroy()
            except Exception:
                pass
            self.tooltip_window = None

    def hide_overlay(self):
        if self.overlay:
            self.overlay.withdraw()

    def show_overlay(self):
        if self.overlay:
            self.overlay.deiconify()
            self.overlay.lift()

    def toggle_overlay(self):
        if self.overlay:
            if self.overlay.winfo_viewable():
                self.hide_overlay()
            else:
                self.show_overlay()

    def toggle_sound(self):
        self.sound_enabled = not self.sound_enabled
        self._save_cached_settings()

    def _close_context_menu(self):
        try:
            self.menu.grab_release()
        except Exception:
            pass
        try:
            self.menu.unpost()
        except Exception:
            pass

    def _show_context_menu(self, event):
        self._close_context_menu()
        self.menu.delete(0, "end")
        self.menu.add_command(label="История", command=self.show_history_window)
        self.menu.add_separator()
        
        sound_label = "Выключить звук" if self.sound_enabled else "Включить звук"
        self.menu.add_command(label=sound_label, command=self.toggle_sound)
        self.menu.add_separator()
        
        self.menu.add_command(label="Скрыть", command=self.hide_overlay)
        self.menu.add_separator()
        
        self.menu.add_command(label="Выход", command=self.on_exit, foreground="red")
        
        self.menu.bind("<Unmap>", lambda e: self._on_menu_unmap())
        self.menu.bind("<FocusOut>", lambda e: self._close_context_menu())
        
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        except Exception as e:
            log_debug("main.py: Ошибка tk_popup для контекстного меню: {}".format(e))

    def _on_menu_unmap(self):
        try:
            self.menu.grab_release()
        except Exception:
            pass

    def _close_card_menu(self):
        try:
            self.card_menu.grab_release()
        except Exception:
            pass
        try:
            self.card_menu.unpost()
        except Exception:
            pass

    def _show_card_settings_menu(self, event):
        self._close_card_menu()
        self.card_menu.delete(0, "end")
        self.card_menu.add_command(label="Показать кнопку управления", command=self.show_overlay)
        self.card_menu.add_separator()
        
        self.card_menu.add_command(label="История", command=self.show_history_window)
        self.card_menu.add_separator()
        
        sound_label = "Выключить звук" if self.sound_enabled else "Включить звук"
        self.card_menu.add_command(label=sound_label, command=self.toggle_sound)
        self.card_menu.add_separator()
        
        self.card_menu.add_command(label="Скрыть кнопку управления", command=self.hide_overlay)
        self.card_menu.add_separator()
        
        self.card_menu.add_command(label="Выход", command=self.on_exit, foreground="red")
        
        self.card_menu.bind("<Unmap>", lambda e: self._on_card_menu_unmap())
        self.card_menu.bind("<FocusOut>", lambda e: self._close_card_menu())
        
        try:
            self.card_menu.tk_popup(event.x_root, event.y_root)
        except Exception as e:
            log_debug("main.py: Ошибка tk_popup для меню настроек карточки: {}".format(e))

    def _on_card_menu_unmap(self):
        try:
            self.card_menu.grab_release()
        except Exception:
            pass

    def _on_db_status_changed(self, is_connected: bool, status_msg: str):
        """Обновляет статус подключения потокобезопасно через очередь."""
        self.status_queue.put((is_connected, status_msg))

    def _update_status_ui(self, is_connected: bool, status_msg: str):
        """Обновляет подсказку и цвет оверлея."""
        self.overlay_status_msg = status_msg
        self._update_circle_color()

    # --- Карточки уведомлений ---

    def add_notification(self, fio: str, details: str, timeout_ms=300000):
        """Добавляет новое уведомление сверху стека."""
        fio = clean_emojis(fio)
        details = clean_emojis(details)
        log_debug("main.py: Добавление уведомления для {}...".format(mask_fio(fio)))
        try:
            # Строгий лимит ротации: не более 5 карточек одновременно
            while len(self.cards) >= 5:
                oldest_card = self.cards[-1]
                log_debug("main.py: Превышен лимит ({}). Удаляем старую карточку.".format(len(self.cards)))
                oldest_card.close()

            # Добавляем в историю уведомлений
            event_time = time.strftime('%H:%M:%S')
            event_date = time.strftime('%Y-%m-%d')
            self.history.append({
                "time": event_time,
                "date": event_date,
                "fio": fio,
                "details": details
            })
            self._clean_old_history()

            # Контейнер для карточек
            if is_win:
                container = self.root
            else:
                if self.cards_window is None or not self.cards_window.winfo_exists():
                    self.cards_window = tk.Toplevel(self.root)
                    self.cards_window.withdraw()  # Скрываем сразу, чтобы избежать мерцания в 0,0
                    self.cards_window.configure(bg=BG_WINDOW)
                    self.cards_window.overrideredirect(True)
                    try:
                        self.cards_window.wm_attributes("-topmost", True)
                    except Exception:
                        pass
                    try:
                        self.cards_window.attributes("-alpha", 0.95)
                    except Exception:
                        pass
                container = self.cards_window

            card = NotificationCard(
                container, fio=fio, details=details,
                on_close_callback=self._remove_card,
                on_settings_callback=self._show_card_settings_menu,
                width=self.card_width
            )
            self.cards.insert(0, card)
            
            # Автозакрытие карточки по таймеру
            self.root.after(timeout_ms, lambda c=card: self._safe_close_card(c))
            
            log_debug("main.py: Карточка создана и добавлена в список")
            self._repack_cards()
        except Exception as e:
            log_debug("main.py: Ошибка в add_notification: {}".format(e))

    def _safe_close_card(self, card: NotificationCard):
        """Безопасное автоматическое закрытие карточки по таймеру."""
        try:
            if card in self.cards:
                log_debug("main.py: Сработал 5-минутный таймер автозакрытия для карточки.")
                card.close()
        except Exception as e:
            log_debug("main.py: Ошибка при автозакрытии карточки: {}".format(e))

    def _remove_card(self, card: NotificationCard):
        """Удаляет карточку из стека."""
        log_debug("main.py: Удаление карточки...")
        try:
            if card in self.cards:
                self.cards.remove(card)
                card.destroy()
                # Вызываем перепаковку отложенно, чтобы дать завершиться обработчику клика
                self.root.after(10, self._repack_cards)
                import gc
                gc.collect()
        except Exception as e:
            log_debug("main.py: Ошибка в _remove_card: {}".format(e))

    def _repack_cards(self):
        """Перестраивает стек карточек и позиционирует окно в правом нижнем углу."""
        log_debug("main.py: Перепаковка карточек. Всего: {}".format(len(self.cards)))
        try:
            container = self.root if is_win else self.cards_window

            # Удаляем визуальное представление всех дочерних, которые не в self.cards
            if container and container.winfo_exists():
                for c in container.winfo_children():
                    if c not in self.cards and isinstance(c, NotificationCard):
                        try:
                            c.pack_forget()
                        except Exception:
                            pass

            if not self.cards:
                log_debug("main.py: Нет карточек — скрываем окно")
                if is_win:
                    try:
                        self.root.overrideredirect(False)
                    except Exception:
                        pass
                    self.root.withdraw()
                else:
                    if self.cards_window and self.cards_window.winfo_exists():
                        self.cards_window.destroy()
                        self.cards_window = None
                return

            for i, card in enumerate(self.cards):
                pad_bottom = self.spacing if i < len(self.cards) - 1 else 0
                card.pack(fill="x", pady=(0, pad_bottom))

            num = len(self.cards)
            total_h = num * self.card_height + (num - 1) * self.spacing

            sw = container.winfo_screenwidth()
            sh = container.winfo_screenheight()
            x = sw - self.card_width - self.margin_x
            y = sh - total_h - self.margin_y

            log_debug("main.py: Геометрия: {}x{}+{}+{}".format(self.card_width, total_h, x, y))

            if not is_win:
                # На X11/Linux для overrideredirect окон крайне важен порядок:
                # сначала скрываем, устанавливаем геометрию, затем отображаем через deiconify.
                container.withdraw()
                container.geometry("{}x{}+{}+{}".format(self.card_width, total_h, x, y))
                container.deiconify()
                try:
                    container.attributes("-alpha", 0.95)
                except Exception:
                    pass
                container.lift()
                container.update()
                log_debug("main.py: [Linux] Окно позиционировано и отображено в {}x{}+{}+{}".format(self.card_width, total_h, x, y))
            else:
                self.root.overrideredirect(True)
                self.root.wm_attributes("-topmost", True)
                self.root.attributes("-alpha", 0.95)
                self.root.geometry("{}x{}+{}+{}".format(self.card_width, total_h, x, y))
                self.root.deiconify()
                self.root.update_idletasks()
                self.root.lift()
                self.root.wm_attributes("-topmost", True)
                log_debug("main.py: [Windows] Окно отображено")
        except Exception as e:
            log_debug("main.py: Ошибка в _repack_cards: {}".format(e))

    # --- История уведомлений за день ---

    def _clean_old_history(self):
        """Удаляет из истории уведомления, полученные не сегодня."""
        try:
            today = time.strftime('%Y-%m-%d')
            self.history = [item for item in self.history if item['date'] == today]
        except Exception as e:
            log_debug("main.py: Ошибка при очистке старой истории: {}".format(e))

    def show_history_window(self):
        """Отображает стильное окно с историей уведомлений за сегодня."""
        log_debug("main.py: Открытие окна истории уведомлений...")
        try:
            self._clean_old_history()

            # Создаем Toplevel окно
            history_win = tk.Toplevel(self.root)
            history_win.title("История уведомлений за сегодня")
            history_win.configure(bg=BG_WINDOW)
            
            # Размеры окна
            win_w = 450
            win_h = 500
            
            # Центрирование окна на экране
            sw = history_win.winfo_screenwidth()
            sh = history_win.winfo_screenheight()
            x = (sw - win_w) // 2
            y = (sh - win_h) // 2
            history_win.geometry("{}x{}+{}+{}".format(win_w, win_h, x, y))
            
            # Устанавливаем поверх остальных окон и фокусируемся
            try:
                history_win.attributes("-topmost", True)
            except Exception:
                pass
            history_win.focus_force()

            # Шапка окна
            header_frame = tk.Frame(history_win, bg=BG_WINDOW, pady=10)
            header_frame.pack(fill="x")
            
            history_title = "🕒 История уведомлений за сегодня" if is_win else "История уведомлений за сегодня"
            tk.Label(
                header_frame, text=history_title,
                bg=BG_WINDOW, fg=ACCENT_BLUE,
                font=(UI_FONT, 14, "bold")
            ).pack(side="left", padx=15)

            # Контейнер для списка с прокруткой
            container = tk.Frame(history_win, bg=BG_WINDOW)
            container.pack(fill="both", expand=True, padx=15, pady=(0, 10))

            canvas = tk.Canvas(container, bg=BG_WINDOW, highlightthickness=0)
            scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
            scrollable_frame = tk.Frame(canvas, bg=BG_WINDOW)

            scrollable_frame.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
            )

            canvas.create_window((0, 0), window=scrollable_frame, anchor="nw", width=win_w - 50)
            canvas.configure(yscrollcommand=scrollbar.set)

            # Привязка прокрутки колесиком мыши (совместимо с Windows и Linux)
            def _on_mousewheel(event):
                if event.num == 4:
                    canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    canvas.yview_scroll(1, "units")
                else:
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

            def bind_mousewheel(widget):
                widget.bind("<MouseWheel>", _on_mousewheel)
                widget.bind("<Button-4>", _on_mousewheel)
                widget.bind("<Button-5>", _on_mousewheel)
                for child in widget.winfo_children():
                    bind_mousewheel(child)
            
            # Отвязка прокрутки при закрытии окна
            def _on_close_history():
                history_win.destroy()

            history_win.protocol("WM_DELETE_WINDOW", _on_close_history)

            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            # Заполнение списка
            if not self.history:
                no_events_label = tk.Label(
                    scrollable_frame, text="Сегодня уведомлений еще не поступало.",
                    bg=BG_WINDOW, fg=TEXT_MUTED,
                    font=(UI_FONT, 11, "italic"), pady=50
                )
                no_events_label.pack(fill="x")
            else:
                # Выводим в обратном порядке (сначала самые новые)
                for item in reversed(self.history):
                    card_item = tk.Frame(
                        scrollable_frame, bg=BG_CARD,
                        highlightbackground=ACCENT_BLUE,
                        highlightcolor=ACCENT_BLUE,
                        highlightthickness=1,
                        pady=8, padx=10
                    )
                    card_item.pack(fill="x", pady=4)
                    
                    # Синий индикатор
                    tk.Frame(card_item, width=3, bg=ACCENT_BLUE).pack(side="left", fill="y", padx=(0, 8))
                    
                    info_frame = tk.Frame(card_item, bg=BG_CARD)
                    info_frame.pack(side="left", fill="both", expand=True)
                    
                    # Время и заголовок
                    title_frame = tk.Frame(info_frame, bg=BG_CARD)
                    title_frame.pack(fill="x")
                    
                    display_history_fio = item['fio'] if is_win else "Пациент: {}".format(item['fio'])
                    tk.Label(
                        title_frame, text=display_history_fio,
                        bg=BG_CARD, fg=TEXT_WHITE,
                        font=(UI_FONT, 10, "bold"), anchor="w"
                    ).pack(side="left")
                    
                    tk.Label(
                        title_frame, text=item['time'],
                        bg=BG_CARD, fg=TEXT_MUTED,
                        font=(UI_FONT, 9), anchor="e"
                    ).pack(side="right", padx=(0, 5))
                    
                    # Детали
                    tk.Label(
                        info_frame, text=item['details'],
                        bg=BG_CARD, fg=TEXT_GREY,
                        font=(UI_FONT, 9), anchor="w", justify="left"
                    ).pack(fill="x", pady=(4, 0))

            # Рекурсивно биндим прокрутку ко всем элементам внутри canvas
            bind_mousewheel(canvas)

            # Кнопка закрытия внизу
            btn_frame = tk.Frame(history_win, bg=BG_WINDOW, pady=10)
            btn_frame.pack(fill="x")
            
            close_btn = tk.Button(
                btn_frame, text="Закрыть",
                bg=ACCENT_BLUE, fg=TEXT_WHITE,
                activebackground=HOVER_DARK, activeforeground=TEXT_WHITE,
                font=(UI_FONT, 10, "bold"), bd=0, padx=20, pady=6,
                command=_on_close_history, cursor="hand2"
            )
            close_btn.pack()
        except Exception as e:
            log_debug("main.py: Ошибка при открытии окна истории: {}".format(e))

    # --- Звуковое оповещение ---

    def _play_notification_sound(self) -> None:
        """Воспроизводит короткий звуковой сигнал (кроссплатформенно)."""
        if not self.sound_enabled:
            return
        now = time.time()
        if now - getattr(self, '_last_sound_time', 0.0) < 2.0:
            return
        self._last_sound_time = now
        
        def _sound_worker():
            try:
                # Windows: winsound.Beep через системный спикер
                if is_win and winsound:
                    winsound.Beep(2000, 150)
                    return

                # Linux: воспроизводим системный звук уведомления
                if platform.system() == "Linux":
                    # Ищем подходящий аудио-плейер
                    player = None
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
                                    timeout=5,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE
                                )
                                return

                    # Фолбек: BEL-символ через терминал
                    subprocess.run(
                        ["bash", "-c", "printf '\\a'"],
                        timeout=2,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
            except Exception:
                pass

        threading.Thread(target=_sound_worker, daemon=True).start()

    # --- Опрос очереди ---

    def _poll_events(self):
        """Проверяет очереди событий и статусов в главном потоке Tkinter."""
        try:
            # Опрашиваем очередь изменения статусов БД
            last_status = None
            while True:
                try:
                    last_status = self.status_queue.get_nowait()
                    self.status_queue.task_done()
                except queue.Empty:
                    break
            
            if last_status is not None:
                is_connected, status_msg = last_status
                self._update_status_ui(is_connected, status_msg)
        except Exception as e:
            log_debug("main.py: Ошибка при обработке очереди статусов: {}".format(e))

        try:
            # Опрашиваем очередь уведомлений
            while True:
                event = self.event_queue.get_nowait()
                fio = event.get('fio')
                details = event.get('details')
                log_debug("main.py: Извлечено событие из очереди для FIO={}".format(mask_fio(fio)))
                self.add_notification(fio, details)
                self._play_notification_sound()
                self.event_queue.task_done()
        except queue.Empty:
            pass
        except Exception as e:
            log_debug("main.py: Ошибка при обработке очереди уведомлений: {}".format(e))

        self.root.after(100, self._poll_events)

    # --- Завершение ---

    def on_exit(self, icon=None, item=None):
        """Корректное завершение работы с подтверждением."""
        self._close_context_menu()
        self._close_card_menu()
        
        if not messagebox.askyesno("Выход", "Вы действительно хотите закрыть Teragis Notifier?"):
            return
            
        log_debug("main.py: Завершение работы приложения...")
        try:
            self.db_listener.stop()
        except Exception:
            pass
        self._hide_tooltip()
        if self.overlay:
            try:
                self.overlay.destroy()
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
        log_debug("main.py: Критический сбой: {}".format(run_err))
