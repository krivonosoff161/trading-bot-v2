@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

echo ============================================
echo  Strategy Lab - Visual Graph Viewer
echo ============================================
echo.
echo  Builds a private standalone HTML graph and opens it in the browser.
echo  No API calls. No network. No public repo output.
echo.

python -X utf8 -m scripts.strategy_lab.build_graph_viewer
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto done

set "VIEWER=%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\graph-viewer\index.html"
echo.
echo Opening:
echo %VIEWER%
start "" "%VIEWER%"

:done
echo.
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
