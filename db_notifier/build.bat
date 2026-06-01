@echo off
chcp 65001 > nul
title Сборка Teragis Notifier (.exe)

echo ====================================================
echo    Сборка Teragis Notifier в исполняемый файл (.exe)
echo ====================================================
echo.

:: Проверка наличия Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не найден в системе!
    echo Пожалуйста, добавьте Python в переменную среды PATH.
    goto end
)

:: Установка/проверка PyInstaller
echo [1/3] Проверка наличия PyInstaller...
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo PyInstaller не найден. Установка PyInstaller...
    pip install pyinstaller
) else (
    echo PyInstaller обнаружен.
)
echo.

:: Установка зависимостей
echo [2/3] Установка зависимостей из requirements.txt...
pip install -r requirements.txt
echo.

:: Запуск сборки
echo [3/3] Сборка приложения через PyInstaller...
echo Это может занять около минуты...
pyinstaller --onefile --noconsole --name="TeragisNotifier" --clean main.py

if %errorlevel% equ 0 (
    echo.
    echo ====================================================
    echo [УСПЕХ] Сборка успешно завершена!
    echo Исполняемый файл находится по пути:
    echo %CD%\dist\TeragisNotifier.exe
    echo ====================================================
) else (
    echo.
    echo [ОШИБКА] Произошел сбой при сборке приложения.
)

:end
pause
