@echo off
setlocal
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
set "STRATEGY_LAB_NIGHT_MODE=1"
if "%STRATEGY_LAB_LLM_ENABLED%"=="" set "STRATEGY_LAB_LLM_ENABLED=1"
if "%STRATEGY_LAB_LLM_DAILY_CAP%"=="" set "STRATEGY_LAB_LLM_DAILY_CAP=10"
if "%STRATEGY_LAB_LLM_RATE_RUB_PER_1K%"=="" set "STRATEGY_LAB_LLM_RATE_RUB_PER_1K=0.5"

echo Strategy Lab overnight LLM loop
echo - bounded single run, night policy, one worker step per iteration
echo - LLM must pass env gates and daily cap; output is validated by code
echo - no live trading, no order engine
echo.
python -X utf8 -m scripts.strategy_lab.research_loop --apply --llm-propose --night-mode --load-env .env --duration-minutes 480 --sleep-seconds 60 --max-queued 20 --max-candidates 5 --max-worker-jobs-per-iteration 1
echo.
echo Done. Check: python -m scripts.strategy_lab.status
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
