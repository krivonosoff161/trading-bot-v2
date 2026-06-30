@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
set "PYTHONWARNINGS=ignore:CUDA path could not be detected:UserWarning"

rem Canonical Strategy Lab farm cycle:
rem scanner/watch intake -> farm lifecycle -> compute worker -> hard validation -> paper runtime
rem -> operational paper-signal watch lane.
rem Paper/research only: public OKX market data, no AUTO_TRADE, no orders,
rem no private exchange endpoints. Telegram paper delivery is opt-in via
rem STRATEGY_LAB_PAPER_TELEGRAM_SEND=1 and goes only to active bot subscribers.

if "%TRADING_BOT_RESEARCH_ROOT%"=="" (
  set "TRADING_BOT_RESEARCH_ROOT=%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
)

if "%STRATEGY_LAB_FARM_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_FARM_SLEEP_SECONDS=600"
if "%STRATEGY_LAB_FARM_MAX_PLAN_EVENTS%"=="" set "STRATEGY_LAB_FARM_MAX_PLAN_EVENTS=20"
if "%STRATEGY_LAB_FARM_MAX_PREPARES%"=="" set "STRATEGY_LAB_FARM_MAX_PREPARES=4"
if "%STRATEGY_LAB_FARM_MAX_ENRICH%"=="" set "STRATEGY_LAB_FARM_MAX_ENRICH=4"
if "%STRATEGY_LAB_FARM_MAX_SWEEPS%"=="" set "STRATEGY_LAB_FARM_MAX_SWEEPS=4"
if "%STRATEGY_LAB_FARM_MAX_WORKER_JOBS%"=="" set "STRATEGY_LAB_FARM_MAX_WORKER_JOBS=2"
if "%STRATEGY_LAB_FARM_MAX_VALIDATIONS%"=="" set "STRATEGY_LAB_FARM_MAX_VALIDATIONS=10"
if "%STRATEGY_LAB_FARM_MAX_PAPER_CARDS%"=="" set "STRATEGY_LAB_FARM_MAX_PAPER_CARDS=20"
if "%STRATEGY_LAB_FARM_DATA_DAYS%"=="" set "STRATEGY_LAB_FARM_DATA_DAYS=30"
if "%STRATEGY_LAB_FARM_BACKEND%"=="" set "STRATEGY_LAB_FARM_BACKEND=auto"
if "%STRATEGY_LAB_FARM_PROVIDER%"=="" set "STRATEGY_LAB_FARM_PROVIDER=okx-public"
if "%STRATEGY_LAB_PFR_DB_PATH%"=="" set "STRATEGY_LAB_PFR_DB_PATH=%TRADING_BOT_RESEARCH_ROOT%\state\strategy_lab.sqlite"
if "%STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE%"=="" set "STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE=20"
if "%STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_SCAN%"=="" set "STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_SCAN=30"
if "%STRATEGY_LAB_PAPER_SIGNALS_PFR_RESERVED%"=="" set "STRATEGY_LAB_PAPER_SIGNALS_PFR_RESERVED=2"
if "%STRATEGY_LAB_PAPER_SIGNALS_FETCH_TIMEOUT%"=="" set "STRATEGY_LAB_PAPER_SIGNALS_FETCH_TIMEOUT=10"
if "%STRATEGY_LAB_MAIN_PAPER_RUNTIME_LIMIT%"=="" set "STRATEGY_LAB_MAIN_PAPER_RUNTIME_LIMIT=50"
if "%STRATEGY_LAB_RUN_CALCULATOR_ADVISOR%"=="" set "STRATEGY_LAB_RUN_CALCULATOR_ADVISOR=0"
if "%STRATEGY_LAB_RUN_AGENT_ROLE_REVIEWS%"=="" set "STRATEGY_LAB_RUN_AGENT_ROLE_REVIEWS=0"
if "%STRATEGY_LAB_CALCULATOR_PROVIDER%"=="" set "STRATEGY_LAB_CALCULATOR_PROVIDER=ollama"
if "%STRATEGY_LAB_CALCULATOR_MODEL%"=="" set "STRATEGY_LAB_CALCULATOR_MODEL=calculator"
if "%STRATEGY_LAB_CALCULATOR_BASE_URL%"=="" set "STRATEGY_LAB_CALCULATOR_BASE_URL=http://127.0.0.1:11434/v1"
if "%STRATEGY_LAB_CALCULATOR_TIMEOUT%"=="" set "STRATEGY_LAB_CALCULATOR_TIMEOUT=120"
if "%STRATEGY_LAB_AGENT_ROLE_PROVIDER%"=="" set "STRATEGY_LAB_AGENT_ROLE_PROVIDER=alibaba"
if "%STRATEGY_LAB_AGENT_ROLE_MODEL%"=="" set "STRATEGY_LAB_AGENT_ROLE_MODEL=qwen-plus"
if "%STRATEGY_LAB_AGENT_ROLE_TIMEOUT%"=="" set "STRATEGY_LAB_AGENT_ROLE_TIMEOUT=60"
if "%STRATEGY_LAB_AGENT_ROLE_MAX_OUTCOMES%"=="" set "STRATEGY_LAB_AGENT_ROLE_MAX_OUTCOMES=1"
if "%STRATEGY_LAB_AGENT_ROLE_MAX_VALIDATOR%"=="" set "STRATEGY_LAB_AGENT_ROLE_MAX_VALIDATOR=1"
if "%STRATEGY_LAB_AGENT_ROLE_MAX_SOURCES%"=="" set "STRATEGY_LAB_AGENT_ROLE_MAX_SOURCES=1"

set "STRATEGY_LAB_FARM_MODE_ARG=--apply"
if /I "%STRATEGY_LAB_FARM_DRY_RUN%"=="1" set "STRATEGY_LAB_FARM_MODE_ARG=--dry-run"
set "STRATEGY_LAB_FARM_RUN_ARG=--loop"
if /I "%STRATEGY_LAB_FARM_ONCE%"=="1" set "STRATEGY_LAB_FARM_RUN_ARG=--once"
set "STRATEGY_LAB_FARM_QUIET_ARG=--quiet"
if /I "%STRATEGY_LAB_FARM_ONCE%"=="1" set "STRATEGY_LAB_FARM_QUIET_ARG="
set "STRATEGY_LAB_CALCULATOR_ADVISOR_ARG="
if /I "%STRATEGY_LAB_RUN_CALCULATOR_ADVISOR%"=="1" set "STRATEGY_LAB_CALCULATOR_ADVISOR_ARG=--run-calculator-advisor"
set "STRATEGY_LAB_AGENT_ROLE_REVIEWS_ARG="
if /I "%STRATEGY_LAB_RUN_AGENT_ROLE_REVIEWS%"=="1" set "STRATEGY_LAB_AGENT_ROLE_REVIEWS_ARG=--run-agent-role-reviews"

set "STOP_FILE=%TRADING_BOT_RESEARCH_ROOT%\state\STOP_FARM_FULL_CYCLE.txt"
set "LOG_DIR=%TRADING_BOT_RESEARCH_ROOT%\logs"
set "LOG_FILE=%LOG_DIR%\farm_full_cycle_loop.log"

if not exist "%TRADING_BOT_RESEARCH_ROOT%\state" mkdir "%TRADING_BOT_RESEARCH_ROOT%\state"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================
echo  Strategy Lab - Farm Full Cycle Loop
echo ============================================
echo  repo        : %CD%
echo  private_root: %TRADING_BOT_RESEARCH_ROOT%
echo  stop file   : %STOP_FILE%
echo  log         : %LOG_FILE%
echo  pfr_db      : %STRATEGY_LAB_PFR_DB_PATH%
echo  sleep       : %STRATEGY_LAB_FARM_SLEEP_SECONDS%s
echo  mode        : %STRATEGY_LAB_FARM_MODE_ARG% %STRATEGY_LAB_FARM_RUN_ARG%
echo  caps        : prepares=%STRATEGY_LAB_FARM_MAX_PREPARES% enrich=%STRATEGY_LAB_FARM_MAX_ENRICH% sweeps=%STRATEGY_LAB_FARM_MAX_SWEEPS% worker=%STRATEGY_LAB_FARM_MAX_WORKER_JOBS% validations=%STRATEGY_LAB_FARM_MAX_VALIDATIONS%
echo  paper caps  : observe=%STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE% pfr_scan=%STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_SCAN% pfr_reserved=%STRATEGY_LAB_PAPER_SIGNALS_PFR_RESERVED% runtime=%STRATEGY_LAB_MAIN_PAPER_RUNTIME_LIMIT%
echo  llm roles   : calculator=%STRATEGY_LAB_RUN_CALCULATOR_ADVISOR%/%STRATEGY_LAB_CALCULATOR_MODEL% agent_reviews=%STRATEGY_LAB_RUN_AGENT_ROLE_REVIEWS%/%STRATEGY_LAB_AGENT_ROLE_MODEL%
echo  telegram    : paper_send=%STRATEGY_LAB_PAPER_TELEGRAM_SEND% target=active subscription users
echo  safety      : paper-only; public OKX; no orders / AUTO_TRADE / private endpoints
echo ============================================
echo.
echo Tip: stop with bat\strategy_lab_farm_full_cycle_stop.bat or Ctrl+C.
echo Fast health: python -m scripts.strategy_lab.operational_health --private-root "%TRADING_BOT_RESEARCH_ROOT%" --pfr-db-path "%STRATEGY_LAB_PFR_DB_PATH%" --fail-on-blocked
echo Monitor status: python -m scripts.strategy_lab.farm_status_report --fast
echo Detailed audit: python -m scripts.strategy_lab.farm_status_report
echo.

python -X utf8 -m scripts.strategy_lab.operational_health --private-root "%TRADING_BOT_RESEARCH_ROOT%" --pfr-db-path "%STRATEGY_LAB_PFR_DB_PATH%" --fail-on-blocked
if errorlevel 1 (
  echo.
  echo Preflight blocked. Fix readiness gates with status=blocked before starting the farm loop.
  if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
  endlocal
  exit /b 2
)
echo.

if exist "%STOP_FILE%" del "%STOP_FILE%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Continue';" ^
  "$env:PYTHONUTF8='1';" ^
  "$env:PYTHONWARNINGS='ignore:CUDA path could not be detected:UserWarning';" ^
  "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8;" ^
  "$env:TRADING_BOT_RESEARCH_ROOT='%TRADING_BOT_RESEARCH_ROOT%';" ^
  "$cmd = @('-X','utf8','-u','-m','scripts.strategy_lab.farm_loop','%STRATEGY_LAB_FARM_RUN_ARG%','%STRATEGY_LAB_FARM_MODE_ARG%','--run-worker','--run-validation','--run-paper','--run-paper-signals','%STRATEGY_LAB_CALCULATOR_ADVISOR_ARG%','%STRATEGY_LAB_AGENT_ROLE_REVIEWS_ARG%','--enrich-funding','--enrich-oi','--backend','%STRATEGY_LAB_FARM_BACKEND%','--provider','%STRATEGY_LAB_FARM_PROVIDER%','--pfr-db-path','%STRATEGY_LAB_PFR_DB_PATH%','--paper-signals-max-observe','%STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE%','--paper-signals-max-pfr-scan','%STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_SCAN%','--paper-signals-pfr-reserved','%STRATEGY_LAB_PAPER_SIGNALS_PFR_RESERVED%','--paper-signals-fetch-timeout','%STRATEGY_LAB_PAPER_SIGNALS_FETCH_TIMEOUT%','--main-paper-runtime-limit','%STRATEGY_LAB_MAIN_PAPER_RUNTIME_LIMIT%','--calculator-provider','%STRATEGY_LAB_CALCULATOR_PROVIDER%','--calculator-model','%STRATEGY_LAB_CALCULATOR_MODEL%','--calculator-base-url','%STRATEGY_LAB_CALCULATOR_BASE_URL%','--calculator-timeout','%STRATEGY_LAB_CALCULATOR_TIMEOUT%','--agent-role-provider','%STRATEGY_LAB_AGENT_ROLE_PROVIDER%','--agent-role-model','%STRATEGY_LAB_AGENT_ROLE_MODEL%','--agent-role-timeout','%STRATEGY_LAB_AGENT_ROLE_TIMEOUT%','--agent-role-max-outcomes','%STRATEGY_LAB_AGENT_ROLE_MAX_OUTCOMES%','--agent-role-max-validator','%STRATEGY_LAB_AGENT_ROLE_MAX_VALIDATOR%','--agent-role-max-sources','%STRATEGY_LAB_AGENT_ROLE_MAX_SOURCES%','--max-plan-events','%STRATEGY_LAB_FARM_MAX_PLAN_EVENTS%','--max-prepares','%STRATEGY_LAB_FARM_MAX_PREPARES%','--max-enrich','%STRATEGY_LAB_FARM_MAX_ENRICH%','--max-sweeps','%STRATEGY_LAB_FARM_MAX_SWEEPS%','--max-worker-jobs','%STRATEGY_LAB_FARM_MAX_WORKER_JOBS%','--max-validations','%STRATEGY_LAB_FARM_MAX_VALIDATIONS%','--max-paper-cards','%STRATEGY_LAB_FARM_MAX_PAPER_CARDS%','--data-days','%STRATEGY_LAB_FARM_DATA_DAYS%','--sleep-seconds','%STRATEGY_LAB_FARM_SLEEP_SECONDS%','--stop-file','%STOP_FILE%','--private-root','%TRADING_BOT_RESEARCH_ROOT%','--night-mode','%STRATEGY_LAB_FARM_QUIET_ARG%') | Where-Object { $_ -ne '' };" ^
  "& python @cmd 2>&1 | Tee-Object -FilePath '%LOG_FILE%' -Append;" ^
  "exit $LASTEXITCODE"

set "RC=%ERRORLEVEL%"
echo.
echo Farm full-cycle loop exited with code %RC%.
echo Log: %LOG_FILE%
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
