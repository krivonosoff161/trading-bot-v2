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
    powershell -NoProfile -Command "Start-Sleep -Seconds %ERROR_SLEEP_SECONDS%"
) else (
    powershell -NoProfile -Command "Start-Sleep -Seconds %SLEEP_SECONDS%"
)
goto loop
