@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "ROOT=%CD%"
set PYTHONUTF8=1

echo [1/5] Syncing Strategy Lab state DB...
python scripts\strategy_lab\sync_state_db.py
if errorlevel 1 goto fail

echo [2/5] Queueing smoke experiment...
python scripts\strategy_lab\enqueue_experiment.py --spec configs\strategy_lab\l2_smoke.json --priority 50
if errorlevel 1 goto fail

echo [3/5] Running one queued job...
python scripts\strategy_lab\worker_once.py
if errorlevel 1 goto fail

echo [4/5] Starting dashboard on http://127.0.0.1:8765 ...
start "Strategy Lab Dashboard" cmd /k "cd /d ""%ROOT%"" && set PYTHONUTF8=1 && python scripts\strategy_lab\serve_dashboard.py --host 127.0.0.1 --port 8765"

echo [5/5] Opening dashboard...
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765"
echo Demo complete. Full results are in the private trading-bot-research workspace.
exit /b 0

:fail
echo Strategy Lab demo failed.
exit /b 1
