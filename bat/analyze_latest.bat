@echo off
chcp 65001 > nul
cd /d "%~dp0.."
python scripts\run_latest_analysis.py
echo.
pause
