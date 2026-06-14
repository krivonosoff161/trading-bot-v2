@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Operator market-data prepare for 15m/1h/4h/1d.
rem Default: dry-run, null provider, no network, no files except private status report.
rem To fetch public OKX candles explicitly:
rem   set STRATEGY_LAB_MARKET_PREP_TF=1h
rem   set STRATEGY_LAB_MARKET_PREP_PROVIDER=okx-public
rem   set STRATEGY_LAB_MARKET_PREP_APPLY=1
rem   bat\strategy_lab_prepare_market_data.bat

if "%STRATEGY_LAB_MARKET_PREP_TF%"=="" set "STRATEGY_LAB_MARKET_PREP_TF=1d"
if "%STRATEGY_LAB_MARKET_PREP_UNIVERSE%"=="" set "STRATEGY_LAB_MARKET_PREP_UNIVERSE=core_market"
if "%STRATEGY_LAB_MARKET_PREP_PROVIDER%"=="" set "STRATEGY_LAB_MARKET_PREP_PROVIDER=null"
if "%STRATEGY_LAB_MARKET_PREP_MAX_SYMBOLS%"=="" set "STRATEGY_LAB_MARKET_PREP_MAX_SYMBOLS=0"

set "MODE=--dry-run"
if "%STRATEGY_LAB_MARKET_PREP_APPLY%"=="1" set "MODE=--apply"

echo ============================================
echo  Strategy Lab - Market Data Prepare
echo ============================================
echo.
echo  Timeframe:  %STRATEGY_LAB_MARKET_PREP_TF%
echo  Universe:   %STRATEGY_LAB_MARKET_PREP_UNIVERSE%
echo  Provider:   %STRATEGY_LAB_MARKET_PREP_PROVIDER%
echo  Mode:       %MODE%
echo  Safety:     market data only; no orders; no live trading
echo.
echo ============================================
echo.

python -X utf8 -m scripts.strategy_lab.prepare_market_data --timeframe %STRATEGY_LAB_MARKET_PREP_TF% --universe %STRATEGY_LAB_MARKET_PREP_UNIVERSE% --provider %STRATEGY_LAB_MARKET_PREP_PROVIDER% --max-symbols %STRATEGY_LAB_MARKET_PREP_MAX_SYMBOLS% %MODE%
set "RC=%ERRORLEVEL%"

echo.
echo Market-data prepare finished. Check: python -m scripts.strategy_lab.status
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
