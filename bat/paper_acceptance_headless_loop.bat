@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem 24h+ paper/research acceptance. No dashboard, graph viewer, Telegram send,
rem AUTO_TRADE, old main.py, private exchange endpoints, or live orders.
set "STRATEGY_LAB_PAPER_PRODUCT_SEND_TELEGRAM=0"
set "STRATEGY_LAB_PAPER_TELEGRAM_SEND=0"
set "STRATEGY_LAB_RUN_CALCULATOR_ADVISOR=1"
set "STRATEGY_LAB_CALCULATOR_PROVIDER=ollama"
set "STRATEGY_LAB_CALCULATOR_MODEL=calculator-swarm"
set "STRATEGY_LAB_RUN_AGENT_ROLE_REVIEWS=1"

python -X utf8 -m scripts.strategy_lab.paper_acceptance start --hours 24
if errorlevel 1 exit /b %ERRORLEVEL%

call bat\paper_product_headless_loop.bat
set "RC=%ERRORLEVEL%"
endlocal
exit /b %RC%
