@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Trading Bot V2 - Start
cd /d "%~dp0"

echo ================================
echo   TRADING BOT V2 - START
echo ================================
echo.

REM Clear proxy env vars (prevents REST API blocking)
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

REM Check dependencies
python -c "import numpy, aiohttp, loguru, yaml" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Dependencies missing! Run: pip install -r requirements.txt
    pause
    exit /b 1
)

REM Check config
if not exist "config.yaml" (
    echo [ERROR] config.yaml not found!
    pause
    exit /b 1
)

if not exist ".env" (
    echo [ERROR] .env not found! Create it from .env.example
    pause
    exit /b 1
)

REM Create logs dir if not exists
if not exist "logs" mkdir "logs"

echo [OK] Python found
echo [OK] Dependencies OK
echo [OK] config.yaml found
echo [OK] .env found
echo.
echo Starting bot...
echo Press Ctrl+C to stop.
echo.

python main.py

echo.
echo [INFO] Bot stopped. Logs saved in logs\
echo.
pause
