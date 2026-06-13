# -*- coding: utf-8 -*-
"""Scan local 1m data for the event microscope — read-only, no download, no run.

Reports, per symbol in a universe group, whether usable 1m data exists plus the
microscope caps. With no 1m files present every symbol is reported as a clean
SKIPPED reason instead of crashing or downloading anything. This never runs a
sweep, never calls an API, and never touches live trading.

    python -m scripts.strategy_lab.microscope_scan --universe l2_high_beta
    python -m scripts.strategy_lab.microscope_scan --universe l2_high_beta --json
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

from src.research_lab.event_microscope import plan_microscope  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, one_minute_glob  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402
from src.research_lab.universe import load_universe  # noqa: E402


def _night_mode(flag: bool) -> bool:
    return flag or os.getenv("STRATEGY_LAB_NIGHT_MODE", "").strip().lower() in {"1", "true", "yes"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="l2_high_beta", help="Universe group to scan")
    ap.add_argument("--data-glob", default=None, help="1m data glob with a {symbol} placeholder (default: private market_data/1m)")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--night-mode", action="store_true")
    ap.add_argument("--json", action="store_true", help="Print the machine-readable summary")
    args = ap.parse_args()

    private_root = Path(args.private_root).expanduser()  # read-only scan, no writes
    data_glob = args.data_glob or one_minute_glob(private_root)
    universe = load_universe()
    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=_night_mode(args.night_mode))
    symbols = list(universe.symbols_in(args.universe))
    if not symbols:
        print(f"unknown or empty universe group: {args.universe}")
        return

    plan = plan_microscope(symbols, data_glob=data_glob, profiles=profiles, policy=policy)
    if args.json:
        print(json.dumps(plan.to_summary(), ensure_ascii=False, indent=2))
        return

    lim = plan.limits
    print("Event microscope (1m) scan")
    print("-" * 48)
    disabled = "" if lim.enabled else f" (disabled: {lim.disabled_reason})"
    print(f"enabled : {lim.enabled}{disabled}")
    print(
        f"limits  : symbols<={lim.max_symbols}, event_windows<={lim.max_event_windows}, "
        f"bars/window<={lim.max_bars_per_window}, variants<={lim.max_variants}"
    )
    counts = plan.availability_counts()
    counts_str = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) if counts else "none scanned"
    print(f"data    : {counts_str}")
    for d in plan.data:
        tag = "OK " if d.status == "available" else "SKIP"
        extra = f" ({d.reason})" if d.reason else ""
        rows = f" rows={d.rows}" if d.rows else ""
        print(f"  [{tag}] {d.symbol}: {d.status}{extra}{rows}")
    for symbol, reason in plan.skipped_symbols:
        print(f"  [SKIP] {symbol}: {reason}")
    print("-" * 48)
    print("read-only: no download, no 1m sweep run, no live trading, no API")
    if not plan.available():
        print("no usable 1m data found -> microscope has nothing to analyze (clean skip)")


if __name__ == "__main__":
    main()
