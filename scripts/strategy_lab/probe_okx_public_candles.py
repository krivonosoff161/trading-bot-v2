# -*- coding: utf-8 -*-
"""Small keyless diagnostic for the public OKX candle path."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider  # noqa: E402

_TF_MS = {
    "1m": 60_000, "15m": 15 * 60_000, "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000, "1d": 24 * 60 * 60_000,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC_USDT_SWAP")
    parser.add_argument("--timeframe", choices=tuple(_TF_MS), default="15m")
    parser.add_argument("--bars", type=int, default=12)
    args = parser.parse_args()
    bars = min(300, max(2, int(args.bars)))
    end_ts = int(time.time() * 1000)
    start_ts = end_ts - bars * _TF_MS[args.timeframe]
    provider = OkxPublicMarketDataProvider(
        timeout=8.0, max_pages=2, sleep_seconds=0.0, retry_attempts=2,
    )
    try:
        rows = provider.fetch_ohlcv(args.symbol, args.timeframe, start_ts, end_ts)
        payload = {
            "status": "ok" if rows else "no_data",
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "rows": len(rows),
            "first_ts": int(rows[0]["ts"]) if rows else None,
            "last_ts": int(rows[-1]["ts"]) if rows else None,
            "public_only": True,
        }
        print(json.dumps(payload, ensure_ascii=False))
    finally:
        owned = getattr(provider, "_owned_http_get", None)
        if owned is not None:
            owned.close()


if __name__ == "__main__":
    main()
