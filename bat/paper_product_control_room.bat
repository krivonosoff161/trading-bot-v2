@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
set "PYTHONWARNINGS=ignore:CUDA path could not be detected:UserWarning"

rem Product-facing paper operator room.
rem This is the preferred visible launcher when the goal is "the bot works and
rem produces main-style paper cards" without touching old main.py or live orders.
rem It delegates to the canonical Strategy Lab control room with product cadence
rem defaults and keeps Telegram network sending off unless explicitly enabled.

if "%TRADING_BOT_RESEARCH_ROOT%"=="" (
  set "TRADING_BOT_RESEARCH_ROOT=%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
)

if "%STRATEGY_LAB_FARM_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_FARM_SLEEP_SECONDS=180"
if "%STRATEGY_LAB_STATUS_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_STATUS_SLEEP_SECONDS=120"
if "%STRATEGY_LAB_PAPER_TELEGRAM_SEND_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_PAPER_TELEGRAM_SEND_SLEEP_SECONDS=120"
if "%STRATEGY_LAB_MAIN_PAPER_RUNTIME_LIMIT%"=="" set "STRATEGY_LAB_MAIN_PAPER_RUNTIME_LIMIT=50"
if "%STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE%"=="" set "STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE=20"
if "%STRATEGY_LAB_FARM_MAX_WORKER_JOBS%"=="" set "STRATEGY_LAB_FARM_MAX_WORKER_JOBS=2"
if "%STRATEGY_LAB_FARM_MAX_VALIDATIONS%"=="" set "STRATEGY_LAB_FARM_MAX_VALIDATIONS=10"
if "%STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM%"=="" set "STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM=0"

if /I "%STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM%"=="1" (
  set "STRATEGY_LAB_PAPER_TELEGRAM_SEND=1"
) else (
  set "STRATEGY_LAB_PAPER_TELEGRAM_SEND=0"
)

echo ============================================
echo  Paper Product Control Room
echo ============================================
echo  repo        : %CD%
echo  private_root: %TRADING_BOT_RESEARCH_ROOT%
echo  cadence     : farm=%STRATEGY_LAB_FARM_SLEEP_SECONDS%s status=%STRATEGY_LAB_STATUS_SLEEP_SECONDS%s
echo  telegram    : send=%STRATEGY_LAB_PAPER_TELEGRAM_SEND% target=active subscription users
echo  safety      : paper-only; no old main.py / AUTO_TRADE / orders / private endpoints
echo ============================================
echo.
echo Use bat\paper_product_control_room_send.bat only after reviewing preview/delivery status.
echo.

call bat\strategy_lab_control_room.bat
set "RC=%ERRORLEVEL%"
endlocal
exit /b %RC%
