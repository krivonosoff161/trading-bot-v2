@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Visible GPU/auto backend probe for the Strategy Lab sweep worker.
rem Paper/research only. No order engine. No live trading. No secrets.
rem Shows backend capability, then runs one bounded momentum_breakout sweep.

echo ============================================================
echo   Strategy Lab - GPU backend probe (visible, paper-only)
echo ============================================================
echo.

echo [1/2] GPU doctor:
python -X utf8 -m scripts.strategy_lab.gpu_doctor
echo.

echo [2/2] Bounded sweep probe (backend=auto, then gpu):
python -X utf8 -m scripts.strategy_lab.gpu_probe --backend auto --parity
echo.
python -X utf8 -m scripts.strategy_lab.gpu_probe --backend gpu --parity
echo.

echo Done. backend=cpu/auto always run; backend=gpu runs only on a real GPU.
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b 0
