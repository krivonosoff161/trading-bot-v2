@echo off
cd /d %~dp0
echo ================================
echo  OKX WS Scanner
echo ================================
python scripts/ws/ws_scanner.py %*
pause
