@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

if "%SCANNER_LOOP_LIMIT%"=="" set "SCANNER_LOOP_LIMIT=3"
if "%SCANNER_LOOP_SLEEP_SECONDS%"=="" set "SCANNER_LOOP_SLEEP_SECONDS=300"

echo ============================================
echo  News Scanner Loop - scout scanner_v0
echo ============================================
echo.
echo  Mode:      BUFFER
echo  Limit:     %SCANNER_LOOP_LIMIT% cards per pass
echo  Sleep:     %SCANNER_LOOP_SLEEP_SECONDS% seconds between passes
echo  Stop:      Ctrl+C in this window
echo.
echo  No order engine. No live trading.
echo ============================================
echo.

:loop
echo.
echo [%date% %time%] Starting news scanner pass...
python -X utf8 src\scout\scanner_v0.py --buffer --limit %SCANNER_LOOP_LIMIT%
set "RC=%ERRORLEVEL%"
echo [%date% %time%] Scanner pass finished with code %RC%.
echo Waiting %SCANNER_LOOP_SLEEP_SECONDS% seconds. Press Ctrl+C to stop.
timeout /t %SCANNER_LOOP_SLEEP_SECONDS% /nobreak
goto loop
