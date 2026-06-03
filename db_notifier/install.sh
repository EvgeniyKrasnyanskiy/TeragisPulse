#!/bin/bash
# Скрипт установки Teragis Notifier на Linux (Xubuntu)
set -e

echo "=== Установка системных зависимостей (требуются права sudo) ==="
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-tk python3-dev libpq-dev build-essential libjpeg-dev zlib1g-dev

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

# Определение структуры каталогов (из репозитория или из распакованного дистрибутива)
if [ -f "$SRC_DIR/tnotif.py" ]; then
    # Скрипт запущен внутри самой папки db_notifier
    DB_NOTIFIER_SRC="$SRC_DIR"
    SCRIPTS_SRC="$SRC_DIR"
else
    # Скрипт запущен из корня дистрибутива, где db_notifier — подпапка
    DB_NOTIFIER_SRC="$SRC_DIR/db_notifier"
    SCRIPTS_SRC="$SRC_DIR"
fi

echo "=== Определение пути к Рабочему столу ==="
DESKTOP_DIR=$(xdg-user-dir DESKTOP 2>/dev/null || echo "")
if [ -z "$DESKTOP_DIR" ] || [ ! -d "$DESKTOP_DIR" ]; then
    if [ -d "$HOME/Рабочий стол" ]; then
        DESKTOP_DIR="$HOME/Рабочий стол"
    else
        DESKTOP_DIR="$HOME/Desktop"
    fi
fi
mkdir -p "$DESKTOP_DIR"

INSTALL_DIR="$DESKTOP_DIR/TeragisNotifier"
echo "=== Создание рабочей директории на Рабочем столе: $INSTALL_DIR ==="
mkdir -p "$INSTALL_DIR"

echo "=== Копирование файлов приложения ==="
mkdir -p "$INSTALL_DIR/db_notifier"
cp -rf "$DB_NOTIFIER_SRC"/. "$INSTALL_DIR/db_notifier/"
cp -f "$SCRIPTS_SRC/start.sh" "$SCRIPTS_SRC/autostart.sh" "$SCRIPTS_SRC/uninstall.sh" "$SCRIPTS_SRC/instruction.txt" "$INSTALL_DIR/"

echo "=== Установка Python-библиотек в пространство пользователя ==="
pip3 install --user -r "$INSTALL_DIR/db_notifier/requirements.txt" --break-system-packages 2>/dev/null || pip3 install --user -r "$INSTALL_DIR/db_notifier/requirements.txt"

echo "=== Настройка прав выполнения ==="
chmod +x "$INSTALL_DIR/start.sh" "$INSTALL_DIR/autostart.sh" "$INSTALL_DIR/uninstall.sh"

echo "=== Создание директории кэша с безопасными правами ==="
mkdir -p ~/.config/teragis_notifier
chmod 700 ~/.config/teragis_notifier

echo "=== Создание ярлыка запуска на Рабочем столе ==="
cat <<EOF > "$DESKTOP_DIR/teragis_notifier.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=Teragis Notifier
Comment=Запуск Teragis Notifier (клиент уведомлений)
Exec="$INSTALL_DIR/start.sh"
Icon=utilities-terminal
Terminal=false
Categories=Utility;
EOF
chmod +x "$DESKTOP_DIR/teragis_notifier.desktop"

echo "=== Создание ярлыка запуска в папке приложения ==="
cat <<EOF > "$INSTALL_DIR/Teragis Notifier.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=Teragis Notifier
Comment=Запуск Teragis Notifier (клиент уведомлений)
Exec="$INSTALL_DIR/start.sh"
Icon=utilities-terminal
Terminal=false
Categories=Utility;
EOF
chmod +x "$INSTALL_DIR/Teragis Notifier.desktop"

echo "=== Настройка автозапуска при входе в систему ==="
"$INSTALL_DIR/autostart.sh"

echo "=== Установка успешно завершена! ==="
echo "Программа установлена в папку на Рабочем столе: $INSTALL_DIR"
echo "Ярлык для запуска создан на Рабочем столе: $DESKTOP_DIR/teragis_notifier.desktop"
