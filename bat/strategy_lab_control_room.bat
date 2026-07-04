@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
set "PYTHONWARNINGS=ignore:CUDA path could not be detected:UserWarning"

rem Visible Strategy Lab operator control room.
rem Opens separate visible windows for the canonical paper/research farm loop,
rem dashboard, private graph viewer, and periodic status. No hidden services.
rem Paper/research only: no AUTO_TRADE, no orders, no private endpoints.
rem Optional paper Telegram delivery is owned by the farm full-cycle loop when
rem STRATEGY_LAB_PAPER_TELEGRAM_SEND=1. Do not start the standalone sender loop
rem beside this control room; it is a manual fallback only.

if "%TRADING_BOT_RESEARCH_ROOT%"=="" (
  set "TRADING_BOT_RESEARCH_ROOT=%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
)
if "%STRATEGY_LAB_STATUS_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_STATUS_SLEEP_SECONDS=300"
if "%STRATEGY_LAB_PFR_DB_PATH%"=="" set "STRATEGY_LAB_PFR_DB_PATH=%TRADING_BOT_RESEARCH_ROOT%\state\strategy_lab.sqlite"

set "STOP_FILE=%TRADING_BOT_RESEARCH_ROOT%\state\STOP_FARM_FULL_CYCLE.txt"
if not exist "%TRADING_BOT_RESEARCH_ROOT%\state" mkdir "%TRADING_BOT_RESEARCH_ROOT%\state"

echo ============================================
echo  Strategy Lab - Visible Control Room
echo ============================================
echo  repo        : %CD%
echo  private_root: %TRADING_BOT_RESEARCH_ROOT%
echo  stop file   : %STOP_FILE%
echo  safety      : paper-only; no .env / AUTO_TRADE / orders / private endpoints
echo ============================================
echo.
echo This opens visible windows:
echo   1. Farm Full Cycle Loop
echo   2. Dashboard server at http://127.0.0.1:8765
echo   3. Private graph viewer build/open
echo   4. Periodic farm status monitor
if /I "%STRATEGY_LAB_PAPER_TELEGRAM_SEND%"=="1" echo      Telegram paper delivery runs inside Farm Full Cycle Loop
echo.
echo Stop farm loop: bat\strategy_lab_farm_full_cycle_stop.bat
echo Preflight: python -m scripts.strategy_lab.operational_health --fail-on-blocked
echo Fast status: python -m scripts.strategy_lab.operational_health --private-root "%TRADING_BOT_RESEARCH_ROOT%" --pfr-db-path "%STRATEGY_LAB_PFR_DB_PATH%" --fail-on-blocked
echo Close dashboard/graph/status windows manually when done.
echo.

python -X utf8 -m scripts.strategy_lab.operational_health --private-root "%TRADING_BOT_RESEARCH_ROOT%" --pfr-db-path "%STRATEGY_LAB_PFR_DB_PATH%" --fail-on-blocked
if errorlevel 1 (
  echo.
  echo Preflight blocked. Fix readiness gates with status=blocked before starting visible windows.
  if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
  endlocal
  exit /b 2
)
echo.

start "Strategy Lab - Farm Full Cycle" cmd /k "cd /d ""%CD%"" && bat\strategy_lab_farm_full_cycle_loop.bat"
timeout /t 2 /nobreak >nul

start "Strategy Lab - Dashboard 8765" cmd /k "cd /d ""%CD%"" && bat\strategy_lab_dashboard.bat"
timeout /t 2 /nobreak >nul

start "Strategy Lab - Graph Viewer" cmd /k "cd /d ""%CD%"" && bat\strategy_lab_graph_viewer.bat"
timeout /t 1 /nobreak >nul

start "Strategy Lab - Status Monitor" cmd /k "cd /d ""%CD%"" && bat\strategy_lab_status_monitor.bat"

echo Control room windows started.
echo If a window reports an error, keep it open and inspect the message.
echo.
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b 0
