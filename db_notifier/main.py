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
from auto_discovery import log_debug

class NotificationCard(customtkinter.CTkFrame):
    """Виджет отдельной карточки уведомления."""
    
    def __init__(self, master, fio, details, on_close_callback, **kwargs):
        # Очень темный фон для карточки, скругленные углы.
        # Не передаем width/height в super().__init__, чтобы избежать багов масштабирования float DPI в Tkinter.
        super().__init__(
            master, 
            fg_color="#1E1E1E", 
            border_width=1, 
            border_color="#1f538d", # Приятный синий акцентный бордюр
            corner_radius=12,
            **kwargs
        )
        self.on_close_callback = on_close_callback
        
        # Задаем размеры через configure (он безопасно приводит к целым числам на системном уровне)
        self.configure(width=320, height=145)
        
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
        
        # Контент (ФИО и детальные клинические параметры)
        self.title_label = customtkinter.CTkLabel(
            self,
            text="Пациент на лечении!",
            font=("Helvetica", 11, "bold"),
            text_color="#1f538d"
        )
        self.title_label.pack(anchor="w", pady=(8, 0), padx=(10, 0))
        
        self.fio_label = customtkinter.CTkLabel(
            self,
            text=f"👤 {fio}",
            font=("Helvetica", 13, "bold"),
            text_color="white"
        )
        self.fio_label.pack(anchor="w", pady=(2, 0), padx=(10, 0))
        
        self.details_label = customtkinter.CTkLabel(
            self,
            text=details,
            font=("Helvetica", 11),
            text_color="#DDDDDD",
            justify="left"
        )
        self.details_label.pack(anchor="w", pady=(2, 8), padx=(10, 0))
        
        # Запуск таймера на автоматическое закрытие через 5 минут (300 000 мс)
        self.timer_id = self.after(300000, self.close_with_fade)

    def close(self):
        """Немедленное удаление карточки."""
        if hasattr(self, 'timer_id'):
            self.after_cancel(self.timer_id)
        self.on_close_callback(self)
        
    def close_with_fade(self):
        """Плавное закрытие карточки с эффектом угасания."""
        self.close()

class NotifierApp(customtkinter.CTk):
    """Основной класс АРМ-клиента (безрамочное главное окно-карусель)."""
    
    def __init__(self):
        super().__init__()
        log_debug("main.py: Инициализация NotifierApp...")
        
        # 1. Настройки CustomTkinter
        customtkinter.set_appearance_mode("Dark")
        customtkinter.set_default_color_theme("blue")
        
        # Скрываем стандартное оформление окна, но не применяем overrideredirect до deiconify,
        # чтобы избежать багов инициализации Tkinter на Windows.
        self.withdraw()
        
        # Размеры окна (строго целые числа)
        self.card_width = 320
        self.card_height = 145
        self.spacing = 10
        self.margin_x = 20
        self.margin_y = 65  # Безопасный отступ от нижнего края (над панелью задач)
        
        self.cards = []
        
        # 3. Инициализация очереди событий
        self.event_queue = queue.Queue()
        
        # 4. Запуск фоного трея
        self.tray_icon = None
        self._init_tray()
        
        # 5. Запуск фонового прослушивания БД
        self.db_listener = DBListener(
            event_queue=self.event_queue,
            on_status_change=self._on_db_status_changed
        )
        self.db_listener.start()
        
        # 6. Запуск периодической проверки очереди событий
        self.after(100, self._poll_events)
        log_debug("main.py: Инициализация NotifierApp завершена успешно")
        
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
        log_debug("main.py: Запуск фонового трея...")
        def tray_worker():
            try:
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
            except Exception as tray_err:
                log_debug(f"main.py: Ошибка в потоке трея: {tray_err}")
            
        threading.Thread(target=tray_worker, daemon=True).start()

    def _on_db_status_changed(self, is_connected, status_msg):
        """Callback: вызывается при изменении статуса подключения к БД."""
        if self.tray_icon:
            self.tray_icon.title = f"Teragis Notifier: {status_msg}"

    def add_notification(self, fio, details):
        """Добавляет новое уведомление сверху стека."""
        log_debug(f"main.py: Добавление уведомления для {fio}...")
        try:
            # 1. Если карточек уже 5, принудительно удаляем самую старую (первую в списке, т.е. нижнюю)
            if len(self.cards) >= 5:
                oldest_card = self.cards[-1]
                log_debug(f"main.py: Превышен лимит карточек, удаляем самую старую ({oldest_card.fio_label.cget('text')})")
                oldest_card.close()
                
            # 2. Создаем новую карточку
            card = NotificationCard(
                self, 
                fio=fio, 
                details=details, 
                on_close_callback=self._remove_card
            )
            
            # Добавляем в начало нашего списка
            self.cards.insert(0, card)
            log_debug("main.py: Карточка создана и добавлена в список")
            
            # 3. Перерисовываем стек
            self._repack_cards()
        except Exception as e:
            log_debug(f"main.py: Ошибка в add_notification: {e}")

    def _remove_card(self, card):
        """Удаляет карточку из стека и перерисовывает окно."""
        log_debug("main.py: Удаление карточки...")
        try:
            if card in self.cards:
                self.cards.remove(card)
                card.destroy()
                self._repack_cards()
        except Exception as e:
            log_debug(f"main.py: Ошибка в _remove_card: {e}")

    def _repack_cards(self):
        """Перераспределяет карточки сверху вниз и меняет геометрию окна."""
        log_debug(f"main.py: Перепаковка карточек. Всего активных карточек: {len(self.cards)}")
        try:
            # Сначала убираем упаковку всех карточек
            for c in self.winfo_children():
                c.pack_forget()
                
            if not self.cards:
                log_debug("main.py: Нет активных карточек, скрываем окно (withdraw)")
                self.withdraw()
                return
                
            # Упаковываем карточки (новые сверху)
            for i, card in enumerate(self.cards):
                card.pack(fill="x", pady=(0, self.spacing if i < len(self.cards)-1 else 0))
                
            # Рассчитываем новую высоту (строго целое)
            num_cards = int(len(self.cards))
            total_height = int((num_cards * self.card_height) + ((num_cards - 1) * self.spacing))
            
            # Позиционируем окно в правом нижнем углу
            screen_width = int(self.winfo_screenwidth())
            screen_height = int(self.winfo_screenheight())
            
            x = int(screen_width - self.card_width - self.margin_x)
            y = int(screen_height - total_height - self.margin_y)
            
            log_debug(f"main.py: Рассчитанная геометрия окна: {self.card_width}x{total_height}+{x}+{y}")
            
            # ВАЖНО для Windows: Сначала скрываем рамку
            self.overrideredirect(True)
            # Устанавливаем поверх всех окон
            self.wm_attributes("-topmost", True)
            # Легкая прозрачность
            self.attributes("-alpha", 0.95)
            
            # Задаем геометрию
            self.geometry(f"{self.card_width}x{total_height}+{x}+{y}")
            
            # Принудительно обновляем и выводим на экран
            self.deiconify()
            self.update()
            self.lift()
            self.wm_attributes("-topmost", True)
            log_debug("main.py: Окно успешно отображено на экране (deiconify, update, lift)")
            
        except Exception as e:
            log_debug(f"main.py: Ошибка в _repack_cards: {e}")

    def _poll_events(self):
        """Проверяет потокобезопасную очередь событий и выводит новые уведомления."""
        try:
            while True:
                event = self.event_queue.get_nowait()
                fio = event.get('fio')
                details = event.get('details')
                log_debug(f"main.py: Извлечено событие из очереди для FIO={fio}")
                
                # Подаем сигнал окну уведомлений
                self.add_notification(fio, details)
                
                # Проигрываем легкий системный писк (необязательно, но приятно)
                try:
                    self.bell()
                except Exception:
                    pass
                    
                self.event_queue.task_done()
        except queue.Empty:
            pass
        except Exception as e:
            log_debug(f"main.py: Ошибка при обработке очереди событий: {e}")
            
        # Планируем следующую проверку через 100 мс
        self.after(100, self._poll_events)

    def on_exit(self, icon=None, item=None):
        """Корректное завершение работы программы."""
        logger.info("[NotifierApp]: Завершение работы...")
        log_debug("main.py: Завершение работы приложения...")
        
        # Останавливаем поток БД
        if hasattr(self, 'db_listener'):
            try:
                self.db_listener.stop()
            except Exception:
                pass
            
        # Останавливаем трей
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            
        # Уничтожаем окна Tkinter
        try:
            self.after(0, self.destroy)
        except Exception:
            pass

if __name__ == '__main__':
    # В Linux Tkinter требует правильную локаль
    try:
        import locale
        locale.setlocale(locale.LC_ALL, '')
    except Exception:
        pass
        
    log_debug("main.py: Точка входа в приложение (main)")
    try:
        app = NotifierApp()
        app.mainloop()
    except Exception as run_err:
        log_debug(f"main.py: Критический сбой при запуске приложения: {run_err}")
