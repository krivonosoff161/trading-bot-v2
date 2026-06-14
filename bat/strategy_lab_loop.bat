@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

REM LEGACY manual loop. This bypasses the queue/resource-policy 24/7 path.
REM Preferred safe path: bat\strategy_lab_research_loop_overnight_no_llm.bat
REM Set STRATEGY_LAB_ALLOW_LEGACY_LOOP=1 only if you intentionally want this.
if not "%STRATEGY_LAB_ALLOW_LEGACY_LOOP%"=="1" (
  echo This legacy loop is disabled by default.
  echo Use: bat\strategy_lab_research_loop_overnight_no_llm.bat
  echo To force legacy mode: set STRATEGY_LAB_ALLOW_LEGACY_LOOP=1
  if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
  exit /b 2
)

REM Default: one direct experiment every 6 hours.
set INTERVAL=21600
set SPEC=configs\strategy_lab\l2_smoke.json

:loop
echo --- strategy lab %date% %time% ---
python scripts\strategy_lab\run_experiment.py --spec %SPEC%
echo --- next run in %INTERVAL%s ---
timeout /t %INTERVAL% /nobreak >nul
goto loop
