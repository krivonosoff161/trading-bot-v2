@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem One bounded, visible, paper-only pass of the full research machine:
rem   scanner watches -> farm bridge -> worker -> hard validation -> feedback -> status
rem No order engine. No live trading. No AUTO_TRADE. No paid LLM.
rem Real artifacts go to the private trading-bot-research workspace.

echo ============================================================
echo   Research Machine Demo (visible, paper-only)
echo ============================================================
echo.
echo   Add --dry-run to preview without writing artifacts.
echo   Stop: Ctrl+C in this window.
echo.

python -X utf8 -m scripts.strategy_lab.run_research_machine_demo %*
set "RC=%ERRORLEVEL%"

echo.
echo Demo finished with code %RC%.
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
