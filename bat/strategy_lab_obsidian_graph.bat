@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

echo ============================================
echo  Strategy Lab - Obsidian Graph
echo ============================================
echo.
echo  Builds private candidate notes plus the lightweight browser graph.
echo  Opens graph-viewer\index.html in the default browser.
echo.

python -X utf8 -m scripts.strategy_lab.build_obsidian_graph
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto done

python -X utf8 -m scripts.strategy_lab.build_graph_viewer
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto done

set "VIEWER=%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\graph-viewer\index.html"
echo.
echo Opening browser graph:
echo %VIEWER%
start "" "%VIEWER%"

:done
echo.
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
