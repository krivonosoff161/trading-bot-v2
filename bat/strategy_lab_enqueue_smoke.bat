@echo off
setlocal
cd /d "%~dp0\.."
python scripts\strategy_lab\enqueue_experiment.py --spec configs\strategy_lab\l2_smoke.json %*
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
