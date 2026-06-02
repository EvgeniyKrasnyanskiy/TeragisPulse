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
from PIL import Image, ImageDraw

is_win = os.name == 'nt'
if is_win:
    import pystray
else:
    pystray = None

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

    def __init__(self, master, fio: str, details: str, on_close_callback, width=400, **kwargs):
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

        self.cards: list[NotificationCard] = []
        self.event_queue: queue.Queue = queue.Queue()
        
        # Хранилище истории уведомлений за текущие сутки
        self.history: list[dict] = []

        # Трей и управление окнами в зависимости от ОС
        self.tray_icon = None
        self.cards_window = None

        if is_win:
            self.root.withdraw()  # Полностью скрыть до первого уведомления
            self._init_tray()
        else:
            log_debug("main.py: Вызов _init_linux_dashboard...")
            self._init_linux_dashboard()
            log_debug("main.py: _init_linux_dashboard успешно завершен!")

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

    def _init_linux_dashboard(self):
        """Инициализация аккуратного пульта управления для Linux."""
        log_debug("main.py: Инициализация Linux-пульта...")
        self.root.title("Teragis Notifier")

        # Кастомный заголовок
        log_debug("Linux-пульт: Создание title_label...")
        title_label = tk.Label(
            self.root,
            text="Teragis Pulse Notifier",
            bg=BG_WINDOW,
            fg=TEXT_WHITE,
            font=(UI_FONT, 14, "bold"),
            pady=10
        )
        title_label.pack()
        log_debug("Linux-пульт: title_label упакован!")

        # Рамка для статуса подключения
        log_debug("Linux-пульт: Создание status_frame...")
        status_frame = tk.Frame(
            self.root,
            bg=BG_CARD,
            highlightbackground=ACCENT_BLUE,
            highlightthickness=1,
            bd=0
        )
        status_frame.pack(fill="x", padx=20, pady=5)
        log_debug("Linux-пульт: status_frame упакован!")

        # Canvas для светодиода статуса
        log_debug("Linux-пульт: Создание status_canvas...")
        self.status_canvas = tk.Canvas(
            status_frame,
            width=16,
            height=16,
            bg=BG_CARD,
            highlightthickness=0
        )
        self.status_canvas.pack(side="left", padx=(10, 5), pady=8)
        log_debug("Linux-пульт: status_canvas упакован!")
        log_debug("Linux-пульт: Рисование status_led (oval)...")
        self.status_led = self.status_canvas.create_oval(2, 2, 14, 14, fill="#777777", outline="")
        log_debug("Linux-пульт: status_led отрисован!")

        # Текст статуса подключения
        log_debug("Linux-пульт: Создание status_label...")
        self.status_label = tk.Label(
            status_frame,
            text="Инициализация...",
            bg=BG_CARD,
            fg=TEXT_GREY,
            font=(UI_FONT, 10),
            anchor="w"
        )
        self.status_label.pack(side="left", fill="x", expand=True, pady=8)
        log_debug("Linux-пульт: status_label упакован!")

        # Кнопка истории
        log_debug("Linux-пульт: Создание btn_history...")
        btn_history = tk.Button(
            self.root,
            text="🕒 История за сегодня",
            bg=ACCENT_BLUE,
            fg=TEXT_WHITE,
            activebackground=HOVER_DARK,
            activeforeground=TEXT_WHITE,
            font=(UI_FONT, 10, "bold"),
            bd=0,
            cursor="hand2",
            command=self.show_history_window
        )
        btn_history.pack(fill="x", padx=20, pady=(15, 5), ipady=5)
        log_debug("Linux-пульт: btn_history упакован!")

        # Кнопка выхода
        log_debug("Linux-пульт: Создание btn_exit...")
        btn_exit = tk.Button(
            self.root,
            text="🚪 Выход",
            bg="#444444",
            fg=TEXT_WHITE,
            activebackground="#666666",
            activeforeground=TEXT_WHITE,
            font=(UI_FONT, 10, "bold"),
            bd=0,
            cursor="hand2",
            command=self.on_exit
        )
        btn_exit.pack(fill="x", padx=20, pady=5, ipady=5)
        log_debug("Linux-пульт: btn_exit упакован!")

        # Обработка закрытия окна крестиком
        log_debug("Linux-пульт: Настройка WM_DELETE_WINDOW...")
        self.root.protocol("WM_DELETE_WINDOW", self.on_exit)

        # Размеры и центрирование (выполняется строго ПОСЛЕ создания и упаковки всех виджетов!)
        win_w = 340
        win_h = 220
        log_debug("Linux-пульт: Определение размеров экрана...")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        log_debug(f"Linux-пульт: sw={sw}, sh={sh}")
        x = (sw - win_w) // 2
        y = (sh - win_h) // 2
        log_debug(f"Linux-пульт: Установка геометрии {win_w}x{win_h}+{x}+{y}...")
        self.root.geometry(f"{win_w}x{win_h}+{x}+{y}")
        log_debug("Linux-пульт: Геометрия установлена!")
        self.root.resizable(False, False)
        log_debug("Linux-пульт: Инициализация завершена!")

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
                is_linux = platform.system() == "Linux"
                
                menu_history_text = "Today's History" if is_linux else 'История за сегодня'
                menu_exit_text = 'Exit' if is_linux else 'Выход'
                title_text = "Teragis Notifier: Searching..." if is_linux else "Teragis Notifier: Поиск сервера..."
                
                menu = pystray.Menu(
                    pystray.MenuItem(menu_history_text, self._show_history_from_tray),
                    pystray.MenuItem(menu_exit_text, self.on_exit)
                )
                self.tray_icon = pystray.Icon(
                    "TeragisNotifier", image,
                    title_text, menu
                )
                self.tray_icon.run()
            except Exception as err:
                log_debug(f"main.py: Ошибка в потоке трея: {err}")

        threading.Thread(target=tray_worker, daemon=True).start()

    def _on_db_status_changed(self, is_connected: bool, status_msg: str):
        """Обновляет статус подключения потокобезопасно."""
        self.root.after(0, lambda: self._update_status_ui(is_connected, status_msg))

    def _update_status_ui(self, is_connected: bool, status_msg: str):
        """Обновляет подсказку иконки трея или виджеты пульта управления на Linux."""
        if is_win:
            if self.tray_icon:
                self.tray_icon.title = f"Teragis Notifier: {status_msg}"
        else:
            if not hasattr(self, 'status_label') or not hasattr(self, 'status_canvas'):
                return
            
            # Обновляем текст статуса подключения
            self.status_label.config(text=status_msg)

            # Определяем цвет светодиода
            if is_connected:
                color = "#4CAF50"  # Зеленый
            elif "Сбой" in status_msg or "не найден" in status_msg:
                color = "#F44336"  # Красный
            elif "Подключение" in status_msg or "Поиск" in status_msg:
                color = "#FF9800"  # Оранжевый
            else:
                color = "#777777"  # Серый
            
            self.status_canvas.itemconfig(self.status_led, fill=color)

    # --- Карточки уведомлений ---

    def add_notification(self, fio: str, details: str):
        """Добавляет новое уведомление сверху стека."""
        log_debug(f"main.py: Добавление уведомления для {fio}...")
        try:
            # Строгий лимит ротации: не более 5 карточек одновременно
            while len(self.cards) >= 5:
                oldest_card = self.cards[-1]
                log_debug(f"main.py: Превышен лимит ({len(self.cards)}). Удаляем старую карточку.")
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
                    self.cards_window.configure(bg=BG_WINDOW)
                    self.cards_window.overrideredirect(True)
                    self.cards_window.wm_attributes("-topmost", True)
                    try:
                        self.cards_window.attributes("-alpha", 0.95)
                    except Exception:
                        pass
                container = self.cards_window

            card = NotificationCard(
                container, fio=fio, details=details,
                on_close_callback=self._remove_card,
                width=self.card_width
            )
            self.cards.insert(0, card)
            
            # Автозакрытие карточки по таймеру ровно через 5 минут (300 000 мс)
            self.root.after(300_000, lambda c=card: self._safe_close_card(c))
            
            log_debug("main.py: Карточка создана и добавлена в список")
            self._repack_cards()
        except Exception as e:
            log_debug(f"main.py: Ошибка в add_notification: {e}")

    def _safe_close_card(self, card: NotificationCard):
        """Безопасное автоматическое закрытие карточки по таймеру."""
        try:
            if card in self.cards:
                log_debug("main.py: Сработал 5-минутный таймер автозакрытия для карточки.")
                card.close()
        except Exception as e:
            log_debug(f"main.py: Ошибка при автозакрытии карточки: {e}")

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
            container = self.root if is_win else self.cards_window

            # Удаляем визуальное представление всех дочерних, которые не в self.cards
            if container and container.winfo_exists():
                for c in container.winfo_children():
                    if c not in self.cards:
                        c.pack_forget()

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

            log_debug(f"main.py: Геометрия: {self.card_width}x{total_h}+{x}+{y}")

            if not is_win:
                # На X11/Linux сначала нужно отобразить окно (deiconify), 
                # и только потом применять overrideredirect(True), иначе WM скроет его навсегда.
                container.deiconify()
                container.overrideredirect(True)
                container.geometry(f"{self.card_width}x{total_h}+{x}+{y}")
                try:
                    container.attributes("-alpha", 0.95)
                except Exception:
                    pass
                container.lift()
                container.update()
                log_debug("main.py: [Linux] Окно отображено через deiconify + overrideredirect(True)")
            else:
                self.root.overrideredirect(True)
                self.root.wm_attributes("-topmost", True)
                self.root.attributes("-alpha", 0.95)
                self.root.geometry(f"{self.card_width}x{total_h}+{x}+{y}")
                self.root.deiconify()
                self.root.update_idletasks()
                self.root.lift()
                self.root.wm_attributes("-topmost", True)
                log_debug("main.py: [Windows] Окно отображено")
        except Exception as e:
            log_debug(f"main.py: Ошибка в _repack_cards: {e}")

    # --- История уведомлений за день ---

    def _clean_old_history(self):
        """Удаляет из истории уведомления, полученные не сегодня."""
        try:
            today = time.strftime('%Y-%m-%d')
            self.history = [item for item in self.history if item['date'] == today]
        except Exception as e:
            log_debug(f"main.py: Ошибка при очистке старой истории: {e}")

    def _show_history_from_tray(self, icon=None, item=None):
        """Метод вызывается из потока трея для открытия окна истории в потоке Tkinter."""
        log_debug("main.py: Запрос открытия истории из трея")
        self.root.after(0, self.show_history_window)

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
            history_win.geometry(f"{win_w}x{win_h}+{x}+{y}")
            
            # Устанавливаем поверх остальных окон и фокусируемся
            history_win.attributes("-topmost", True)
            history_win.focus_force()

            # Шапка окна
            header_frame = tk.Frame(history_win, bg=BG_WINDOW, pady=10)
            header_frame.pack(fill="x")
            
            tk.Label(
                header_frame, text="🕒 История уведомлений за сегодня",
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

            # Привязка прокрутки колесиком мыши
            def _on_mousewheel(event):
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            
            # Отвязка прокрутки при закрытии окна
            def _on_close_history():
                canvas.unbind_all("<MouseWheel>")
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
                    
                    tk.Label(
                        title_frame, text=f"👤 {item['fio']}",
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
            log_debug(f"main.py: Ошибка при открытии окна истории: {e}")

    # --- Звуковое оповещение ---

    def _play_notification_sound(self) -> None:
        """Воспроизводит короткий звуковой сигнал (кроссплатформенно)."""
        if not is_win:
            # На Linux звук должен быть полностью отключен (тихий режим)
            return

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
