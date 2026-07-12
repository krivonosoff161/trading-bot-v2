@echo off
setlocal
cd /d "%~dp0\.."
echo Public news channel loop. Paper/public only. No live trading.
echo Collects every 5 minutes; publishes one queued item every 15 minutes.
echo Close this window to stop.
if "%TRADING_BOT_RESEARCH_ROOT%"=="" set "TRADING_BOT_RESEARCH_ROOT=%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
set "PUBLIC_NEWS_STOP_FILE=%TRADING_BOT_RESEARCH_ROOT%\state\STOP_PUBLIC_NEWS.txt"
if not exist "%TRADING_BOT_RESEARCH_ROOT%\state" mkdir "%TRADING_BOT_RESEARCH_ROOT%\state"
if exist "%PUBLIC_NEWS_STOP_FILE%" del "%PUBLIC_NEWS_STOP_FILE%"
set /a TICK=0
:loop
if exist "%PUBLIC_NEWS_STOP_FILE%" goto stopped
python -X utf8 scripts\public_channel_publisher.py --mode collect
if %TICK% GEQ 2 (
  python -X utf8 scripts\public_channel_publisher.py --mode publish --limit 1 --use-llm --send
  set /a TICK=0
) else (
  set /a TICK+=1
)
for /L %%S in (1,1,300) do (
  if exist "%PUBLIC_NEWS_STOP_FILE%" goto stopped
  timeout /t 1 /nobreak >nul
)
goto loop

:stopped
echo [%date% %time%] Public news stop file received. Exiting normally.
exit /b 0
