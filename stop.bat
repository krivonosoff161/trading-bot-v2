@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Trading Bot V2 - Stop
cd /d "%~dp0"

echo ================================
echo   TRADING BOT V2 - STOP
echo ================================
echo.

set "found=0"
set "stopped=0"

echo Searching for bot processes...

for /f "tokens=2" %%i in ('tasklist /FI "IMAGENAME eq python.exe" /FO CSV 2^>nul ^| findstr /I "python.exe"') do (
    set "pid=%%i"
    set "pid=!pid:"=!"

    if not "!pid!"=="" (
        set "is_bot=0"
        for /f "tokens=*" %%c in ('wmic process where "processid=!pid!" get commandline 2^>nul') do (
            set "line=%%c"
            echo !line! | findstr /I /C:"main.py" >nul
            if !errorlevel! equ 0 set "is_bot=1"
        )

        if !is_bot! equ 1 (
            set "found=1"
            echo [FOUND] Bot process PID !pid!
            taskkill /PID !pid! /F >nul 2>&1
            if errorlevel 1 (
                echo [ERROR] Could not stop PID !pid! (try running as Administrator)
            ) else (
                echo [OK] Stopped PID !pid!
                set /a stopped+=1
            )
        )
    )
)

echo.
if !found! equ 0 (
    echo [INFO] No bot processes found.
) else (
    echo [OK] Stopped !stopped! process(es).
)

echo.
pause
