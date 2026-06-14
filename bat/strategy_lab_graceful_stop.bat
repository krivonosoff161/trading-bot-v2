@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Request a graceful stop. The loop exits after the current iteration.

echo Strategy Lab - Graceful Stop
echo.
python -X utf8 -c "from src.research_lab.stop_intent import request_stop; from src.research_lab.paths import DEFAULT_PRIVATE_ROOT; from pathlib import Path; import os; p = Path(os.getenv('TRADING_BOT_RESEARCH_ROOT', str(DEFAULT_PRIVATE_ROOT))); request_stop(p); print('Stop intent written: state/strategy_lab_stop_requested.json (private root)')"
set "RC=%ERRORLEVEL%"
echo.
echo The running loop will finish its current iteration and then stop.
echo To check status: python -m scripts.strategy_lab.status
echo To clear stop intent: bat\strategy_lab_clear_stop.bat
echo.
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
