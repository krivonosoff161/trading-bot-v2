@echo off
cd /d %~dp0
echo Starting Live Screener + Pump Engine V2...
start "Live Screener" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_screener_live.py"
timeout /t 5 /nobreak > nul
start "Pump Engine V2" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_pump_engine_v2.py"
echo [OK] Live Screener started
echo [OK] Pump Engine V2 started
pause > nul
