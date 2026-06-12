@echo off
setlocal
cd /d "%~dp0\.."
python scripts\strategy_lab\worker_once.py %*
endlocal
