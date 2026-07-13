@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
set "PYTHONWARNINGS=ignore:CUDA path could not be detected:UserWarning"

rem Headless product paper loop.
rem Runs only the canonical farm full-cycle loop: no dashboard, no graph viewer,
rem no status-monitor window. Paper/research only: no AUTO_TRADE, no orders,
rem no old main.py, no private exchange endpoints.
rem Outcome-learning advisory reviews are bounded and enabled by default here.

if "%TRADING_BOT_RESEARCH_ROOT%"=="" (
  set "TRADING_BOT_RESEARCH_ROOT=%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
)

if "%STRATEGY_LAB_FARM_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_FARM_SLEEP_SECONDS=180"
if "%STRATEGY_LAB_FARM_MAX_PLAN_EVENTS%"=="" set "STRATEGY_LAB_FARM_MAX_PLAN_EVENTS=8"
if "%STRATEGY_LAB_FARM_MAX_PREPARES%"=="" set "STRATEGY_LAB_FARM_MAX_PREPARES=1"
if "%STRATEGY_LAB_FARM_MAX_ENRICH%"=="" set "STRATEGY_LAB_FARM_MAX_ENRICH=1"
if "%STRATEGY_LAB_FARM_MAX_SWEEPS%"=="" set "STRATEGY_LAB_FARM_MAX_SWEEPS=1"
if "%STRATEGY_LAB_FARM_MAX_WORKER_JOBS%"=="" set "STRATEGY_LAB_FARM_MAX_WORKER_JOBS=1"
if "%STRATEGY_LAB_FARM_MAX_VALIDATIONS%"=="" set "STRATEGY_LAB_FARM_MAX_VALIDATIONS=2"
if "%STRATEGY_LAB_MAIN_PAPER_RUNTIME_LIMIT%"=="" set "STRATEGY_LAB_MAIN_PAPER_RUNTIME_LIMIT=50"
if "%STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE%"=="" set "STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE=20"
if "%STRATEGY_LAB_PAPER_SIGNALS_MAX_LIVE_FETCHES%"=="" set "STRATEGY_LAB_PAPER_SIGNALS_MAX_LIVE_FETCHES=4"
if "%STRATEGY_LAB_PAPER_SIGNALS_MAX_NETWORK_FETCHES%"=="" set "STRATEGY_LAB_PAPER_SIGNALS_MAX_NETWORK_FETCHES=12"
if "%STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES%"=="" set "STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES=4"
if "%STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM%"=="" set "STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM=0"

rem Keep the feedback loop closed without giving LLMs trading authority.
if "%STRATEGY_LAB_RUN_AGENT_ROLE_REVIEWS%"=="" set "STRATEGY_LAB_RUN_AGENT_ROLE_REVIEWS=1"
if "%STRATEGY_LAB_AGENT_ROLE_MAX_OUTCOMES%"=="" set "STRATEGY_LAB_AGENT_ROLE_MAX_OUTCOMES=2"
if "%STRATEGY_LAB_AGENT_ROLE_MAX_VALIDATOR%"=="" set "STRATEGY_LAB_AGENT_ROLE_MAX_VALIDATOR=1"
if "%STRATEGY_LAB_AGENT_ROLE_MAX_SOURCES%"=="" set "STRATEGY_LAB_AGENT_ROLE_MAX_SOURCES=0"
if "%STRATEGY_LAB_AGENT_ROLE_TIMEOUT%"=="" set "STRATEGY_LAB_AGENT_ROLE_TIMEOUT=30"
if "%STRATEGY_LAB_CALCULATOR_TIMEOUT%"=="" set "STRATEGY_LAB_CALCULATOR_TIMEOUT=45"

if /I "%STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM%"=="1" (
  set "STRATEGY_LAB_PAPER_TELEGRAM_SEND=1"
) else (
  set "STRATEGY_LAB_PAPER_TELEGRAM_SEND=0"
)

echo ============================================
echo  Paper Product Headless Loop
echo ============================================
echo  repo        : %CD%
echo  private_root: %TRADING_BOT_RESEARCH_ROOT%
echo  cadence     : farm=%STRATEGY_LAB_FARM_SLEEP_SECONDS%s
echo  windows     : farm loop only; dashboard=off graph=off status_window=off
echo  learning    : outcome_reviews=%STRATEGY_LAB_RUN_AGENT_ROLE_REVIEWS% max_outcomes=%STRATEGY_LAB_AGENT_ROLE_MAX_OUTCOMES% max_validator=%STRATEGY_LAB_AGENT_ROLE_MAX_VALIDATOR% max_sources=%STRATEGY_LAB_AGENT_ROLE_MAX_SOURCES%
echo  telegram    : send=%STRATEGY_LAB_PAPER_TELEGRAM_SEND% target=active subscription users
echo  safety      : paper-only; no old main.py / AUTO_TRADE / orders / private endpoints
echo ============================================
echo.
echo Stop farm loop: bat\strategy_lab_farm_full_cycle_stop.bat
echo Fast status: python -m scripts.strategy_lab.farm_status_report --fast
echo.

call bat\strategy_lab_farm_full_cycle_loop.bat
set "RC=%ERRORLEVEL%"
endlocal
exit /b %RC%
