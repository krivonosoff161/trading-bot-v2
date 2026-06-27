@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
set "PYTHONWARNINGS=ignore:CUDA path could not be detected:UserWarning"

rem Visible read-only Strategy Lab status monitor.
rem It exits when the farm full-cycle stop-file appears. It does not call order,
rem private exchange, Telegram, or LLM paths.

if "%TRADING_BOT_RESEARCH_ROOT%"=="" (
  set "TRADING_BOT_RESEARCH_ROOT=%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
)
if "%STRATEGY_LAB_STATUS_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_STATUS_SLEEP_SECONDS=300"

set "STOP_FILE=%TRADING_BOT_RESEARCH_ROOT%\state\STOP_FARM_FULL_CYCLE.txt"

echo ============================================
echo  Strategy Lab - Status Monitor
echo ============================================
echo  private_root: %TRADING_BOT_RESEARCH_ROOT%
echo  stop file   : %STOP_FILE%
echo  interval    : %STRATEGY_LAB_STATUS_SLEEP_SECONDS%s
echo  safety      : read-only status; no orders / .env / AUTO_TRADE
echo ============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$env:TRADING_BOT_RESEARCH_ROOT='%TRADING_BOT_RESEARCH_ROOT%';" ^
  "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8;" ^
  "while (-not (Test-Path '%STOP_FILE%')) {" ^
  "  Clear-Host;" ^
  "  Get-Date;" ^
  "  python -X utf8 -m scripts.strategy_lab.farm_status_report;" ^
  "  Start-Sleep -Seconds %STRATEGY_LAB_STATUS_SLEEP_SECONDS%;" ^
  "};" ^
  "Write-Host 'Stop file detected. Status monitor exiting.'"

set "RC=%ERRORLEVEL%"
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
