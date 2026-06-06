@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Scanner V0 (info-edge)
cd /d "%~dp0"

REM ==================================================================
REM  INFO-EDGE SCANNER V0 — forward logger (paper, GO/NO-GO cards).
REM  Loop: RSS -> page_extract -> GO/NO-GO brain -> scanner_journal.jsonl
REM  -> Telegram channel (if SCANNER_CHAT_ID set in .env).
REM  NO money / NO orders (isolation test guards this). See SCANNER_SPEC.md.
REM  Tweak INTERVAL / LIMIT below. Close window or Ctrl+C to stop.
REM ==================================================================

set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set PYTHONUTF8=1

REM seconds between scans (1800 = 30 min)
set INTERVAL=1800
REM max cards per scan (token-cost cap)
set LIMIT=5
REM 1 = durable SQLite buffer pipeline, 0 = legacy direct RSS+listings pipeline
set USE_BUFFER=1

set SCANNER_ARGS=--limit %LIMIT%
set MODE_LABEL=direct RSS+listings
if "%USE_BUFFER%"=="1" (
    set SCANNER_ARGS=--buffer --limit %LIMIT%
    set MODE_LABEL=SQLite buffer
)

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)
python -c "import trafilatura, requests, aiohttp" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Scanner deps missing! Run: pip install -r requirements.txt
    pause
    exit /b 1
)
if not exist ".env" (
    echo [ERROR] .env not found!
    pause
    exit /b 1
)

echo ==========================================
echo   INFO-EDGE SCANNER V0  (forward logger)
echo   mode: %MODE_LABEL%
echo   loop every %INTERVAL%s, up to %LIMIT% cards/scan
echo   journal: logs\scout\scanner_journal.jsonl
echo ==========================================
echo Delivery: SCANNER_CHAT_ID in .env (channel). If unset - journal only.
echo Press Ctrl+C or close window to stop.
echo.

:loop
echo --- scan %date% %time% ---
python -u src\scout\scanner_v0.py %SCANNER_ARGS%
echo --- next scan in %INTERVAL%s ---
timeout /t %INTERVAL% /nobreak >nul
goto loop
