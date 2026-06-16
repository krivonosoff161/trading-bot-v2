@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Overnight bounded research loop with local Ollama calculator.
rem The model is advisory JSON-only: it proposes small candidates, code validates.
rem No API keys. No paid LLM. No tool execution. No live trading. No order engine.

if "%STRATEGY_LAB_LOOP_MINUTES%"=="" set "STRATEGY_LAB_LOOP_MINUTES=720"
if "%STRATEGY_LAB_LOOP_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_LOOP_SLEEP_SECONDS=300"
if "%STRATEGY_LAB_LOOP_MAX_QUEUED%"=="" set "STRATEGY_LAB_LOOP_MAX_QUEUED=5"
if "%STRATEGY_LAB_LOOP_MAX_CANDIDATES%"=="" set "STRATEGY_LAB_LOOP_MAX_CANDIDATES=1"

set "STRATEGY_LAB_LLM_ENABLED=1"
set "STRATEGY_LAB_LLM_PROVIDER=ollama"
set "STRATEGY_LAB_LLM_BASE_URL=http://127.0.0.1:11434/v1"
set "STRATEGY_LAB_LLM_MODEL_CHEAP=calculator"
set "STRATEGY_LAB_LLM_TIMEOUT=120"
set "STRATEGY_LAB_LLM_RATE_RUB_PER_1K=0"

echo ============================================
echo  Strategy Lab - Overnight Calculator Loop
echo ============================================
echo.
echo  Duration:     %STRATEGY_LAB_LOOP_MINUTES% minutes
echo  Sleep:        %STRATEGY_LAB_LOOP_SLEEP_SECONDS% seconds
echo  Max queued:   %STRATEGY_LAB_LOOP_MAX_QUEUED%
echo  Candidates:   %STRATEGY_LAB_LOOP_MAX_CANDIDATES% per LLM step
echo  LLM:          local Ollama calculator
echo  Cost:         0 RUB configured
echo  Data fetch:   OFF by default
echo  Worker jobs:  1 per iteration
echo  Live trading: OFF
echo  Order engine: OFF
echo.
echo  Stop gracefully: bat\strategy_lab_graceful_stop.bat
echo  Morning report:  bat\strategy_lab_morning_report.bat
echo ============================================
echo.

python -X utf8 -m scripts.strategy_lab.research_loop --apply --night-mode --llm-propose --duration-minutes %STRATEGY_LAB_LOOP_MINUTES% --sleep-seconds %STRATEGY_LAB_LOOP_SLEEP_SECONDS% --max-queued %STRATEGY_LAB_LOOP_MAX_QUEUED% --max-candidates %STRATEGY_LAB_LOOP_MAX_CANDIDATES% --max-worker-jobs-per-iteration 1 --max-llm-contract-failures 3
set "RC=%ERRORLEVEL%"

echo.
echo Calculator loop finished.
echo Morning command: bat\strategy_lab_morning_report.bat
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
