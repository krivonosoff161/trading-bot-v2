@echo off
setlocal
cd /d "%~dp0\.."
set "SLEEP_SECONDS=60"
set "ERROR_SLEEP_SECONDS=300"
:loop
python scripts\strategy_lab\worker_once.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo [%date% %time%] strategy_lab worker failed with exit code %RC%
    timeout /t %ERROR_SLEEP_SECONDS% /nobreak >nul
) else (
    timeout /t %SLEEP_SECONDS% /nobreak >nul
)
goto loop
