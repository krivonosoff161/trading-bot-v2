@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo Starting Trading Bot V2...
echo.

start "Telegram Bot" cmd /k "cd /d %~dp0 && python -u scripts\telegram_bot.py"
timeout /t 3 /nobreak > nul

start "WS Scanner" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_scanner.py"
start "Pump Engine" cmd /k "cd /d %~dp0 && python -u scripts\ws\coin_screener.py && python -u scripts\ws\ws_pump_engine.py"

echo [OK] Telegram Bot started
echo [OK] WS Scanner started
echo [OK] Pump Engine started
echo.
pause > nul
