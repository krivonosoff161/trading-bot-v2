@echo off
REM Full hard-validation pipeline (dry-run by default)
cd /d "%~dp0\.."
python scripts\strategy_lab\validate_candidates_pipeline.py %*
pause
