@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Telegram Bot
cd /d "%~dp0"

REM ==================================================================
REM  PRODUCT LAUNCHER (pivot 31.05.2026) — Concierge Analyzer only.
REM  Starts the Telegram analyzer bot (scripts\telegram_bot.py).
REM  Scout context (forward_series) runs SEPARATELY via Windows Task
REM  "ScoutDaily" -> bat\run_scout_daily.bat (once a day).
REM  Legacy / FROZEN trading stack (screeners, Tape, order-placing
REM  main.py) is NOT launched here. See start_all.bat (do not run
REM  unless reviving research). Direction = analyzer-first product.
REM ==================================================================

echo ==========================================
echo   TRADING BOT V2 - PRODUCT
echo   Concierge Analyzer (Telegram bot)
echo ==========================================
echo.

REM Clear proxy env vars (prevents REST API blocking)
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set PYTHONUTF8=1

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

REM Check config + secrets
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
echo Starting Concierge Analyzer bot...
echo Clients message the Telegram bot -^> chart analysis.
echo Press Ctrl+C (or close window) to stop.
echo.

python -u scripts\telegram_bot.py

echo.
echo [INFO] Bot stopped. Logs in logs\telegram_bot.log
echo.
pause
