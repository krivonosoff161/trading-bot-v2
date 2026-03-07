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

REM Build archive name with date and time
set "dt=%date:~6,4%-%date:~3,2%-%date:~0,2%_%time:~0,2%-%time:~3,2%"
set "dt=!dt: =0!"
set "archive=logs_archive\logs_%dt%.zip"

REM Create archive dir
if not exist "logs_archive" mkdir "logs_archive"

REM Check for PowerShell (Windows 10+)
powershell -Command "Compress-Archive -Path 'logs\*' -DestinationPath '%archive%' -Force" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not create zip. PowerShell required.
    echo [INFO] Copying logs instead...
    set "copy_dir=logs_archive\logs_%dt%"
    mkdir "!copy_dir!" 2>nul
    xcopy /E /Q "logs\*" "!copy_dir!\" >nul
    echo [OK] Logs copied to: !copy_dir!\
) else (
    echo [OK] Logs archived to: %archive%
)

echo.
echo Logs in archive:
dir /b logs\*.log 2>nul
echo.
pause
