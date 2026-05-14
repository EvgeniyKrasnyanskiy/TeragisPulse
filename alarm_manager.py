import tkinter as tk
import customtkinter
import winsound
import threading
import time
from datetime import datetime, time as dt_time
import logging
from typing import Optional, Callable

logger = logging.getLogger(__name__)

class AlarmManager:
    """Управление будильником и часами в GUI."""

    def __init__(self, clock_label, after_callback, default_time="19:30"):
        self.clock_label = clock_label
        self.after = after_callback
        self.alarm_time: Optional[dt_time] = None
        self.alarm_triggered = False
        self.default_time = default_time

    def tick(self) -> None:
        """Обновление часов и проверка будильника."""
        try:
            now = datetime.now()
            current_time = now.time().replace(second=0, microsecond=0)

            if self.alarm_time is not None:
                # Режим будильника
                self.clock_label.configure(
                    text=self.alarm_time.strftime('%H:%M'),
                    text_color="#FF3333"
                )
                if (not self.alarm_triggered and
                        current_time.hour == self.alarm_time.hour and
                        current_time.minute == self.alarm_time.minute):
                    self.alarm_triggered = True
                    self._trigger_alarm()
            else:
                # Обычные часы
                self.clock_label.configure(
                    text=now.strftime('%H:%M'),
                    text_color="#00CC44"
                )
        except Exception as e:
            logger.error(f"[AlarmManager] Ошибка часов: {e}")

        self.after(5000, self.tick)

    def _trigger_alarm(self) -> None:
        """Воспроизведение звука и мигание."""
        def play_alarm():
            for _ in range(14):
                try:
                    winsound.Beep(1200, 400)
                    time.sleep(0.1)
                except:
                    break

        threading.Thread(target=play_alarm, daemon=True).start()
        self._blink_label(12)
        self.after(8000, self._reset_alarm)

    def _blink_label(self, count: int) -> None:
        if count <= 0:
            return
        current = self.clock_label.cget("text_color")
        next_color = "white" if current == "#FF3333" else "#FF3333"
        self.clock_label.configure(text_color=next_color)
        self.after(500, lambda: self._blink_label(count - 1))

    def _reset_alarm(self) -> None:
        self.alarm_time = None
        self.alarm_triggered = False

    def open_dialog(self, master: tk.Widget) -> None:
        """Диалог установки будильника."""
        dialog = customtkinter.CTkToplevel(master)
        dialog.title("Будильник")
        
        # Центрирование окна
        w, h = 260, 140
        sw = dialog.winfo_screenwidth()
        sh = dialog.winfo_screenheight()
        x = (sw // 2) - (w // 2)
        y = (sh // 2) - (h // 2)
        dialog.geometry(f"{w}x{h}+{x}+{y}")
        
        dialog.resizable(False, False)
        dialog.lift()
        dialog.focus_force()
        dialog.attributes('-topmost', True)

        customtkinter.CTkLabel(dialog, text="Введите время (ЧЧ:ММ):").pack(pady=(16, 4))

        entry = customtkinter.CTkEntry(dialog, width=100, justify="center", font=("Courier", 14))
        if self.alarm_time is not None:
            entry.insert(0, self.alarm_time.strftime('%H:%M'))
        else:
            entry.insert(0, self.default_time)
        entry.pack(pady=4)
        entry.focus()

        btn_frame = customtkinter.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=10)

        def on_set():
            raw = entry.get().strip()
            try:
                t = datetime.strptime(raw, '%H:%M').time()
                self.alarm_time = t
                self.alarm_triggered = False
                dialog.destroy()
            except ValueError:
                entry.configure(border_color="red")

        def on_reset():
            self.alarm_time = None
            self.alarm_triggered = False
            dialog.destroy()

        customtkinter.CTkButton(btn_frame, text="Установить", width=110, command=on_set).pack(side=tk.LEFT, padx=5)
        customtkinter.CTkButton(btn_frame, text="Сбросить", width=100, fg_color="#555", hover_color="#777", command=on_reset).pack(side=tk.LEFT, padx=5)

        entry.bind("<Return>", lambda e: on_set())
