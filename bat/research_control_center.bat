@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
python -X utf8 -m scripts.research_control_center %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal & exit /b %RC%
