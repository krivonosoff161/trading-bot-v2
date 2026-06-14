@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
set "STRATEGY_LAB_NIGHT_MODE=1"
if "%STRATEGY_LAB_LOOP_MINUTES%"=="" set "STRATEGY_LAB_LOOP_MINUTES=480"
if "%STRATEGY_LAB_LOOP_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_LOOP_SLEEP_SECONDS=60"
if "%STRATEGY_LAB_LOOP_MAX_QUEUED%"=="" set "STRATEGY_LAB_LOOP_MAX_QUEUED=20"

rem Overnight no-LLM research loop. Safe default: no paid LLM, no data fetch.
rem Runs for a bounded duration, then stops automatically.
rem No live trading, no order engine, no AUTO_TRADE, no .env loading.

echo ============================================
echo  Strategy Lab - Overnight No-LLM Research Loop
echo ============================================
echo.
echo  Private root: %TRADING_BOT_RESEARCH_ROOT%
if "%TRADING_BOT_RESEARCH_ROOT%"=="" echo  (using default private root)
echo  Duration:     %STRATEGY_LAB_LOOP_MINUTES% minutes
echo  Sleep:        %STRATEGY_LAB_LOOP_SLEEP_SECONDS% seconds
echo  Max queued:   %STRATEGY_LAB_LOOP_MAX_QUEUED%
echo  Worker jobs:  1 per iteration
echo  LLM:          DISABLED
echo  Data provider: null
echo  Night mode:   ON
echo  Live trading: OFF
echo  Order engine: OFF
echo.
echo  Morning command: bat\strategy_lab_morning_report.bat
echo ============================================
echo.

python -X utf8 -m scripts.strategy_lab.research_loop --apply --night-mode --duration-minutes %STRATEGY_LAB_LOOP_MINUTES% --sleep-seconds %STRATEGY_LAB_LOOP_SLEEP_SECONDS% --max-queued %STRATEGY_LAB_LOOP_MAX_QUEUED% --max-worker-jobs-per-iteration 1
set "RC=%ERRORLEVEL%"

echo.
echo Overnight loop finished.
echo Morning command: bat\strategy_lab_morning_report.bat
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
