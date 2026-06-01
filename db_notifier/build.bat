@echo off
title Teragis Notifier Build Tool

echo ====================================================
echo    Teragis Notifier Build Tool (.exe)
echo ====================================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found in your system PATH!
    echo Please install Python and add it to PATH.
    goto end
)

:: Check PyInstaller
echo [1/3] Checking PyInstaller...
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo PyInstaller was not found. Installing...
    pip install pyinstaller
) else (
    echo PyInstaller is already installed.
)
echo.

:: Install dependencies
echo [2/3] Installing dependencies from requirements.txt...
pip install -r requirements.txt
echo.

:: Run PyInstaller
echo [3/3] Building application with PyInstaller...
echo This might take a minute...
pyinstaller --onefile --noconsole --name="TeragisNotifier" --clean --paths=".." --hidden-import="identification" --distpath="..\dist" --workpath="..\build" --specpath="..\build" main.py

if %errorlevel% equ 0 (
    echo.
    echo ====================================================
    echo [SUCCESS] Build completed successfully!
    echo Executable is located at:
    echo %CD%\..\dist\TeragisNotifier.exe
    echo ====================================================
) else (
    echo.
    echo [ERROR] Build failed.
)

:end
pause
