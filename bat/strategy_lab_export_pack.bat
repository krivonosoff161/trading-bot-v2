@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Export-only LLM review pack to the private root. No API call, no spend.
python -X utf8 -m scripts.strategy_lab.export_llm_review_pack --limit 10
set "RC=%ERRORLEVEL%"
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
