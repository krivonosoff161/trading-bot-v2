@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set PYTHONUTF8=1

REM 24/7 private strategy-lab loop. Results are written to trading-bot-research.
REM Default: one experiment every 6 hours.
set INTERVAL=21600
set SPEC=configs\strategy_lab\l2_smoke.json

:loop
echo --- strategy lab %date% %time% ---
python scripts\strategy_lab\run_experiment.py --spec %SPEC%
echo --- next run in %INTERVAL%s ---
timeout /t %INTERVAL% /nobreak >nul
goto loop

