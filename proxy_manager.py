import os
import subprocess
import psutil
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class ProxyManager:
    """Управление внешними прокси-процессами (Xray, MTProto)."""

    def __init__(self, use_ws: bool):
        if use_ws:
            self.proxy_exe = "xmtproto.exe"
            self.proxy_folder = "xmtproto"
        else:
            self.proxy_exe = "xray.exe"
            self.proxy_folder = "xray"

        self.proxy_path = os.path.join(os.getcwd(), self.proxy_folder, self.proxy_exe)

    def start(self) -> None:
        """Запуск прокси-клиента с проверкой, не запущен ли он уже."""
        if not os.path.exists(self.proxy_path):
            logger.warning(f"[ProxyManager] Файл прокси не найден: {self.proxy_path}")
            return

        try:
            # Проверяем, запущен ли процесс с таким именем
            is_running = any(proc.name() == self.proxy_exe for proc in psutil.process_iter(['name']))
            
            if is_running:
                logger.info(f"[ProxyManager] {self.proxy_exe} уже запущен.")
                return

            # Если не запущен — запускаем
            subprocess.Popen(
                self.proxy_path,
                cwd=os.path.dirname(self.proxy_path),
                shell=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            logger.info(f"[ProxyManager] Прокси {self.proxy_exe} успешно запущен")
            
        except Exception as e:
            logger.error(f"[ProxyManager] Ошибка при запуске {self.proxy_exe}: {e}")

    def stop(self) -> None:
        """Принудительное завершение прокси."""
        try:
            # Используем taskkill для Windows
            subprocess.call(
                f'taskkill /f /im {self.proxy_exe} /fi "STATUS eq RUNNING" > nul 2>&1', 
                shell=True
            )
            logger.info(f"[ProxyManager] Прокси {self.proxy_exe} остановлен")
        except Exception as e:
            logger.debug(f"[ProxyManager] Ошибка при остановке прокси: {e}")
