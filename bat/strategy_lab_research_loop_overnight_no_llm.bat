@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
set "STRATEGY_LAB_NIGHT_MODE=1"

rem Overnight no-LLM research loop. Safe default: no paid LLM, no data fetch.
rem Runs for up to 8 hours, then stops automatically.
rem No live trading, no order engine, no AUTO_TRADE, no .env loading.

echo ============================================
echo  Strategy Lab - Overnight No-LLM Research Loop
echo ============================================
echo.
echo  Private root: %TRADING_BOT_RESEARCH_ROOT%
if "%TRADING_BOT_RESEARCH_ROOT%"=="" echo  (using default private root)
echo  Duration:     480 minutes
echo  Sleep:        60 seconds
echo  Max queued:   20
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

python -X utf8 -m scripts.strategy_lab.research_loop --apply --night-mode --duration-minutes 480 --sleep-seconds 60 --max-queued 20 --max-worker-jobs-per-iteration 1
set "RC=%ERRORLEVEL%"

echo.
echo Overnight loop finished.
echo Morning command: bat\strategy_lab_morning_report.bat
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
