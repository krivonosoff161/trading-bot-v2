@echo off
cd /d %~dp0
echo Starting Live Screener + Pump Orchestrator...
start "Live Screener" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_screener_live.py"
timeout /t 5 /nobreak > nul
start "Pump Orchestrator" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_pump_orchestrator.py"
echo [OK] Live Screener started
echo [OK] Pump Orchestrator started
pause > nul
