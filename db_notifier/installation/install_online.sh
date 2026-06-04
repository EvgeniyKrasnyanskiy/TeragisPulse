#!/bin/bash
# Скрипт установки Teragis Notifier на Linux (Xubuntu) С ИНТЕРНЕТОМ
set -e

# Функция восстановления настроек при выходе (в случае ошибок или успешного завершения)
cleanup() {
    echo "=== Восстановление исходных системных настроек ==="
    if [ "$DNS_CHANGED" = true ] && [ -f /etc/resolv.conf.bak ]; then
        echo "Восстановление /etc/resolv.conf..."
        sudo cp /etc/resolv.conf.bak /etc/resolv.conf
    fi
    if [ "$SOURCES_CHANGED" = true ] && [ -f /etc/apt/sources.list.bak ]; then
        echo "Восстановление /etc/apt/sources.list..."
        sudo cp /etc/apt/sources.list.bak /etc/apt/sources.list
    fi
}
# Регистрируем функцию очистки при выходе из скрипта
trap cleanup EXIT

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

echo "=== Резервное копирование системных файлов (требуются права sudo) ==="
DNS_CHANGED=false
SOURCES_CHANGED=false

if [ ! -f /etc/resolv.conf.bak ]; then
    sudo cp /etc/resolv.conf /etc/resolv.conf.bak
fi
if [ ! -f /etc/apt/sources.list.bak ]; then
    sudo cp /etc/apt/sources.list /etc/apt/sources.list.bak
fi

# Проверка интернет-соединения и DNS
echo "=== Проверка подключения к интернету ==="
if ! ping -c 1 old-releases.ubuntu.com >/dev/null 2>&1; then
    echo "Не удалось связаться с old-releases.ubuntu.com. Проверяем доступность внешнего IP (8.8.8.8)..."
    if ping -c 1 8.8.8.8 >/dev/null 2>&1; then
        echo "Интернет доступен по IP, но DNS не работает. Временно настраиваем Google DNS..."
        # Записываем Google DNS
        echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf >/dev/null
        DNS_CHANGED=true
    else
        echo "ПРЕДУПРЕЖДЕНИЕ: Внешний IP 8.8.8.8 недоступен. Возможно, интернета действительно нет."
        echo "Если у вас раздача по USB с телефона, убедитесь, что включен режим модема."
    fi
else
    echo "Интернет и DNS работают корректно."
fi

# Настройка архивных репозиториев для Ubuntu 16.04 (так как стандартные mirrors отключены)
echo "=== Временная перенастройка репозиториев на old-releases.ubuntu.com ==="
sudo sed -i -re 's/([a-z]{2}\.)?archive.ubuntu.com/old-releases.ubuntu.com/g' /etc/apt/sources.list
sudo sed -i -re 's/security.ubuntu.com/old-releases.ubuntu.com/g' /etc/apt/sources.list
SOURCES_CHANGED=true

echo "=== Обновление списков пакетов и установка системных зависимостей ==="
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-tk libpq-dev build-essential

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
if [ -f "$SCRIPTS_SRC/install_online.sh" ]; then
    cp -f "$SCRIPTS_SRC/install_online.sh" "$INSTALL_DIR/"
fi
if [ -f "$SCRIPTS_SRC/.env" ]; then
    echo "=== Копирование конфигурационного файла .env ==="
    cp -f "$SCRIPTS_SRC/.env" "$INSTALL_DIR/db_notifier/"
fi

echo "=== Установка Python-библиотек ==="
pip3 install --user -r "$INSTALL_DIR/db_notifier/requirements.txt" --break-system-packages 2>/dev/null || pip3 install --user -r "$INSTALL_DIR/db_notifier/requirements.txt"

echo "=== Настройка права выполнения ==="
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
