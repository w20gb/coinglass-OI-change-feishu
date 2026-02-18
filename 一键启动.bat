@echo off
setlocal

:: Title
title Binance Open Interest Monitor

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b
)

:: Install dependencies
if not exist "venv" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
)

echo [INFO] Activating venv...
call venv\Scripts\activate

echo [INFO] Checking dependencies...
pip install -r requirements.txt >nul 2>&1

:: Check Playwright Browsers
if not exist "%USERPROFILE%\AppData\Local\ms-playwright" (
    echo [INFO] Installing Playwright browsers...
    playwright install chromium
)

:loop
cls
echo [INFO] Starting Monitor Task...
echo [INFO] Press Ctrl+C to stop.
echo ---------------------------------------------------

python main.py

echo ---------------------------------------------------
echo [INFO] Task finished. Waiting for next cycle (300s)...
echo [INFO] You can change interval in config.json (but this loop is fixed to 300s for safety).
timeout /t 300 >nul

goto loop
