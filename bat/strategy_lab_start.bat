@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "ROOT=%CD%"
set PYTHONUTF8=1

echo [1/3] Syncing Strategy Lab state DB...
python scripts\strategy_lab\sync_state_db.py
if errorlevel 1 goto fail

echo [2/3] Starting dashboard on http://127.0.0.1:8765 ...
start "Strategy Lab Dashboard" cmd /k "cd /d ""%ROOT%"" && set PYTHONUTF8=1 && python scripts\strategy_lab\serve_dashboard.py --host 127.0.0.1 --port 8765"

echo [3/3] Starting one-worker research loop...
start "Strategy Lab Worker" cmd /k "cd /d ""%ROOT%"" && set PYTHONUTF8=1 && bat\strategy_lab_worker_loop.bat"

timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765"
echo Strategy Lab is starting. Queue jobs with bat\strategy_lab_enqueue_smoke.bat or custom specs.
exit /b 0

:fail
echo Strategy Lab start failed.
exit /b 1
