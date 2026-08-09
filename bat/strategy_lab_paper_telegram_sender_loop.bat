@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Visible optional paper Telegram delivery loop.
rem Sends validated paper_telegram_preview cards only to active bot subscribers.
rem Dedup is enforced by src.research_lab.paper_telegram_sender.
rem No orders, no AUTO_TRADE, no private OKX endpoints.

if "%TRADING_BOT_RESEARCH_ROOT%"=="" (
  set "TRADING_BOT_RESEARCH_ROOT=%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
)
if "%STRATEGY_LAB_PAPER_TELEGRAM_SEND_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_PAPER_TELEGRAM_SEND_SLEEP_SECONDS=300"
if "%STRATEGY_LAB_PAPER_TELEGRAM_SEND_LIMIT%"=="" set "STRATEGY_LAB_PAPER_TELEGRAM_SEND_LIMIT=20"

set "STOP_FILE=%TRADING_BOT_RESEARCH_ROOT%\state\STOP_PAPER_TELEGRAM_SENDER.txt"
set "LOG_DIR=%TRADING_BOT_RESEARCH_ROOT%\logs"
set "LOG_FILE=%LOG_DIR%\paper_telegram_sender_loop.log"

if not exist "%TRADING_BOT_RESEARCH_ROOT%\state" mkdir "%TRADING_BOT_RESEARCH_ROOT%\state"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if exist "%STOP_FILE%" del "%STOP_FILE%"

echo ============================================
echo  Strategy Lab - Paper Telegram Sender Loop
echo ============================================
echo  repo        : %CD%
echo  private_root: %TRADING_BOT_RESEARCH_ROOT%
echo  stop file   : %STOP_FILE%
echo  log         : %LOG_FILE%
echo  sleep       : %STRATEGY_LAB_PAPER_TELEGRAM_SEND_SLEEP_SECONDS%s
echo  limit       : %STRATEGY_LAB_PAPER_TELEGRAM_SEND_LIMIT%
echo  target      : active subscription users via Telegram bot
echo  safety      : paper-only; no orders / AUTO_TRADE / private endpoints
echo ============================================
echo.
echo Tip: stop with Ctrl+C or create the stop file above.
echo.

:loop
if exist "%STOP_FILE%" goto done
python -X utf8 -m scripts.strategy_lab.paper_telegram_sender --private-root "%TRADING_BOT_RESEARCH_ROOT%" --limit %STRATEGY_LAB_PAPER_TELEGRAM_SEND_LIMIT% --send
if exist "%STOP_FILE%" goto done
timeout /t %STRATEGY_LAB_PAPER_TELEGRAM_SEND_SLEEP_SECONDS% /nobreak
goto loop

:done
echo Paper Telegram sender loop stopped.
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b 0
