@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "ROOT=%CD%"
set "PYTHONUTF8=1"

if not defined STRATEGY_LAB_UNIVERSE set "STRATEGY_LAB_UNIVERSE=core_market"
if not defined STRATEGY_LAB_TIMEFRAME set "STRATEGY_LAB_TIMEFRAME=1d"
if not defined STRATEGY_LAB_PRIORITY set "STRATEGY_LAB_PRIORITY=80"
if not defined STRATEGY_LAB_PORT set "STRATEGY_LAB_PORT=8765"

set "FULL_ARG="
if /I "%STRATEGY_LAB_FULL%"=="1" set "FULL_ARG=--full"

set "NIGHT_ARG="
if /I "%STRATEGY_LAB_NIGHT_MODE%"=="1" set "NIGHT_ARG=--night-mode"

set "PLAN_MODE=--apply"
if /I "%STRATEGY_LAB_START_DRY_RUN%"=="1" set "PLAN_MODE=--dry-run"

echo Strategy Lab safe start
echo   universe  : %STRATEGY_LAB_UNIVERSE%
echo   timeframe : %STRATEGY_LAB_TIMEFRAME%
echo   mode      : quiet_desktop%NIGHT_ARG%
echo   priority  : %STRATEGY_LAB_PRIORITY%
echo.

echo [1/5] Syncing Strategy Lab state DB...
python scripts\strategy_lab\sync_state_db.py
if errorlevel 1 goto fail

echo [2/5] Planning and queueing bounded research jobs...
python -X utf8 -m scripts.strategy_lab.enqueue_research_plan --universe "%STRATEGY_LAB_UNIVERSE%" --timeframe "%STRATEGY_LAB_TIMEFRAME%" %FULL_ARG% %NIGHT_ARG% %PLAN_MODE% --priority %STRATEGY_LAB_PRIORITY%
if errorlevel 1 goto fail

if /I "%STRATEGY_LAB_START_DRY_RUN%"=="1" (
  echo Dry-run complete. Nothing was queued and no windows were started.
  exit /b 0
)

if /I "%STRATEGY_LAB_START_NO_WINDOWS%"=="1" (
  echo Queue prepared. Window startup skipped by STRATEGY_LAB_START_NO_WINDOWS=1.
  exit /b 0
)

echo [3/5] Starting dashboard on http://127.0.0.1:%STRATEGY_LAB_PORT% ...
start "Strategy Lab Dashboard" cmd /k "cd /d ""%ROOT%"" && set ""PYTHONUTF8=1"" && python scripts\strategy_lab\serve_dashboard.py --host 127.0.0.1 --port %STRATEGY_LAB_PORT%"

echo [4/5] Starting one-worker research loop...
start "Strategy Lab Worker" cmd /k "cd /d ""%ROOT%"" && set ""PYTHONUTF8=1"" && bat\strategy_lab_worker_loop.bat %NIGHT_ARG%"

timeout /t 2 /nobreak >nul
echo [5/5] Opening dashboard...
start "" "http://127.0.0.1:%STRATEGY_LAB_PORT%"
echo Strategy Lab is starting. Research-plan jobs are queued idempotently; worker is throttled by resource_policy.yaml.
exit /b 0

:fail
echo Strategy Lab start failed.
exit /b 1
