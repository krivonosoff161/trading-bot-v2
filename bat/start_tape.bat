@echo off
chcp 65001 > nul
cd /d "%~dp0.."
if not exist "scripts\tape" mkdir scripts\tape
set PYTHONUTF8=1
echo Tape recorder started. Data: scripts\tape\
echo Close this window to stop recording.
echo.
python -u scripts\analysis\tape_recorder.py
pause
