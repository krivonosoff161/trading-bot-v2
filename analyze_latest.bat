@echo off
chcp 65001 > nul
echo ================================================
echo   CHART ANALYZER — последний скрин из обучение/
echo ================================================
echo.

:: Find latest image in обучение/ folder
set STUDY_DIR=%~dp0обучение
set LATEST_IMG=

for /f "delims=" %%F in ('dir /b /o-d /a-d "%STUDY_DIR%\*.jpg" "%STUDY_DIR%\*.jpeg" "%STUDY_DIR%\*.png" 2^>nul') do (
    if not defined LATEST_IMG set LATEST_IMG=%%F
)

if not defined LATEST_IMG (
    echo Скринов не найдено в папке обучение/
    echo Положи .jpg или .png файл в папку и запусти снова.
    pause
    exit /b 1
)

echo Найден скрин: %LATEST_IMG%
echo.

:: Ask for required inputs
set /p SYMBOL=Символ (например BTC-USDT):
set /p CAPTURED_AT=Время скрина UTC (например 2026-03-09T11:42:35Z):

echo.
echo Запускаю анализ...
echo.

python scripts\analyze_chart.py ^
    --symbol "%SYMBOL%" ^
    --captured-at "%CAPTURED_AT%" ^
    --image "%STUDY_DIR%\%LATEST_IMG%"

echo.
echo Готово. Результаты в scripts\analysis_output\
pause
