# -*- coding: utf-8 -*-
"""
Скрипт для тестирования GUI Teragis Notifier.
Запускает приложение NotifierApp в тестовом режиме, симулируя
поступление 6 уведомлений и ускоренное автозакрытие за 15 секунд.
"""
import sys
import os
import time

# Добавляем текущую директорию в sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from tnotif import NotifierApp, NotificationCard

class TestNotifierApp(NotifierApp):
    def __init__(self):
        super().__init__()
        print("[TEST] Приложение инициализировано. Запуск симуляции через 2 секунды...")
        self.root.after(2000, self.run_test_simulation)

    def run_test_simulation(self):
        print("[TEST] Запуск симуляции добавления уведомлений...")
        
        # Симулируем 6 уведомлений
        notifications = [
            ("КОНСТАНТИНОВ КОНСТАНТИН КОНСТАНТИНОВИЧ", "📊 2.5Гр x 20фр = 50.0Гр\n🧑‍⚕ Доктор: Иванов И.И.\n☢ Физик: Петров П.П.\n📝 укладка лечение с 01.06.2026"),
            ("АЛЕКСАНДРОВА АЛЕКСАНДРА АЛЕКСАНДРОВНА", "📊 3.0Гр x 15фр = 45.0Гр\n🧑‍⚕ Доктор: Сидоров С.С.\n☢ Физик: Кузнецов К.К.\n📝 лечение с 02.06.2026"),
            ("ПЕТРОВ ПЕТР ПЕТРОВИЧ", "📊 2.0Гр x 25фр = 50.0Гр\n🧑‍⚕ Доктор: Васильев В.В.\n☢ Физик: Смирнов С.С.\n📝 лечение с 03.06.2026"),
            ("ИВАНОВ ИВАН ИВАНОВИЧ", "📊 1.8Гр x 28фр = 50.4Гр\n🧑‍⚕ Доктор: Михайлов М.М.\n☢ Физик: Федоров Ф.Ф.\n📝 лечение с 04.06.2026"),
            ("СИДОРОВ СИДОР СИДОРОВИЧ", "📊 2.2Гр x 22фр = 48.4Гр\n🧑‍⚕ Доктор: Соколов С.С.\n☢ Физик: Попов П.П.\n📝 лечение с 05.06.2026"),
            ("КУЗНЕЦОВ АЛЕКСЕЙ НИКОЛАЕВИЧ", "📊 2.0Гр x 20фр = 40.0Гр\n🧑‍⚕ Доктор: Новиков Н.Н.\n☢ Физик: Козлов К.К.\n📝 лечение с 06.06.2026")
        ]

        def add_next(idx):
            if idx < len(notifications):
                fio, details = notifications[idx]
                print(f"[TEST] Добавление уведомления {idx + 1}/{len(notifications)}: {fio}")
                self.event_queue.put({"fio": fio, "details": details})
                self.root.after(1500, lambda: add_next(idx + 1))
            else:
                print("[TEST] Симуляция добавления завершена.")
                print("[TEST] Проверьте трей (правый клик -> 'История за сегодня').")
                print("[TEST] Уведомления должны автоматически закрыться через 15 секунд после их появления.")

        add_next(0)

    def add_notification(self, fio: str, details: str):
        """Переопределенный метод для теста (автозакрытие 15 секунд вместо 5 минут)."""
        try:
            # Строгий лимит ротации: не более 5 карточек одновременно
            while len(self.cards) >= 5:
                oldest_card = self.cards[-1]
                print(f"[TEST] Превышен лимит ротации (всего {len(self.cards)}). Удаляем старую карточку: {oldest_card.winfo_children()[1].winfo_children()[1].cget('text')}")
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

            card = NotificationCard(
                self.root, fio=fio, details=details,
                on_close_callback=self._remove_card,
                width=self.card_width
            )
            self.cards.insert(0, card)
            
            # В тесте автозакрытие через 15 секунд!
            self.root.after(15000, lambda c=card: self._safe_close_card(c))
            
            self._repack_cards()
        except Exception as e:
            print(f"[TEST ERROR] Ошибка при добавлении карточки: {e}")

if __name__ == '__main__':
    app = TestNotifierApp()
    app.run()
