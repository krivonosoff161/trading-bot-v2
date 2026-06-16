@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Roblox-safe capture mode.
rem Starts ONLY:
rem   1) news scanner loop
rem   2) scanner -> farm queue bridge
rem It does NOT start the farm worker, dashboard, graph builder, Ollama model, or
rem any live/order component. The queue can be drained later when the PC is free.

echo ============================================================
echo   Roblox-safe News Capture (no farm worker)
echo ============================================================
echo.
echo   Starts: news scanner + scanner bridge
echo   Does not start: farm worker, dashboard, graph viewer, Ollama model
echo   Live trading: OFF
echo   Order engine: OFF
echo.
echo   Close the two opened windows to stop capture.
echo   Later drain queue: bat\strategy_lab_research_loop_overnight_no_llm.bat
echo ============================================================
echo.

python -X utf8 -c "from src.research_lab.stop_intent import clear_stop; from src.research_lab.paths import DEFAULT_PRIVATE_ROOT; from pathlib import Path; import os; p = Path(os.getenv('TRADING_BOT_RESEARCH_ROOT', str(DEFAULT_PRIVATE_ROOT))); clear_stop(p); print('Stop intent cleared.')"
if errorlevel 1 goto fail

start "News Scanner - Roblox Safe" cmd /k "cd /d %CD% && set SCANNER_LOOP_LIMIT=1&& set SCANNER_LOOP_SLEEP_SECONDS=900&& set SCANNER_RUN_OUTCOMES=true&& set SCANNER_OUTCOME_LIMIT=5&& bat\news_scanner_loop.bat"

start "Scanner Bridge - Roblox Safe" cmd /k "cd /d %CD% && set STRATEGY_LAB_SCANNER_BRIDGE_BACKEND=cpu&& set STRATEGY_LAB_SCANNER_BRIDGE_SLEEP_SECONDS=900&& set STRATEGY_LAB_SCANNER_BRIDGE_MAX_SYMBOLS=1&& set STRATEGY_LAB_SCANNER_BRIDGE_MAX_VARIANTS=2&& set STRATEGY_LAB_SCANNER_BRIDGE_LIMIT=10&& bat\strategy_lab_scanner_bridge_loop.bat"

echo Started two capture windows. No calculation worker was started.
echo You can close this window.
endlocal
exit /b 0

:fail
echo Failed to clear stop intent; capture windows were not started.
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b 1
