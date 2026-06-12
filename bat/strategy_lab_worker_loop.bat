@echo off
setlocal
cd /d "%~dp0\.."
:loop
python scripts\strategy_lab\worker_once.py %*
timeout /t 60 /nobreak >nul
goto loop
