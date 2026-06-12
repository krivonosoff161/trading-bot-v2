@echo off
setlocal
cd /d "%~dp0\.."
python scripts\strategy_lab\sync_state_db.py %*
endlocal
