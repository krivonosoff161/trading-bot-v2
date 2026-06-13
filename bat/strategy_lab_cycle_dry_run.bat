@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Controlled research cycle, DRY-RUN only: inspects state and shows what would be
rem generated/queued/prepared/run. Writes nothing except the status report, queues
rem nothing, runs no worker, makes no network call.
python -X utf8 -m scripts.strategy_lab.research_cycle --dry-run
echo.
echo This was a DRY-RUN (no queue writes, no worker, no network).
echo To apply (capped, still no network unless you add prepare flags), run explicitly:
echo   python -m scripts.strategy_lab.research_cycle --apply --max-proposals 5 --max-queue 5 --max-worker-jobs 1
set "RC=%ERRORLEVEL%"
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
