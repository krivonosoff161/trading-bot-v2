@echo off
setlocal
cd /d "%~dp0\.."
set "PYTHONUTF8=1"
python -X utf8 scripts\strategy_lab\worker_loop.py %*
