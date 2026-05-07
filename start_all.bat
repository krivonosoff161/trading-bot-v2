@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo Starting Trading Bot V2...
echo.

start "Telegram Bot" cmd /k "cd /d %~dp0 && python -u scripts\telegram_bot.py"
timeout /t 3 /nobreak > nul

start "WS Scanner" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_scanner.py"
start "Live Screener" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_screener_live.py"
timeout /t 5 /nobreak > nul
start "Pump Engine V2" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_pump_engine_v2.py"

echo [OK] Telegram Bot started
echo [OK] WS Scanner started
echo [OK] Live Screener started
echo [OK] Pump Engine V2 started
echo.
pause > nul
