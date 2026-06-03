#!/bin/bash
# Скрипт запуска Teragis Notifier на Linux (Xubuntu)
cd "$(dirname "$0")"/db_notifier
python3 tnotif.py
