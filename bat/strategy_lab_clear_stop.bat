@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Clear the Strategy Lab stop intent.

echo Strategy Lab - Clear Stop Intent
echo.
python -X utf8 -c "from src.research_lab.stop_intent import clear_stop; from src.research_lab.paths import DEFAULT_PRIVATE_ROOT; from pathlib import Path; import os; p = Path(os.getenv('TRADING_BOT_RESEARCH_ROOT', str(DEFAULT_PRIVATE_ROOT))); clear_stop(p); print('Stop intent cleared.')"
set "RC=%ERRORLEVEL%"
echo.
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
