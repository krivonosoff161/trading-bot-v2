@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Explicit opt-in wrapper for sending already validated paper Telegram cards.
rem Still paper-only: it does not enable AUTO_TRADE, order paths, old main.py, or
rem private exchange endpoints.

set "STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM=1"
call bat\paper_product_control_room.bat
set "RC=%ERRORLEVEL%"
endlocal
exit /b %RC%
