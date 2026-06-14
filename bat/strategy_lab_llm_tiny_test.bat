@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Tiny live LLM test. This is the only bat here that can spend money.
rem It refuses to run unless LLM is explicitly enabled and capped.

if not "%STRATEGY_LAB_LLM_ENABLED%"=="1" (
  echo.
  echo Strategy Lab LLM Tiny Test - REFUSED
  echo.
  echo LLM is not enabled. To run this test:
  echo   1. Set STRATEGY_LAB_LLM_ENABLED=1
  echo   2. Set STRATEGY_LAB_LLM_PROVIDER=alibaba or qwen or openai-compatible
  echo   3. Set STRATEGY_LAB_LLM_BASE_URL, STRATEGY_LAB_LLM_API_KEY, STRATEGY_LAB_LLM_MODEL_CHEAP
  echo   4. Set STRATEGY_LAB_LLM_DAILY_CAP=2
  echo   5. Re-run this bat
  echo.
  echo Do not use overnight LLM until this tiny test passes.
  if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
  endlocal
  exit /b 2
)

if "%STRATEGY_LAB_LLM_DAILY_CAP%"=="" (
  echo.
  echo Strategy Lab LLM Tiny Test - REFUSED
  echo.
  echo STRATEGY_LAB_LLM_DAILY_CAP is not set. Set it to a small value, for example 2.
  if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
  endlocal
  exit /b 2
)

if "%STRATEGY_LAB_LLM_PROVIDER%"=="" (
  echo.
  echo Strategy Lab LLM Tiny Test - REFUSED
  echo.
  echo STRATEGY_LAB_LLM_PROVIDER is not set. Set it to alibaba, qwen, or openai-compatible.
  if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
  endlocal
  exit /b 2
)

echo ============================================
echo  Strategy Lab - LLM Tiny Live Test
echo ============================================
echo.
echo  WARNING: This makes real LLM API calls and can cost money.
echo  Daily cap: %STRATEGY_LAB_LLM_DAILY_CAP% RUB
echo  Provider:  %STRATEGY_LAB_LLM_PROVIDER%
echo  Duration:  5 minutes
echo  Max candidates: 2
echo  Max queued: 3
echo.
echo  No live trading. No order engine.
echo ============================================
echo.

python -X utf8 -m scripts.strategy_lab.research_loop --apply --llm-propose --load-env .env --duration-minutes 5 --sleep-seconds 30 --max-queued 3 --max-candidates 2 --max-worker-jobs-per-iteration 1 --max-llm-contract-failures 3
set "RC=%ERRORLEVEL%"

echo.
echo Tiny test finished. Check: python -m scripts.strategy_lab.status
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
