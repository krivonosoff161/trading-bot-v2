@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

python scripts\strategy_lab\enqueue_pack.py --dir configs\strategy_lab\starter --priority 50
set "RC=%ERRORLEVEL%"
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
