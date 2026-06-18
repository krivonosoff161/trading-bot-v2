# -*- coding: utf-8 -*-
"""Merge a recorded open-interest slot into a prepared candle file (no faking).

Reads the operator-provided OI slot (market_data/oi/<symbol>_oi.{json,csv} under the
private root), forward-fills it onto the prepared candle file for (symbol, timeframe),
and (on --apply) rewrites the file with an added ``oi`` field so the OI families run
on real recorded data. If the slot is empty/missing it reports source=none and the OI
families stay NEEDS_OI_DATA.

    python -m scripts.strategy_lab.enrich_oi_data --symbol BTC_USDT_SWAP --timeframe 1h --dry-run
    python -m scripts.strategy_lab.enrich_oi_data --symbol BTC_USDT_SWAP --timeframe 1h --apply

Safety: reads only local recorded data; no network, no keys, no orders, no .env.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.experiment import choose_symbol_file, load_candles  # noqa: E402
from src.research_lab.flow_merge import coverage, merge_oi  # noqa: E402
from src.research_lab.oi_slot import load_oi_series  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, market_data_glob  # noqa: E402


def enrich(symbol: str, timeframe: str, private_root: Path, *, apply: bool) -> dict:
    glob = market_data_glob(private_root, timeframe)
    path = choose_symbol_file(glob, symbol, timeframe=timeframe)
    if not path:
        return {"status": "no_prepared_file", "symbol": symbol, "timeframe": timeframe}
    series = load_oi_series(private_root, symbol)
    if not series["points"]:
        return {"status": "no_oi_slot", "symbol": symbol, "source": series["source"],
                "rejected": series["rejected"]}
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows = sorted((r for r in rows if isinstance(r, dict) and "ts" in r), key=lambda r: int(r["ts"]))
    enriched = merge_oi(rows, series["points"])
    cov = coverage(enriched, "oi")
    result = {"status": "enriched" if apply else "would_enrich", "symbol": symbol,
              "timeframe": timeframe, "oi_points": len(series["points"]),
              "source": series["source"], "coverage": cov, "file": path.name}
    if apply:
        path.write_text(json.dumps(enriched, ensure_ascii=False), encoding="utf-8")
        # confirm the loader carries the field through to the feature layer
        result["loaded_with_oi"] = any(c.get("oi") is not None for c in load_candles(path))
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report coverage, write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="rewrite the candle file with the oi field")
    args = ap.parse_args()
    print(json.dumps(enrich(args.symbol, args.timeframe, Path(args.private_root), apply=bool(args.apply)),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
