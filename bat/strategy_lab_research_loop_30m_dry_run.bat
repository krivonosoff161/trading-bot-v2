@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Time-bounded research loop, DRY-RUN for 30 minutes: each iteration inspects state
rem and shows what WOULD be queued/run, writing a heartbeat + loop report. No queue
rem writes, no worker, no network, no paid LLM. Stops itself after 30 minutes.
echo This DRY-RUN loop runs for ~30 minutes (press Ctrl+C to stop early). No writes, no network.
python -X utf8 -m scripts.strategy_lab.research_loop --dry-run --duration-minutes 30 --sleep-seconds 60
echo.
echo This was a DRY-RUN loop (no queue writes, no worker, no network, no paid LLM).
echo Heartbeat + report: state/research_loop/ under the private research root.
set "RC=%ERRORLEVEL%"
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
