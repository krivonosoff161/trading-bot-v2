@echo off
chcp 65001 > nul
cd /d "%~dp0.."
if not exist "logs" mkdir logs
if not exist "scripts\tape" mkdir scripts\tape
echo [%date% %time%] Bot started >> logs\telegram_bot.log
set PYTHONUTF8=1
python -u scripts\telegram_bot.py
echo [%date% %time%] Bot stopped >> logs\telegram_bot.log
echo.
pause
