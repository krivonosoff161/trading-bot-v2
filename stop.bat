@echo off
chcp 65001 >nul 2>&1
title Trading Bot V2 - Stop
cd /d "%~dp0"

echo ================================
echo   TRADING BOT V2 - STOP
echo ================================
echo.

call :kill_window "Telegram Bot"
call :kill_window "Main Screener"
call :kill_window "Live Screener"
call :kill_window "Pump Orchestrator"
call :kill_window "Smart Pump"
call :kill_window "Impulse Pump"
call :kill_window "BB Fade"
call :kill_window "Tape Recorder"

echo.
echo [OK] Done.
pause
goto :eof

:kill_window
taskkill /FI "WINDOWTITLE eq %~1" /T /F >nul 2>&1
if errorlevel 1 (
    echo [SKIP] %~1 — not found
) else (
    echo [OK]   %~1 — stopped
)
goto :eof
