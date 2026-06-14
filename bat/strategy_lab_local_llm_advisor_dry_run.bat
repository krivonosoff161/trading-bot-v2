@echo off
REM Local LLM advisor — dry-run (safe, no LLM calls)
cd /d "%~dp0\.."
python scripts\strategy_lab\local_llm_advisor.py --dry-run %*
pause
