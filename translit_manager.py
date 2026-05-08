import time
import threading
import pyperclip
from pynput import mouse, keyboard
import re
import logging

logger = logging.getLogger('TPulse.Translit')

class TranslitManager:
    """
    Класс для управления фоновой транслитерацией во внешних окнах.
    Слушает глобальное сочетание Shift + ЛКМ.
    """
    
    # Таблица обратной замены (Latin -> Cyrillic) ГОСТ 7.79-2000 (Система Б)
    # Порядок важен: от длинных к коротким
    REVERSE_MAP = [
        ('shh', 'Щ'),
        ('sh', 'Ш'),
        ('ch', 'Ч'),
        ('zh', 'Ж'),
        ('jo', 'Ё'),
        ('yu', 'Ю'),
        ('ya', 'Я'),
        ('eh', 'Э'),
        ('a', 'А'), ('b', 'Б'), ('v', 'В'), ('g', 'Г'), ('d', 'Д'),
        ('e', 'Е'), ('z', 'З'), ('i', 'И'), ('j', 'Й'), ('k', 'К'),
        ('l', 'Л'), ('m', 'М'), ('n', 'Н'), ('o', 'О'), ('p', 'П'),
        ('r', 'Р'), ('s', 'С'), ('t', 'Т'), ('u', 'У'), ('f', 'Ф'),
        ('x', 'Х'), ('c', 'Ц'), ('y', 'Ы'), ("'", 'Ь'), ('"', 'Ъ')
    ]

    def __init__(self):
        self.enabled = False
        self.is_shift_pressed = False
        self.mouse_listener = None
        self.kb_listener = None
        self.kb_controller = keyboard.Controller()
        self._stop_event = threading.Event()

    def start(self):
        """Запуск слушателей в фоновом режиме."""
        if self.enabled:
            return
        
        self.enabled = True
        self._stop_event.clear()
        
        # Слушатель клавиатуры (для отслеживания Shift)
        self.kb_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        self.kb_listener.start()
        
        # Слушатель мыши (для перехвата ЛКМ)
        self.mouse_listener = mouse.Listener(on_click=self._on_mouse_click)
        self.mouse_listener.start()
        
        logger.info("[Translit] Фоновая служба запущена")

    def stop(self):
        """Остановка слушателей."""
        self.enabled = False
        self._stop_event.set()
        if self.kb_listener:
            self.kb_listener.stop()
        if self.mouse_listener:
            self.mouse_listener.stop()
        logger.info("[Translit] Фоновая служба остановлена")

    def _on_key_press(self, key):
        if key in [keyboard.Key.shift, keyboard.Key.shift_r, keyboard.Key.shift_l]:
            self.is_shift_pressed = True

    def _on_key_release(self, key):
        if key in [keyboard.Key.shift, keyboard.Key.shift_r, keyboard.Key.shift_l]:
            self.is_shift_pressed = False

    def _on_mouse_click(self, x, y, button, pressed):
        if pressed and button == mouse.Button.left and self.is_shift_pressed and self.enabled:
            # Запускаем обработку в отдельном потоке, чтобы не блокировать слушателя
            threading.Thread(target=self._process_translit, daemon=True).start()

    def _process_translit(self):
        """Основная логика захвата и преобразования текста."""
        try:
            # 1. Даем время системе обработать фокус при клике
            time.sleep(0.1)
            
            # 2. Очищаем буфер, выделяем всё (Ctrl+A) и копируем (Ctrl+C)
            pyperclip.copy("") 
            with self.kb_controller.pressed(keyboard.Key.ctrl):
                self.kb_controller.press('a')
                self.kb_controller.release('a')
                time.sleep(0.05)
                self.kb_controller.press('c')
                self.kb_controller.release('c')
            
            # 3. Ждем наполнения буфера
            time.sleep(0.15)
            original_text = pyperclip.paste()
            
            if not original_text or not original_text.strip():
                return

            # 4. Проверка: является ли текст транслитом (минимум одно латинское слово)
            if not self._is_translit_needed(original_text):
                return

            # 5. Преобразование
            converted_text = self._apply_reverse_translit(original_text)
            
            # 6. Вставляем обратно (Ctrl+V)
            pyperclip.copy(converted_text.upper())
            with self.kb_controller.pressed(keyboard.Key.ctrl):
                self.kb_controller.press('v')
                self.kb_controller.release('v')
                
            logger.debug(f"[Translit] Преобразовано: {original_text[:20]}... -> {converted_text[:20]}...")
            
        except Exception as e:
            logger.error(f"[Translit] Ошибка при обработке: {e}")

    def _is_translit_needed(self, text):
        """
        Проверяет первые 1-2 слова на наличие латиницы.
        Если кириллица уже есть в первых словах - игнорируем.
        """
        words = text.strip().split()[:2]
        if not words:
            return False
            
        has_latin = False
        for word in words:
            # Если есть хоть одна кириллическая буква - это не транслит (или уже переведено)
            if re.search('[а-яА-ЯёЁ]', word):
                return False
            if re.search('[a-zA-Z]', word):
                has_latin = True
        
        return has_latin

    def _apply_reverse_translit(self, text):
        """Применяет маппинг ГОСТ 7.79-2000 Б (Latin -> Cyrillic)."""
        result = text
        # Проходим по маппингу (важен порядок!)
        for lat, cyr in self.REVERSE_MAP:
            # Заменяем с учетом регистра (упрощенно: если вход был в нижнем, 
            # но мы все равно в конце делаем .upper() по просьбе пользователя)
            # Однако для корректности regex делаем case-insensitive замену
            pattern = re.compile(re.escape(lat), re.IGNORECASE)
            result = pattern.sub(cyr, result)
            
        return result
