@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

rem Demand-driven 1m data prep. DRY-RUN only here: shows what 1m windows are needed
rem and which are missing. Writes nothing. Default provider is null (no network).
set "GROUP_ARG="
if not "%STRATEGY_LAB_PREPARE_SYMBOL%"=="" set "GROUP_ARG=--symbol %STRATEGY_LAB_PREPARE_SYMBOL% --start %STRATEGY_LAB_PREPARE_START% --end %STRATEGY_LAB_PREPARE_END%"

python -X utf8 -m scripts.strategy_lab.prepare_1m_data --dry-run %GROUP_ARG%
echo.
echo This was a DRY-RUN (nothing downloaded, nothing written).
echo To actually fetch (only with a configured provider), run explicitly:
echo   python -m scripts.strategy_lab.prepare_1m_data --apply
set "RC=%ERRORLEVEL%"
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
