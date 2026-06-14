@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0.."
set "PYTHONUTF8=1"

echo ============================================
echo  News Scanner Status
echo ============================================
echo.

python scripts\scanner_status.py %*

echo.
pause
