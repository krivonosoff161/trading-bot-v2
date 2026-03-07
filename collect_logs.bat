@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Trading Bot V2 - Collect Logs
cd /d "%~dp0"

echo ================================
echo   TRADING BOT V2 - COLLECT LOGS
echo ================================
echo.

if not exist "logs" (
    echo [ERROR] logs\ folder not found. Run bot first.
    pause
    exit /b 1
)

REM Build folder name with date and time
set "dt=%date:~6,4%-%date:~3,2%-%date:~0,2%_%time:~0,2%-%time:~3,2%"
set "dt=!dt: =0!"
set "dest=logs_archive\logs_%dt%"

if not exist "logs_archive" mkdir "logs_archive"
mkdir "!dest!" 2>nul

xcopy /E /Q "logs\*" "!dest!\" >nul
if errorlevel 1 (
    echo [ERROR] Could not copy logs.
    pause
    exit /b 1
)

echo [OK] Logs saved to: !dest!\
echo.
dir /b "!dest!\*.log" 2>nul

REM Delete original logs after successful copy
del /Q /F "logs\*.log" 2>nul
del /Q /F "logs\*.jsonl" 2>nul
echo.
echo [OK] Original logs cleared.

echo.
pause
