@echo off
setlocal
cd /d "%~dp0\.."
python scripts\strategy_lab\enqueue_experiment.py --spec configs\strategy_lab\l2_smoke.json %*
endlocal
