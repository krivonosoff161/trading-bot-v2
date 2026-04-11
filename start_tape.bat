@echo off
chcp 65001 > nul
if not exist "scripts\tape" mkdir scripts\tape
set PYTHONUTF8=1
echo Tape recorder запущен. Данные: scripts\tape\
echo Закрой это окно чтобы остановить запись.
echo.
python -u scripts\tape_recorder.py
pause
