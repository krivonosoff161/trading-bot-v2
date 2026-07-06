@echo off
setlocal
cd /d "%~dp0\.."
echo Public news channel loop. Paper/public only. No live trading.
echo Collects every 5 minutes; publishes one queued item every 15 minutes.
echo Close this window to stop.
set /a TICK=0
:loop
python -X utf8 scripts\public_channel_publisher.py --mode collect
if %TICK% GEQ 2 (
  python -X utf8 scripts\public_channel_publisher.py --mode publish --limit 1 --use-llm --send
  set /a TICK=0
) else (
  set /a TICK+=1
)
timeout /t 300 /nobreak
goto loop
