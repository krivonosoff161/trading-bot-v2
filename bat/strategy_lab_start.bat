@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "ROOT=%CD%"
set "PYTHONUTF8=1"

echo [1/6] Building data inventory...
python scripts\strategy_lab\build_data_inventory.py --spec configs\strategy_lab\l2_smoke.json
if errorlevel 1 goto fail

echo [2/6] Syncing Strategy Lab state DB...
python scripts\strategy_lab\sync_state_db.py
if errorlevel 1 goto fail

echo [3/6] Ensuring starter research pack is queued...
python scripts\strategy_lab\enqueue_pack.py --dir configs\strategy_lab\starter --priority 50
if errorlevel 1 goto fail

echo [4/6] Generating and queueing bounded follow-up proposals...
python scripts\strategy_lab\autopilot_once.py --max-proposals 8 --priority 70
if errorlevel 1 goto fail

echo [5/6] Starting dashboard on http://127.0.0.1:8765 ...
start "Strategy Lab Dashboard" cmd /k "cd /d ""%ROOT%"" && set ""PYTHONUTF8=1"" && python scripts\strategy_lab\serve_dashboard.py --host 127.0.0.1 --port 8765"

echo [6/6] Starting one-worker research loop...
start "Strategy Lab Worker" cmd /k "cd /d ""%ROOT%"" && set ""PYTHONUTF8=1"" && bat\strategy_lab_worker_loop.bat"

timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765"
echo Strategy Lab is starting. Starter specs and bounded autopilot proposals are queued idempotently.
exit /b 0

:fail
echo Strategy Lab start failed.
exit /b 1
