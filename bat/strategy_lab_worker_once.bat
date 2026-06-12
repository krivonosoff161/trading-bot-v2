@echo off
setlocal
cd /d "%~dp0\.."
python scripts\strategy_lab\worker_once.py %*
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
