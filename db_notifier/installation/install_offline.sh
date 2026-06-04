#!/bin/bash
# Скрипт установки Teragis Notifier на Linux (Xubuntu) БЕЗ ИНТЕРНЕТА (Offline)
set -e

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

# Универсальное определение структуры каталогов
if [ -f "$SRC_DIR/../tnotif.py" ]; then
    DB_NOTIFIER_SRC="$(cd "$SRC_DIR/.." && pwd)"
    SCRIPTS_SRC="$SRC_DIR"
elif [ -f "$SRC_DIR/db_notifier/tnotif.py" ]; then
    DB_NOTIFIER_SRC="$SRC_DIR/db_notifier"
    if [ -d "$SRC_DIR/db_notifier/installation" ]; then
        SCRIPTS_SRC="$SRC_DIR/db_notifier/installation"
    else
        SCRIPTS_SRC="$SRC_DIR"
    fi
else
    DB_NOTIFIER_SRC="$SRC_DIR"
    SCRIPTS_SRC="$SRC_DIR"
fi

# Пути к локальным пакетам
DEB_DIR="$SCRIPTS_SRC/packages/deb"
PIP_DIR="$SCRIPTS_SRC/packages/pip"

if [ ! -d "$DEB_DIR" ] || [ ! -d "$PIP_DIR" ]; then
    # Если запуск из корня флешки офлайн-версии
    DEB_DIR="$SRC_DIR/packages/deb"
    PIP_DIR="$SRC_DIR/packages/pip"
fi

echo "=== Установка системных зависимостей из локальной папки (требуются права sudo) ==="
if [ -d "$DEB_DIR" ] && [ "$(ls -A "$DEB_DIR")" ]; then
    # Получаем архитектуру текущей системы (например, i386 или amd64)
    ARCH=$(dpkg --print-architecture)
    echo "Архитектура текущей системы: $ARCH"
    
    echo "Установка локальных пакетов .deb для архитектуры $ARCH и all..."
    # Устанавливаем только пакеты с соответствующей архитектурой и архитектурой all
    sudo dpkg -i "$DEB_DIR"/*_"${ARCH}".deb "$DEB_DIR"/*_all.deb || true
    
    echo "Проверка и исправление возможных проблем с зависимостями..."
    sudo apt-get install -f -y || true
else
    echo "ВНИМАНИЕ: Папка с локальными .deb пакетами пуста или не найдена!"
fi

# Проверка работоспособности tkinter и pip3
echo "=== Проверка установленных зависимостей Python ==="
if python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "Библиотека tkinter (GUI) успешно найдена."
else
    echo "ОШИБКА: Библиотека tkinter не установлена. Проверьте логи установки deb-пакетов."
    exit 1
fi

if pip3 --version >/dev/null 2>&1; then
    echo "Менеджер пакетов pip3 успешно найден."
else
    echo "ОШИБКА: pip3 не найден. Проверьте логи установки deb-пакетов."
    exit 1
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
if [ -f "$SCRIPTS_SRC/install_offline.sh" ]; then
    cp -f "$SCRIPTS_SRC/install_offline.sh" "$INSTALL_DIR/"
fi
if [ -f "$SCRIPTS_SRC/.env" ]; then
    echo "=== Копирование конфигурационного файла .env ==="
    cp -f "$SCRIPTS_SRC/.env" "$INSTALL_DIR/db_notifier/"
fi

# Копируем также саму папку с офлайн пакетами в папку приложения на всякий случай
cp -rf "$DEB_DIR/../.." "$INSTALL_DIR/"

echo "=== Установка Python-библиотек из локального кэша ==="
if [ -d "$PIP_DIR" ] && [ "$(ls -A "$PIP_DIR")" ]; then
    pip3 install --user --no-index --find-links="$PIP_DIR" -r "$INSTALL_DIR/db_notifier/requirements.txt" --break-system-packages 2>/dev/null || \
    pip3 install --user --no-index --find-links="$PIP_DIR" -r "$INSTALL_DIR/db_notifier/requirements.txt"
else
    echo "ОШИБКА: Папка с локальными .whl файлами пуста или не найдена!"
    exit 1
fi

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
