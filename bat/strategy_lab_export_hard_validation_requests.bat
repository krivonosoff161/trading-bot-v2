@echo off
REM Export hard validation requests from candidate registry (dry-run by default)
cd /d "%~dp0\.."
python scripts\strategy_lab\export_hard_validation_requests.py %*
pause
