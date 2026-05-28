@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo Starting Trading Bot V2...
echo.

start "Telegram Bot" cmd /k "cd /d %~dp0 && python -u scripts\telegram_bot.py"
timeout /t 3 /nobreak > nul

start "Main Screener" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_main_screener.py"
start "Live Screener" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_screener_live.py"
timeout /t 5 /nobreak > nul
REM Smart Pump (reversal) DISABLED 20.05.2026 - reversal edge fee-blocked (see docs/strategy_pump_reversal_postmortem.md).
REM Impulse Pump + Main Impulse (rivok) DISABLED 24.05.2026 - forward-paper NO-GO
REM   (impulse -7.77%/WR 0%, pump -19.67%/WR 14%; replay-vs-live gap, BSB overfit, capture~0).
REM   See docs/strategy_impulse_postmortem.md. Logs kept for geometry research. Re-enable: config *.enabled=true + uncomment below.
REM start "Impulse Pump" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_impulse_pump.py"
REM start "Main Impulse" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_main_impulse.py"
REM BB Fade DISABLED 28.05.2026 — фокус только на Main для 10-дневного цикла после fix лейблера.
REM   Re-enable: uncomment line below.
REM start "BB Fade" cmd /k "cd /d %~dp0 && python -u scripts\ws\ws_bb_fade.py"
start "Tape Recorder" cmd /k "cd /d %~dp0 && python -u scripts\analysis\tape_recorder.py"

echo [OK] Telegram Bot started
echo [OK] Main Screener started (shadow mode)
echo [OK] Live Screener started
echo [--] Impulse Pump / Main Impulse DISABLED 24.05 (NO-GO, see docs/strategy_impulse_postmortem.md)
echo [--] BB Fade DISABLED 28.05 (фокус на Main для 10-дневного цикла)
echo [OK] Tape Recorder started
echo.
pause > nul
