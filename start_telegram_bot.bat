@echo off
chcp 65001 > nul
if not exist "logs" mkdir logs
if not exist "scripts\tape" mkdir scripts\tape

echo [%date% %time%] Bot started >> logs\telegram_bot.log
set PYTHONUTF8=1
python -u scripts\telegram_bot.py >> logs\telegram_bot.log 2>&1

echo [%date% %time%] Bot stopped >> logs\telegram_bot.log
echo.
pause
