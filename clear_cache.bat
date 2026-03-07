@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Trading Bot V2 - Clear Cache
cd /d "%~dp0"

echo ================================
echo   TRADING BOT V2 - CLEAR CACHE
echo ================================
echo.

set "count=0"

echo Removing __pycache__ folders...
for /f "delims=" %%d in ('dir /b /s /ad "__pycache__" 2^>nul') do (
    if exist "%%d" (
        rd /s /q "%%d" 2>nul
        if not errorlevel 1 (
            set /a count+=1
            echo [OK] Removed: %%d
        )
    )
)

echo.
echo Removing .pyc files...
set "pyc=0"
for /f "delims=" %%f in ('dir /b /s "*.pyc" 2^>nul') do (
    if exist "%%f" (
        del /Q /F "%%f" 2>nul
        if not errorlevel 1 set /a pyc+=1
    )
)

echo.
echo ================================
echo [OK] __pycache__ removed: !count! folders
echo [OK] .pyc removed: !pyc! files
echo ================================
echo.
pause
