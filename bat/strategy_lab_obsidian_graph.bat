@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0\.."
set "PYTHONUTF8=1"

echo ============================================
echo  Strategy Lab - Obsidian Graph
echo ============================================
echo.
echo  Builds private candidate notes and opens the vault folder.
echo  In Obsidian: Open folder as vault, then Graph View.
echo.

python -X utf8 -m scripts.strategy_lab.build_obsidian_graph
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto done

set "VAULT=%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\obsidian"
echo.
echo Opening vault folder:
echo %VAULT%
start "" explorer "%VAULT%"

rem Best-effort: if Obsidian protocol is registered, open the folder as a vault.
start "" "obsidian://open?path=%VAULT%"

:done
echo.
if not "%STRATEGY_LAB_NO_PAUSE%"=="1" pause
endlocal
exit /b %RC%
