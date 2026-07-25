# -*- coding: utf-8 -*-
"""Enrich a prepared candle file with public OKX funding rate (flow features).

Reads a prepared private candle file for (symbol, timeframe), fetches the public
funding-rate-history over its time window, forward-fills the funding rate onto each
candle, and (on --apply) rewrites the file with the added ``funding`` field. This
lets the OI/funding flow families run on real funding data. OI history is not
shipped keyless, so this enriches funding only.

    python -m scripts.strategy_lab.enrich_flow_data --symbol BTC_USDT_SWAP --timeframe 1h --dry-run
    python -m scripts.strategy_lab.enrich_flow_data --symbol BTC_USDT_SWAP --timeframe 1h --apply

Safety: public OKX market data only; no keys, no orders, no .env, no private endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.experiment import choose_symbol_file, load_candles  # noqa: E402
from src.research_lab.flow_merge import coverage, merge_funding  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, market_data_glob  # noqa: E402
from src.research_lab.providers.okx_flow import FlowDataError, OkxPublicFundingProvider  # noqa: E402
from src.research_lab.candle_library import sync_json_to_store  # noqa: E402


def enrich(symbol: str, timeframe: str, private_root: Path, *, apply: bool) -> dict:
    glob = market_data_glob(private_root, timeframe)
    path = choose_symbol_file(glob, symbol, timeframe=timeframe)
    if not path:
        return {"status": "no_prepared_file", "symbol": symbol, "timeframe": timeframe}
    candles = load_candles(path)
    if not candles:
        return {"status": "empty_file", "symbol": symbol, "timeframe": timeframe}
    start_ts, end_ts = int(candles[0]["ts"]), int(candles[-1]["ts"])
    try:
        points = OkxPublicFundingProvider().fetch_funding(symbol, start_ts, end_ts)
    except (FlowDataError, ValueError) as exc:
        return {"status": "flow_fetch_failed", "reason": str(exc), "symbol": symbol}
    enriched = merge_funding(candles, points)
    cov = coverage(enriched, "funding")
    result = {"status": "enriched" if apply else "would_enrich", "symbol": symbol,
              "timeframe": timeframe, "funding_points": len(points), "coverage": cov,
              "file": path.name}
    if apply:
        path.write_text(json.dumps(enriched, ensure_ascii=False), encoding="utf-8")
        sync_json_to_store(
            private_root,
            symbol,
            timeframe,
            path,
            source="funding_enrichment",
            available_at_ms=time.time_ns() // 1_000_000,
        )
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="fetch + report coverage, write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="rewrite the candle file with the funding field")
    args = ap.parse_args()
    result = enrich(args.symbol, args.timeframe, Path(args.private_root), apply=bool(args.apply))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
