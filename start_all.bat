@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo Starting Trading Bot V2...
echo.

start "Telegram Bot" cmd /k "cd /d %~dp0 && python -u scripts\telegram_bot.py"
timeout /t 3 /nobreak > nul

start "Main Screener" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_main_screener.py"
start "Live Screener" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_screener_live.py"
timeout /t 5 /nobreak > nul
start "Pump Orchestrator" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_pump_orchestrator.py"
start "Smart Pump" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_smart_pump.py"

echo [OK] Telegram Bot started
echo [OK] Main Screener started (shadow mode)
echo [OK] Live Screener started
echo [OK] Pump Orchestrator started
echo [OK] Smart Pump started (shadow mode)
echo.
pause > nul
