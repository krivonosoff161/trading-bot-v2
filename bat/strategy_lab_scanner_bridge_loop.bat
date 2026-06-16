@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Visible scanner -> Strategy Lab queue bridge.
rem Reads open WATCH/GO rows from logs/scout/watch_queue.jsonl and queues bounded
rem paper-only sweeps for the farm. No order engine. No live trading. No secrets.

if "%STRATEGY_LAB_SCANNER_BRIDGE_SLEEP_SECONDS%"=="" set "STRATEGY_LAB_SCANNER_BRIDGE_SLEEP_SECONDS=300"
if "%STRATEGY_LAB_SCANNER_BRIDGE_TIMEFRAME%"=="" set "STRATEGY_LAB_SCANNER_BRIDGE_TIMEFRAME=1d"
if "%STRATEGY_LAB_SCANNER_BRIDGE_MAX_SYMBOLS%"=="" set "STRATEGY_LAB_SCANNER_BRIDGE_MAX_SYMBOLS=2"
if "%STRATEGY_LAB_SCANNER_BRIDGE_MAX_VARIANTS%"=="" set "STRATEGY_LAB_SCANNER_BRIDGE_MAX_VARIANTS=8"
if "%STRATEGY_LAB_SCANNER_BRIDGE_LIMIT%"=="" set "STRATEGY_LAB_SCANNER_BRIDGE_LIMIT=25"
if "%STRATEGY_LAB_SCANNER_BRIDGE_PRIORITY%"=="" set "STRATEGY_LAB_SCANNER_BRIDGE_PRIORITY=85"
if "%STRATEGY_LAB_SCANNER_BRIDGE_BACKEND%"=="" set "STRATEGY_LAB_SCANNER_BRIDGE_BACKEND=auto"

echo ============================================================
echo   Strategy Lab - Scanner Bridge Loop (visible, paper-only)
echo ============================================================
echo.
echo   Source:       logs/scout/watch_queue.jsonl open WATCH/GO rows
echo   Timeframe:    %STRATEGY_LAB_SCANNER_BRIDGE_TIMEFRAME%
echo   Max symbols:  %STRATEGY_LAB_SCANNER_BRIDGE_MAX_SYMBOLS% per pass
echo   Max variants: %STRATEGY_LAB_SCANNER_BRIDGE_MAX_VARIANTS% per sweep
echo   Backend:      %STRATEGY_LAB_SCANNER_BRIDGE_BACKEND% (auto records GPU/CPU fallback)
echo   Sleep:        %STRATEGY_LAB_SCANNER_BRIDGE_SLEEP_SECONDS% seconds
echo   Live trading: OFF
echo   Order engine: OFF
echo   Stop:         Ctrl+C in this window, or bat\strategy_lab_graceful_stop.bat
echo ============================================================
echo.

:bridge_loop
python -X utf8 -c "from src.research_lab.stop_intent import is_stop_requested; from src.research_lab.paths import DEFAULT_PRIVATE_ROOT; from pathlib import Path; import os, sys; p = Path(os.getenv('TRADING_BOT_RESEARCH_ROOT', str(DEFAULT_PRIVATE_ROOT))); sys.exit(3 if is_stop_requested(p) else 0)"
if "%ERRORLEVEL%"=="3" goto stopped

echo [%date% %time%] Scanner bridge pass...
python -X utf8 -m scripts.strategy_lab.generate_event_sweeps --from-scanner --timeframe %STRATEGY_LAB_SCANNER_BRIDGE_TIMEFRAME% --max-symbols %STRATEGY_LAB_SCANNER_BRIDGE_MAX_SYMBOLS% --max-variants %STRATEGY_LAB_SCANNER_BRIDGE_MAX_VARIANTS% --limit %STRATEGY_LAB_SCANNER_BRIDGE_LIMIT% --backend %STRATEGY_LAB_SCANNER_BRIDGE_BACKEND% --priority %STRATEGY_LAB_SCANNER_BRIDGE_PRIORITY% --apply
set "RC=%ERRORLEVEL%"
echo [%date% %time%] Scanner bridge finished with code %RC%.

python -X utf8 -c "from src.research_lab.stop_intent import is_stop_requested; from src.research_lab.paths import DEFAULT_PRIVATE_ROOT; from pathlib import Path; import os, sys; p = Path(os.getenv('TRADING_BOT_RESEARCH_ROOT', str(DEFAULT_PRIVATE_ROOT))); sys.exit(3 if is_stop_requested(p) else 0)"
if "%ERRORLEVEL%"=="3" goto stopped

echo Waiting %STRATEGY_LAB_SCANNER_BRIDGE_SLEEP_SECONDS% seconds. Press Ctrl+C to stop.
timeout /t %STRATEGY_LAB_SCANNER_BRIDGE_SLEEP_SECONDS% /nobreak
goto bridge_loop

:stopped
echo.
echo Stop intent detected. Scanner bridge stopped.
echo Status command: python -m scripts.strategy_lab.status
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b 0
