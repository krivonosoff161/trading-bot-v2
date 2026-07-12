@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
python -X utf8 scripts\research_control_center.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
endlocal
exit /b %RC%
