@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Explicit opt-in headless wrapper for sending reviewed paper Telegram cards.
rem Still paper-only: it does not enable AUTO_TRADE, order paths, old main.py,
rem private exchange endpoints, dashboard, or graph-viewer windows.

set "STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM=1"
set "STRATEGY_LAB_PAPER_TELEGRAM_FETCH_CHART_CANDLES=1"
call bat\paper_product_headless_loop.bat
set "RC=%ERRORLEVEL%"
endlocal
exit /b %RC%
