@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
set "PYTHONWARNINGS=ignore:CUDA path could not be detected:UserWarning"

rem Canonical Strategy Lab farm cycle:
rem scanner/watch intake -> farm lifecycle -> compute worker -> hard validation -> paper runtime
rem -> operational paper-signal watch lane.
rem Paper/research only: public OKX market data, no .env, no AUTO_TRADE, no orders,
rem no private exchange endpoints, no Telegram.

if "%TRADING_BOT_RESEARCH_ROOT%"=="" (
  set "TRADING_BOT_RESEARCH_ROOT=%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
)

if "%STRATEGY_LAB_FARM_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_FARM_SLEEP_SECONDS=600"
if "%STRATEGY_LAB_FARM_MAX_PLAN_EVENTS%"=="" set "STRATEGY_LAB_FARM_MAX_PLAN_EVENTS=20"
if "%STRATEGY_LAB_FARM_MAX_PREPARES%"=="" set "STRATEGY_LAB_FARM_MAX_PREPARES=4"
if "%STRATEGY_LAB_FARM_MAX_ENRICH%"=="" set "STRATEGY_LAB_FARM_MAX_ENRICH=4"
if "%STRATEGY_LAB_FARM_MAX_SWEEPS%"=="" set "STRATEGY_LAB_FARM_MAX_SWEEPS=4"
if "%STRATEGY_LAB_FARM_MAX_WORKER_JOBS%"=="" set "STRATEGY_LAB_FARM_MAX_WORKER_JOBS=2"
if "%STRATEGY_LAB_FARM_MAX_PAPER_CARDS%"=="" set "STRATEGY_LAB_FARM_MAX_PAPER_CARDS=20"
if "%STRATEGY_LAB_FARM_DATA_DAYS%"=="" set "STRATEGY_LAB_FARM_DATA_DAYS=30"
if "%STRATEGY_LAB_FARM_BACKEND%"=="" set "STRATEGY_LAB_FARM_BACKEND=auto"
if "%STRATEGY_LAB_FARM_PROVIDER%"=="" set "STRATEGY_LAB_FARM_PROVIDER=okx-public"
if "%STRATEGY_LAB_PFR_DB_PATH%"=="" set "STRATEGY_LAB_PFR_DB_PATH=%TRADING_BOT_RESEARCH_ROOT%\state\strategy_lab.sqlite"
if "%STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_SCAN%"=="" set "STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_SCAN=30"
if "%STRATEGY_LAB_PAPER_SIGNALS_FETCH_TIMEOUT%"=="" set "STRATEGY_LAB_PAPER_SIGNALS_FETCH_TIMEOUT=10"

set "STRATEGY_LAB_FARM_MODE_ARG=--apply"
if /I "%STRATEGY_LAB_FARM_DRY_RUN%"=="1" set "STRATEGY_LAB_FARM_MODE_ARG=--dry-run"
set "STRATEGY_LAB_FARM_RUN_ARG=--loop"
if /I "%STRATEGY_LAB_FARM_ONCE%"=="1" set "STRATEGY_LAB_FARM_RUN_ARG=--once"
set "STRATEGY_LAB_FARM_QUIET_ARG=--quiet"
if /I "%STRATEGY_LAB_FARM_ONCE%"=="1" set "STRATEGY_LAB_FARM_QUIET_ARG="

set "STOP_FILE=%TRADING_BOT_RESEARCH_ROOT%\state\STOP_FARM_FULL_CYCLE.txt"
set "LOG_DIR=%TRADING_BOT_RESEARCH_ROOT%\logs"
set "LOG_FILE=%LOG_DIR%\farm_full_cycle_loop.log"

if not exist "%TRADING_BOT_RESEARCH_ROOT%\state" mkdir "%TRADING_BOT_RESEARCH_ROOT%\state"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if exist "%STOP_FILE%" del "%STOP_FILE%"

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
echo  caps        : prepares=%STRATEGY_LAB_FARM_MAX_PREPARES% enrich=%STRATEGY_LAB_FARM_MAX_ENRICH% sweeps=%STRATEGY_LAB_FARM_MAX_SWEEPS% worker=%STRATEGY_LAB_FARM_MAX_WORKER_JOBS%
echo  safety      : paper-only; public OKX; no orders / .env / AUTO_TRADE / private endpoints / Telegram
echo ============================================
echo.
echo Tip: stop with bat\strategy_lab_farm_full_cycle_stop.bat or Ctrl+C.
echo Status: python -m scripts.strategy_lab.status
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Continue';" ^
  "$env:PYTHONUTF8='1';" ^
  "$env:PYTHONWARNINGS='ignore:CUDA path could not be detected:UserWarning';" ^
  "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8;" ^
  "$env:TRADING_BOT_RESEARCH_ROOT='%TRADING_BOT_RESEARCH_ROOT%';" ^
  "$cmd = @('-X','utf8','-u','-m','scripts.strategy_lab.farm_loop','%STRATEGY_LAB_FARM_RUN_ARG%','%STRATEGY_LAB_FARM_MODE_ARG%','--run-worker','--run-validation','--run-paper','--run-paper-signals','--enrich-funding','--enrich-oi','--backend','%STRATEGY_LAB_FARM_BACKEND%','--provider','%STRATEGY_LAB_FARM_PROVIDER%','--pfr-db-path','%STRATEGY_LAB_PFR_DB_PATH%','--paper-signals-max-pfr-scan','%STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_SCAN%','--paper-signals-fetch-timeout','%STRATEGY_LAB_PAPER_SIGNALS_FETCH_TIMEOUT%','--max-plan-events','%STRATEGY_LAB_FARM_MAX_PLAN_EVENTS%','--max-prepares','%STRATEGY_LAB_FARM_MAX_PREPARES%','--max-enrich','%STRATEGY_LAB_FARM_MAX_ENRICH%','--max-sweeps','%STRATEGY_LAB_FARM_MAX_SWEEPS%','--max-worker-jobs','%STRATEGY_LAB_FARM_MAX_WORKER_JOBS%','--max-paper-cards','%STRATEGY_LAB_FARM_MAX_PAPER_CARDS%','--data-days','%STRATEGY_LAB_FARM_DATA_DAYS%','--sleep-seconds','%STRATEGY_LAB_FARM_SLEEP_SECONDS%','--stop-file','%STOP_FILE%','--private-root','%TRADING_BOT_RESEARCH_ROOT%','--night-mode','%STRATEGY_LAB_FARM_QUIET_ARG%') | Where-Object { $_ -ne '' };" ^
  "& python @cmd 2>&1 | Tee-Object -FilePath '%LOG_FILE%' -Append;" ^
  "exit $LASTEXITCODE"

set "RC=%ERRORLEVEL%"
echo.
echo Farm full-cycle loop exited with code %RC%.
echo Log: %LOG_FILE%
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
