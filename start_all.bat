@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo Starting Trading Bot V2...
echo.

start "Telegram Bot" cmd /k "chcp 65001 > nul && set PYTHONUTF8=1 && python -u scripts\telegram_bot.py"
timeout /t 3 /nobreak > nul

start "WS Scanner" cmd /k "chcp 65001 > nul && set PYTHONUTF8=1 && python -u scripts\ws\ws_scanner.py"

echo [OK] Telegram Bot started
echo [OK] WS Scanner started
echo.
echo Close this window or press any key to exit.
pause > nul
