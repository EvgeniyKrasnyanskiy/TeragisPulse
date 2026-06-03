#!/bin/bash
# Скрипт установки зависимостей для Teragis Notifier на Linux (Xubuntu)

set -e

echo "=== Установка системных зависимостей (требуются права sudo) ==="
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-tk

echo "=== Установка Python-библиотек в пространство пользователя ==="
cd "$(dirname "$0")"
pip3 install --user -r db_notifier/requirements.txt --break-system-packages 2>/dev/null || pip3 install --user -r db_notifier/requirements.txt

echo "=== Настройка прав выполнения ==="
chmod +x start.sh autostart.sh uninstall.sh

echo "=== Создание директории кэша с безопасными правами ==="
mkdir -p ~/.config/teragis_notifier
chmod 700 ~/.config/teragis_notifier

echo "=== Установка успешно завершена! ==="
echo "Для запуска используйте: ./start.sh"
echo "Для добавления в автозагрузку: ./autostart.sh"
