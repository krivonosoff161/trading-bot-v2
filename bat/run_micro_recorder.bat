@echo off
title THEME40 MICRO RECORDER
cd /d C:\Users\krivo\trading-bot-v2
echo ============================================================
echo  THEME 40 - MICROSTRUCTURE ORDERBOOK RECORDER (research-only)
echo  keyless public OKX books+trades - no keys, no orders
echo  heartbeat below; log: strategy-lab\microstructure\recorder_log.jsonl
echo  STOP: close this window, OR run  python -m src.research_lab.stop_intent
echo ============================================================
python -m src.research_lab.micro_recorder ^
  --symbols BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP,DOGE-USDT-SWAP,PEPE-USDT-SWAP,WIF-USDT-SWAP,BONK-USDT-SWAP,AERO-USDT-SWAP ^
  --duration-seconds 7200 ^
  --interval-seconds 2 ^
  --heartbeat-seconds 30
echo.
echo ===== RECORDER FINISHED - you can close this window manually =====
pause
