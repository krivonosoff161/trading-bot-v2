@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Preview which VALIDATED proposals would be queued. Dry-run only: queues nothing.
echo Checking validated proposals in DRY-RUN (nothing queued)...
python -X utf8 -m scripts.strategy_lab.queue_validated_proposals --dry-run
echo.
echo To actually queue them, run explicitly:
echo   python -m scripts.strategy_lab.queue_validated_proposals --apply
set "RC=%ERRORLEVEL%"
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
