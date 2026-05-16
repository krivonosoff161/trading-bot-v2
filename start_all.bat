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
start "Pump Orchestrator" cmd /k "cd /d %~dp0 && python -u scripts\ws\run_pump_watchdog.py"
start "Smart Pump" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_smart_pump.py"
start "BB Fade" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_bb_fade.py"
start "Tape Recorder" cmd /k "cd /d %~dp0 && python -u scripts\analysis\tape_recorder.py"

echo [OK] Telegram Bot started
echo [OK] Main Screener started (shadow mode)
echo [OK] Live Screener started
echo [OK] Pump Orchestrator started
echo [OK] Smart Pump started (shadow mode)
echo [OK] BB Fade started
echo [OK] Tape Recorder started
echo.
pause > nul
