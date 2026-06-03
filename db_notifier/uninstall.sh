#!/bin/bash
# Скрипт удаления (деинсталляции) Teragis Notifier на Linux (Xubuntu)

AUTOSTART_FILE="$HOME/.config/autostart/teragis_notifier.desktop"
CACHE_DIR="$HOME/.config/teragis_notifier"

echo "=== Деинсталляция Teragis Notifier ==="

# 1. Удаление из автозапуска
if [ -f "$AUTOSTART_FILE" ]; then
    echo "Удаление файла автозапуска..."
    rm -f "$AUTOSTART_FILE"
    echo "Приложение успешно удалено из автозапуска."
else
    echo "Файл автозапуска не найден."
fi

# 2. Удаление кэша и настроек
if [ -d "$CACHE_DIR" ]; then
    echo "Удаление директории кэша и настроек ($CACHE_DIR)..."
    rm -rf "$CACHE_DIR"
    echo "Кэш и настройки успешно удалены."
else
    echo "Директория кэша не найдена."
fi

echo ""
echo "=== Деинсталляция успешно завершена! ==="
echo "Для полной очистки вы можете вручную удалить установленные библиотеки Python:"
echo "pip3 uninstall -y psycopg2-binary pillow python-dotenv"
