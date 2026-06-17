@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."

rem Combined scanner experiment:
rem - public OKX endpoints only
rem - no private API keys
rem - no order engine / no live trading
rem - existing RSS/Telegram/API sources stay enabled

set "SCANNER_OKX_DAY_TEST=true"

if "%SCANNER_LOOP_LIMIT%"=="" set "SCANNER_LOOP_LIMIT=5"
if "%SCANNER_LOOP_SLEEP_SECONDS%"=="" set "SCANNER_LOOP_SLEEP_SECONDS=300"
if "%SCANNER_RUN_OUTCOMES%"=="" set "SCANNER_RUN_OUTCOMES=true"
if "%SCANNER_OUTCOME_LIMIT%"=="" set "SCANNER_OUTCOME_LIMIT=25"

echo ============================================
echo  Scanner Day Test - existing feeds + OKX
echo ============================================
echo  Adds:      okx_announcements + okx_market_tape
echo  Limit:     %SCANNER_LOOP_LIMIT% cards per pass
echo  Outcomes:  %SCANNER_RUN_OUTCOMES% ^(limit %SCANNER_OUTCOME_LIMIT% mature cards per pass^)
echo  Sleep:     %SCANNER_LOOP_SLEEP_SECONDS% seconds between passes
echo  Stop:      Ctrl+C in this window
echo.
echo  No order engine. No live trading. No private OKX endpoints.
echo ============================================
echo.

call bat\news_scanner_loop.bat
