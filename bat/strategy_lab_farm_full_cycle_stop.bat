@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

if "%TRADING_BOT_RESEARCH_ROOT%"=="" (
  set "TRADING_BOT_RESEARCH_ROOT=%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
)

set "STOP_FILE=%TRADING_BOT_RESEARCH_ROOT%\state\STOP_FARM_FULL_CYCLE.txt"
if not exist "%TRADING_BOT_RESEARCH_ROOT%\state" mkdir "%TRADING_BOT_RESEARCH_ROOT%\state"

echo Strategy Lab - Stop Farm Full Cycle
echo.
echo stop file: %STOP_FILE%
echo stop requested at %date% %time% > "%STOP_FILE%"
echo.
echo The farm loop will exit after the current cycle.
echo Status: python -m scripts.strategy_lab.status
echo.
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b 0
