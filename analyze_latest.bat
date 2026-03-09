@echo off
chcp 65001 > nul
echo ================================================
echo   CHART ANALYZER — последний скрин из обучение проба/
echo ================================================
echo.

:: Input folder — screenshots go here
set "STUDY_DIR=%~dp0обучение проба"

if not exist "%STUDY_DIR%" (
    echo Папка не найдена: %STUDY_DIR%
    echo Создай папку "обучение проба" и положи туда скрин.
    pause
    exit /b 1
)

:: Find latest image that is NOT an annotated output (_annotated in name)
set LATEST_IMG=

for /f "delims=" %%F in ('dir /b /o-d /a-d "%STUDY_DIR%\*.jpg" "%STUDY_DIR%\*.jpeg" "%STUDY_DIR%\*.png" 2^>nul') do (
    if not defined LATEST_IMG (
        echo %%F | findstr /i "_annotated" > nul
        if errorlevel 1 set LATEST_IMG=%%F
    )
)

if not defined LATEST_IMG (
    echo Скринов не найдено в папке "%STUDY_DIR%"
    echo Положи .jpg или .png файл (не _annotated) и запусти снова.
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
