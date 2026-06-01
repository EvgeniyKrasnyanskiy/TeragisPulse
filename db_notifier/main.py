# -*- coding: utf-8 -*-
import os
import sys
import queue
import time
import threading
import customtkinter
import pystray
from PIL import Image, ImageDraw

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

class NotificationCard(customtkinter.CTkFrame):
    """Виджет отдельной карточки уведомления."""
    
    def __init__(self, master, fio, start_date, on_close_callback, **kwargs):
        # Очень темный фон для карточки, скругленные углы
        super().__init__(
            master, 
            fg_color="#1E1E1E", 
            border_width=1, 
            border_color="#1f538d", # Приятный синий акцентный бордюр
            corner_radius=12,
            height=85,
            **kwargs
        )
        self.on_close_callback = on_close_callback
        
        # Запрещаем виджетам сжимать фрейм
        self.pack_propagate(False)
        
        # Синяя полоска-индикатор слева
        self.indicator = customtkinter.CTkFrame(
            self, 
            width=5, 
            fg_color="#1f538d", 
            corner_radius=0
        )
        self.indicator.pack(side="left", fill="y", padx=(1, 10))
        
        # Кнопка закрытия карточки ("X") на правом углу
        self.close_btn = customtkinter.CTkButton(
            self,
            text="×",
            width=22,
            height=22,
            fg_color="transparent",
            hover_color="#333333",
            text_color="#777777",
            font=("Helvetica", 16, "bold"),
            command=self.close
        )
        self.close_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-5, y=5)
        
        # Контент (ФИО и дата старта)
        self.title_label = customtkinter.CTkLabel(
            self,
            text="Пациент на аппарате!",
            font=("Helvetica", 11, "bold"),
            text_color="#1f538d"
        )
        self.title_label.pack(anchor="w", pady=(8, 0))
        
        self.fio_label = customtkinter.CTkLabel(
            self,
            text=f"👤 {fio}",
            font=("Helvetica", 13, "bold"),
            text_color="white"
        )
        self.fio_label.pack(anchor="w", pady=(2, 0))
        
        self.start_label = customtkinter.CTkLabel(
            self,
            text=f"⏱ {start_date}",
            font=("Helvetica", 11),
            text_color="#AAAAAA"
        )
        self.start_label.pack(anchor="w", pady=(2, 8))
        
        # Запуск таймера на автоматическое закрытие через 5 минут (300 000 мс)
        self.timer_id = self.after(300000, self.close_with_fade)

    def close(self):
        """Немедленное удаление карточки."""
        if hasattr(self, 'timer_id'):
            self.after_cancel(self.timer_id)
        self.on_close_callback(self)
        
    def close_with_fade(self):
        """Плавное закрытие карточки с эффектом угасания."""
        # Поскольку у нас безрамочное TopLevel окно для всех карточек,
        # мы просто удаляем элемент из верстки.
        self.close()

class NotificationWindow(customtkinter.CTkToplevel):
    """Фреймворк для управления всеми всплывающими карточками (Тост-Карусель)."""
    
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        
        # Делаем окно полностью безрамочным и поверх всех окон
        self.overrideredirect(True)
        self.wm_attributes("-topmost", True)
        
        # Прозрачный или темный фон подложки
        self.configure(fg_color="black")
        self.attributes("-alpha", 0.95) # Легкая общая прозрачность стека
        
        # Размеры окна
        self.card_width = 320
        self.card_height = 85
        self.spacing = 10
        self.margin_x = 20
        self.margin_y = 65  # Безопасный отступ от нижнего края (над панелью задач)
        
        self.cards = []
        
        # Прячем окно изначально
        self.withdraw()
        
    def add_notification(self, fio, start_date):
        """Добавляет новое уведомление сверху стека."""
        # 1. Если карточек уже 5, принудительно удаляем самую старую (первую в списке, т.е. нижнюю)
        if len(self.cards) >= 5:
            oldest_card = self.cards[-1]
            oldest_card.close()
            
        # 2. Создаем новую карточку
        card = NotificationCard(
            self, 
            fio=fio, 
            start_date=start_date, 
            on_close_callback=self._remove_card
        )
        
        # Добавляем в начало нашего списка
        self.cards.insert(0, card)
        
        # 3. Перерисовываем стек
        self._repack_cards()
        
    def _remove_card(self, card):
        """Удаляет карточку из стека и перерисовывает окно."""
        if card in self.cards:
            self.cards.remove(card)
            card.destroy()
            self._repack_cards()
            
    def _repack_cards(self):
        """Перераспределяет карточки сверху вниз и меняет геометрию окна."""
        # Сначала убираем упаковку всех карточек
        for c in self.winfo_children():
            c.pack_forget()
            
        if not self.cards:
            self.withdraw()
            return
            
        # Упаковываем карточки (новые сверху)
        for i, card in enumerate(self.cards):
            card.pack(fill="x", pady=(0, self.spacing if i < len(self.cards)-1 else 0))
            
        # Рассчитываем новую высоту
        num_cards = len(self.cards)
        total_height = (num_cards * self.card_height) + ((num_cards - 1) * self.spacing)
        
        # Позиционируем окно в правом нижнем углу
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        
        x = screen_width - self.card_width - self.margin_x
        y = screen_height - total_height - self.margin_y
        
        self.geometry(f"{self.card_width}x{total_height}+{x}+{y}")
        self.deiconify()
        self.lift()
        self.wm_attributes("-topmost", True)

class NotifierApp:
    """Основной класс АРМ-клиента."""
    
    def __init__(self):
        # 1. Настройки CustomTkinter
        customtkinter.set_appearance_mode("Dark")
        customtkinter.set_default_color_theme("blue")
        
        # 2. Главное невидимое окно (root)
        self.root = customtkinter.CTk()
        self.root.withdraw() # Полностью прячем главное окно
        
        # 3. Инициализация очереди событий
        self.event_queue = queue.Queue()
        
        # 4. Создаем окно уведомлений
        self.notif_win = NotificationWindow(self.root)
        
        # 5. Запуск фонового трея
        self.tray_icon = None
        self._init_tray()
        
        # 6. Запуск фонового прослушивания БД
        self.db_listener = DBListener(
            event_queue=self.event_queue,
            on_status_change=self._on_db_status_changed
        )
        self.db_listener.start()
        
        # 7. Запуск периодической проверки очереди событий
        self.root.after(100, self._poll_events)
        
    def _create_tray_image(self):
        """Создает красивое изображение для иконки трея (синий радар/пульс)."""
        img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "tray_icon.png")
        if os.path.exists(img_path):
            try:
                return Image.open(img_path)
            except Exception:
                pass
                
        # Если файла нет — генерируем красивое изображение в памяти
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Рисуем красивый пульсирующий круг с синим градиентом
        draw.ellipse([4, 4, 60, 60], outline="#1f538d", width=5)
        draw.ellipse([16, 16, 48, 48], fill="#1f538d")
        draw.ellipse([26, 26, 38, 38], fill="#4CAF50") # Зеленое ядро
        
        # Сохраняем на будущее
        try:
            os.makedirs(os.path.dirname(img_path), exist_ok=True)
            image.save(img_path)
        except Exception:
            pass
            
        return image

    def _init_tray(self):
        """Запускает системный трей в отдельном потоке."""
        def tray_worker():
            image = self._create_tray_image()
            menu = pystray.Menu(
                pystray.MenuItem('Выход', self.on_exit)
            )
            self.tray_icon = pystray.Icon(
                "TeragisNotifier", 
                image, 
                "Teragis Notifier: Поиск сервера...", 
                menu
            )
            self.tray_icon.run()
            
        threading.Thread(target=tray_worker, daemon=True).start()

    def _on_db_status_changed(self, is_connected, status_msg):
        """Callback: вызывается при изменении статуса подключения к БД."""
        if self.tray_icon:
            self.tray_icon.title = f"Teragis Notifier: {status_msg}"
            
    def _poll_events(self):
        """Проверяет потокобезопасную очередь событий и выводит новые уведомления."""
        try:
            while True:
                event = self.event_queue.get_nowait()
                fio = event.get('fio')
                start_date = event.get('start_date')
                
                # Подаем сигнал окну уведомлений
                self.notif_win.add_notification(fio, start_date)
                
                # Проигрываем легкий системный писк (необязательно, но приятно)
                try:
                    self.root.bell()
                except Exception:
                    pass
                    
                self.event_queue.task_done()
        except queue.Empty:
            pass
            
        # Планируем следующую проверку через 100 мс
        self.root.after(100, self._poll_events)

    def on_exit(self, icon=None, item=None):
        """Корректное завершение работы программы."""
        logger.info("[NotifierApp]: Завершение работы...")
        
        # Останавливаем поток БД
        if hasattr(self, 'db_listener'):
            self.db_listener.stop()
            
        # Останавливаем трей
        if self.tray_icon:
            self.tray_icon.stop()
            
        # Уничтожаем окна Tkinter
        self.root.after(0, self.root.destroy)
        
    def run(self):
        """Запускает главный цикл Tkinter."""
        self.root.mainloop()

if __name__ == '__main__':
    # В Linux Tkinter требует правильную локаль
    try:
        import locale
        locale.setlocale(locale.LC_ALL, '')
    except Exception:
        pass
        
    app = NotifierApp()
    app.run()
