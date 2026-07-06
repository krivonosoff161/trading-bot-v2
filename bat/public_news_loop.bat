@echo off
setlocal
cd /d "%~dp0\.."
echo Public news channel loop. Paper/public only. No live trading.
echo Close this window to stop.
:loop
python -X utf8 scripts\public_channel_publisher.py --mode news --limit 2 --use-llm --send
timeout /t 900 /nobreak
goto loop
