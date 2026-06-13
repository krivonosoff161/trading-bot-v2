@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem One controlled research session, DRY-RUN only: inspect -> generate -> data check
rem -> queue (capped) -> worker. Writes only the status report; queues nothing, runs
rem no worker, makes no network call, and never calls a paid LLM.
python -X utf8 -m scripts.strategy_lab.research_session --dry-run
echo.
echo This was a DRY-RUN (no queue writes, no worker, no network, no paid LLM).
echo To apply (capped, still no network, no LLM), run explicitly:
echo   python -m scripts.strategy_lab.research_session --apply --max-queued 5 --max-worker-jobs 1
set "RC=%ERRORLEVEL%"
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
