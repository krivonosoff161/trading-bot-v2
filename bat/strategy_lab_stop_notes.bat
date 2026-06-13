@echo off
setlocal
chcp 65001 >nul 2>&1

echo Strategy Lab - how to stop safely
echo --------------------------------------------------
echo There is no daemon. The lab runs in two windows opened by
echo strategy_lab_start.bat: "Strategy Lab Dashboard" and "Strategy Lab Worker".
echo.
echo Stopping is safe at any time: the worker runs ONE job at a time, and a
echo half-claimed job is requeued automatically on the next start.
echo.
echo This script closes only those two lab windows (nothing else):
echo   taskkill /FI "WINDOWTITLE eq Strategy Lab Worker*" /T /F
echo   taskkill /FI "WINDOWTITLE eq Strategy Lab Dashboard*" /T /F
echo.

choice /C YN /M "Close the Strategy Lab worker and dashboard windows now"
if errorlevel 2 goto skip

taskkill /FI "WINDOWTITLE eq Strategy Lab Worker*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Strategy Lab Dashboard*" /T /F >nul 2>&1
echo Done. Lab windows closed (if they were open).
goto end

:skip
echo No action taken. You can also just close the two windows manually.

:end
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
