@echo off
cd /d %~dp0
echo ================================
echo  OKX Pump Engine - Paper Trading
echo ================================
echo [1/2] Refreshing coin screener...
python scripts/ws/coin_screener.py
echo [2/2] Starting pump engine (dynamic universe)...
python scripts/ws/ws_pump_engine.py
pause
