@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Preview next-experiment proposals. Dry-run only: writes nothing, queues nothing.
echo Generating proposals in DRY-RUN (no files changed, nothing queued)...
python -X utf8 -m scripts.strategy_lab.generate_next_proposals --limit 10 --dry-run
echo.
echo To store proposals, run explicitly:
echo   python -m scripts.strategy_lab.generate_next_proposals --limit 10 --apply
set "RC=%ERRORLEVEL%"
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
