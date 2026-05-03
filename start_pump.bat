@echo off
cd /d %~dp0
echo ================================
echo  OKX Pump Engine - Paper Trading
echo ================================
python scripts/ws/ws_pump_engine.py %*
pause
