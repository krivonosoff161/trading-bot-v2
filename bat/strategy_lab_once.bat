@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

python scripts\strategy_lab\run_experiment.py --spec configs\strategy_lab\l2_smoke.json
