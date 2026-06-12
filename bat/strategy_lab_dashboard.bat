@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

python scripts\strategy_lab\serve_dashboard.py --host 127.0.0.1 --port 8765
