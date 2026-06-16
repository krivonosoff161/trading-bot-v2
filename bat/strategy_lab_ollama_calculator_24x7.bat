@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem 24/7 Strategy Lab loop with the local Ollama calculator.
rem Important: this puts the LLM dispatcher on GPU when Ollama offloads it.
rem Scanner-driven sweeps can request backend=auto/gpu/cpu; unsupported modes
rem fall back honestly and record runtime evidence in metrics.json.
rem No API keys. No paid LLM. No tool execution. No live trading. No order engine.

where nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo WARNING: nvidia-smi not found. Ollama may still run, but GPU offload cannot be verified here.
)

if "%STRATEGY_LAB_LOOP_MINUTES%"=="" set "STRATEGY_LAB_LOOP_MINUTES=720"
if "%STRATEGY_LAB_LOOP_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_LOOP_SLEEP_SECONDS=300"
if "%STRATEGY_LAB_LOOP_MAX_QUEUED%"=="" set "STRATEGY_LAB_LOOP_MAX_QUEUED=5"
if "%STRATEGY_LAB_LOOP_MAX_CANDIDATES%"=="" set "STRATEGY_LAB_LOOP_MAX_CANDIDATES=1"
if "%STRATEGY_LAB_RESTART_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_RESTART_SLEEP_SECONDS=30"

set "STRATEGY_LAB_LLM_ENABLED=1"
set "STRATEGY_LAB_LLM_PROVIDER=ollama"
set "STRATEGY_LAB_LLM_BASE_URL=http://127.0.0.1:11434/v1"
set "STRATEGY_LAB_LLM_MODEL_CHEAP=calculator"
set "STRATEGY_LAB_LLM_TIMEOUT=120"
set "STRATEGY_LAB_LLM_RATE_RUB_PER_1K=0"

echo ============================================
echo  Strategy Lab - 24/7 Ollama Calculator Loop
echo ============================================
echo.
echo  Model:        local Ollama calculator
echo  Chunk:        %STRATEGY_LAB_LOOP_MINUTES% minutes, then restart
echo  Sleep:        %STRATEGY_LAB_LOOP_SLEEP_SECONDS% seconds
echo  Max queued:   %STRATEGY_LAB_LOOP_MAX_QUEUED%
echo  Candidates:   %STRATEGY_LAB_LOOP_MAX_CANDIDATES% per LLM step
echo  Worker jobs:  1 per iteration
echo  Sweep backend: from queued spec (cpu/gpu/auto), runtime fallback recorded
echo  Live trading: OFF
echo  Order engine: OFF
echo.
echo  During first LLM call, verify: ollama ps
echo  Expected for LLM only: calculator:latest PROCESSOR 100%% GPU
echo  Stop gracefully: bat\strategy_lab_graceful_stop.bat
echo ============================================
echo.

where nvidia-smi >nul 2>&1
if not errorlevel 1 (
    nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu --format=csv
    echo.
)

:farm_loop
python -X utf8 -c "from src.research_lab.stop_intent import is_stop_requested; from src.research_lab.paths import DEFAULT_PRIVATE_ROOT; from pathlib import Path; import os, sys; p = Path(os.getenv('TRADING_BOT_RESEARCH_ROOT', str(DEFAULT_PRIVATE_ROOT))); sys.exit(3 if is_stop_requested(p) else 0)"
if "%ERRORLEVEL%"=="3" goto stopped

echo [%date% %time%] Starting 24/7 calculator chunk...
python -X utf8 -m scripts.strategy_lab.research_loop --apply --night-mode --llm-propose --duration-minutes %STRATEGY_LAB_LOOP_MINUTES% --sleep-seconds %STRATEGY_LAB_LOOP_SLEEP_SECONDS% --max-queued %STRATEGY_LAB_LOOP_MAX_QUEUED% --max-candidates %STRATEGY_LAB_LOOP_MAX_CANDIDATES% --max-worker-jobs-per-iteration 1 --max-llm-contract-failures 3
set "RC=%ERRORLEVEL%"

python -X utf8 -c "from src.research_lab.stop_intent import is_stop_requested; from src.research_lab.paths import DEFAULT_PRIVATE_ROOT; from pathlib import Path; import os, sys; p = Path(os.getenv('TRADING_BOT_RESEARCH_ROOT', str(DEFAULT_PRIVATE_ROOT))); sys.exit(3 if is_stop_requested(p) else 0)"
if "%ERRORLEVEL%"=="3" goto stopped

echo [%date% %time%] Calculator chunk finished with code %RC%.
echo Restarting in %STRATEGY_LAB_RESTART_SLEEP_SECONDS% seconds. Press Ctrl+C to stop the window.
timeout /t %STRATEGY_LAB_RESTART_SLEEP_SECONDS% /nobreak
goto farm_loop

:stopped
echo.
echo Stop intent detected. Calculator loop stopped.
echo Status command: python -m scripts.strategy_lab.status
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b 0
