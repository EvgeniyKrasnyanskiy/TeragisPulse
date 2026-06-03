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

# 2. Удаление ярлыка с Рабочего стола
DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "")
if [ -z "$DESKTOP_DIR" ] || [ ! -d "$DESKTOP_DIR" ]; then
    if [ -d "$HOME/Рабочий стол" ]; then
        DESKTOP_DIR="$HOME/Рабочий стол"
    else
        DESKTOP_DIR="$HOME/Desktop"
    fi
fi

if [ -f "$DESKTOP_DIR/teragis_notifier.desktop" ]; then
    echo "Удаление ярлыка с Рабочего стола..."
    rm -f "$DESKTOP_DIR/teragis_notifier.desktop"
    echo "Ярлык успешно удален."
else
    echo "Ярлык на Рабочем столе не найден."
fi

# 3. Удаление кэша и настроек
if [ -d "$CACHE_DIR" ]; then
    echo "Удаление директории кэша и настроек ($CACHE_DIR)..."
    rm -rf "$CACHE_DIR"
    echo "Кэш и настройки успешно удалены."
else
    echo "Директория кэша не найдена."
fi

# 4. Удаление рабочей директории TeragisNotifier
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -d "$INSTALL_DIR" ] && [ "$(basename "$INSTALL_DIR")" = "TeragisNotifier" ]; then
    echo "Удаление папки приложения ($INSTALL_DIR)..."
    # Запускаем удаление в фоновом режиме, чтобы скрипт завершил работу без ошибок блокировки
    (sleep 0.5 && rm -rf "$INSTALL_DIR") &
    echo "Папка приложения запланирована к удалению."
fi

echo ""
echo "=== Деинсталляция успешно завершена! ==="
