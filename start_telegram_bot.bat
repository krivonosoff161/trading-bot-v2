@echo off
chcp 65001 > nul
if not exist "logs" mkdir logs
echo [%date% %time%] Bot started >> logs\telegram_bot.log
python scripts\telegram_bot.py >> logs\telegram_bot.log 2>&1
echo [%date% %time%] Bot stopped >> logs\telegram_bot.log
echo.
pause
