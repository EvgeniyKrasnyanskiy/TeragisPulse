#!/bin/bash
# Скрипт автоматического запуска Teragis Notifier на Linux (Xubuntu)
# Переходим в директорию, где находится этот скрипт, и заходим в db_notifier
cd "$(dirname "$0")"/db_notifier
python3 tnotif.py
