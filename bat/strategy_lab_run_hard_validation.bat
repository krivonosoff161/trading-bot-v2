@echo off
REM Run hard validation on exported request files (dry-run by default)
cd /d "%~dp0\.."
python scripts\strategy_lab\run_hard_validation.py %*
pause
