#!/bin/bash
# Скрипт добавления Teragis Notifier в автозапуск Xubuntu (XFCE)

AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/start.sh"

cat <<EOF > "$AUTOSTART_DIR/teragis_notifier.desktop"
[Desktop Entry]
Type=Application
Name=Teragis Notifier
Comment=Фоновые уведомления о готовности планов лечения
Exec="$SCRIPT_PATH"
Icon=utilities-terminal
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
EOF

chmod +x "$AUTOSTART_DIR/teragis_notifier.desktop"

echo "=== Приложение добавлено в автозапуск Xubuntu! ==="
echo "Файл автозапуска создан по пути: $AUTOSTART_DIR/teragis_notifier.desktop"
