@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Read-only 1m event-microscope scan. No download, no sweep run, no API.
set "GROUP=%STRATEGY_LAB_MICROSCOPE_UNIVERSE%"
if "%GROUP%"=="" set "GROUP=l2_high_beta"
python -X utf8 -m scripts.strategy_lab.microscope_scan --universe %GROUP%
set "RC=%ERRORLEVEL%"
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
