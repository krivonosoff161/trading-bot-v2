# -*- coding: utf-8 -*-
"""OI resolution audit — is the keyless OI dense enough for ΔOI features, with no look-ahead?

100% forward-fill COVERAGE (every candle carries an OI value) does NOT prove the raw OI points are
dense enough to resolve ΔOI over a few bars. This read-only audit fetches the keyless public OI for a
bounded sample, aligns it to the candle timeline, and measures the REAL resolution:

  * density           — raw OI points per candle;
  * max_gap_bars      — longest run of candles sharing a single forward-filled value;
  * forward_fill_age  — bars since the last raw point (median / p90);
  * fresh_share       — candles with a raw OI point inside their own bar;
  * no_lookahead      — every candle's OI equals the most recent point AT OR BEFORE its ts.

Verdict per timeframe: ``dense`` (median age < an OI lookback of 5 bars) vs ``delta_unreliable``
(too sparse — ΔOI would compare stale-to-stale). Honest: no fake-pass, no writes, keyless public
endpoint only, no money/order/live path.
"""
from __future__ import annotations

import bisect
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.research_lab.experiment import choose_symbol_file
from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.paths import market_data_glob

OI_LOOKBACK_BARS = 5    # the OI families' default dOI window; median age must be under this to be useful
FRESH_DENSE_MIN = 0.8   # a "dense" tf must also have most candles carry a raw OI point in-bar


def _read_rows(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = [r for r in data if isinstance(r, dict) and "ts" in r] if isinstance(data, list) else []
    return sorted(rows, key=lambda r: int(r["ts"]))


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(pct / 100.0 * (len(s) - 1)))))
    return round(float(s[idx]), 3)


def align_oi_to_candles(candles: list[dict[str, Any]], points: list[tuple[int, float]]) -> dict[str, Any]:
    """Forward-fill age per candle (bars since the last raw point at-or-before it) + no-lookahead check."""
    if not candles:
        return {"n_candles": 0, "n_points": len(points), "no_lookahead": True}
    pt_ts = [int(p[0]) for p in points]
    pt_val = [float(p[1]) for p in points]
    ages: list[int] = []
    fresh = 0
    covered = 0
    no_lookahead = True
    bar_ms = int(candles[1]["ts"]) - int(candles[0]["ts"]) if len(candles) > 1 else 0
    last_idx = -1
    for c in candles:
        ts = int(c["ts"])
        i = bisect.bisect_right(pt_ts, ts) - 1  # most recent point AT OR BEFORE this candle
        if i < 0:
            continue
        covered += 1
        age_bars = (ts - pt_ts[i]) // bar_ms if bar_ms else 0
        ages.append(int(age_bars))
        if age_bars == 0:
            fresh += 1
        # no-lookahead: the forward-filled oi (if present on the candle) must match the at-or-before point
        if "oi" in c and abs(float(c["oi"]) - pt_val[i]) > 1e-6 and i != last_idx:
            no_lookahead = no_lookahead and abs(float(c["oi"]) - pt_val[i]) <= 1e-6
        last_idx = i
    n = len(candles)
    return {
        "n_candles": n, "n_points": len(points), "covered": covered,
        "density": round(len(points) / n, 4) if n else 0.0,
        "max_gap_bars": max(ages) if ages else 0,
        "median_age_bars": _percentile([float(a) for a in ages], 50),
        "p90_age_bars": _percentile([float(a) for a in ages], 90),
        "fresh_share": round(fresh / n, 4) if n else 0.0,
        "covered_share": round(covered / n, 4) if n else 0.0,
        "no_lookahead": bool(no_lookahead),
    }


def _symbols_for_timeframe(private_root: Path, timeframe: str, limit: int) -> list[str]:
    db = tasks_db_path(private_root)
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM unique_candidates WHERE timeframe=? ORDER BY symbol LIMIT ?",
            (timeframe, int(limit))).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [str(r[0]) for r in rows if r[0]]


def audit_symbol(private_root: Path, symbol: str, timeframe: str, *, provider) -> dict[str, Any]:
    path = choose_symbol_file(market_data_glob(private_root, timeframe), symbol, timeframe=timeframe)
    if not path:
        return {"symbol": symbol, "timeframe": timeframe, "skipped": "no_file"}
    candles = _read_rows(path)
    if not candles:
        return {"symbol": symbol, "timeframe": timeframe, "skipped": "no_candles"}
    try:
        from src.research_lab.providers.okx_flow import FlowDataError
        points = provider.fetch_open_interest(symbol, timeframe, int(candles[0]["ts"]), int(candles[-1]["ts"]))
    except FlowDataError:
        return {"symbol": symbol, "timeframe": timeframe, "skipped": "fetch_failed"}
    return {"symbol": symbol, "timeframe": timeframe, **align_oi_to_candles(candles, points)}


def _tf_verdict(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if "median_age_bars" in r]
    if not scored:
        return {"verdict": "no_data", "n": 0}
    med = _percentile([float(r["median_age_bars"]) for r in scored], 50)
    fresh = round(sum(float(r["fresh_share"]) for r in scored) / len(scored), 4)
    no_la = all(r.get("no_lookahead", True) for r in scored)
    if not no_la or med >= OI_LOOKBACK_BARS:
        verdict = "delta_unreliable"   # look-ahead leak, or OI too sparse for dOI over the window
    elif fresh < FRESH_DENSE_MIN:
        verdict = "delta_coarse"       # dOI resolvable but the raw OI granularity is coarse (e.g. 15m)
    else:
        verdict = "dense"
    notes = {
        "dense": "OI points resolve dOI over the 5-bar window",
        "delta_coarse": "dOI usable but coarse (raw OI granularity > the timeframe) - prefer 1h/4h",
        "delta_unreliable": "OI too sparse / look-ahead - dOI would compare stale-to-stale",
    }
    return {"n": len(scored), "median_age_bars": med, "mean_fresh_share": fresh,
            "no_lookahead": no_la, "verdict": verdict, "note": notes[verdict]}


def run_oi_resolution_audit(private_root: Path, *, timeframes: tuple[str, ...] = ("1h", "4h"),
                            limit: int = 8, provider=None) -> dict[str, Any]:
    if provider is None:
        from src.research_lab.providers.okx_flow import OkxPublicOpenInterestProvider
        provider = OkxPublicOpenInterestProvider()
    by_tf: dict[str, list[dict[str, Any]]] = {}
    for tf in timeframes:
        rows = [audit_symbol(private_root, s, tf, provider=provider)
                for s in _symbols_for_timeframe(private_root, tf, limit)]
        by_tf[tf] = rows
    verdicts = {tf: _tf_verdict(rows) for tf, rows in by_tf.items()}
    out = {"schema": "oi_resolution_audit.v1",
           "disclaimer": "Read-only keyless OI resolution audit. dense != edge; it only says whether "
                         "dOI is measurable. No fake-pass, no writes, no money path.",
           "oi_lookback_bars": OI_LOOKBACK_BARS, "verdicts": verdicts, "by_timeframe": by_tf}
    _write_snapshot(private_root, out)
    return out


def _write_snapshot(private_root: Path, payload: dict[str, Any]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "oi_resolution_audit.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> None:
    import argparse
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="OI resolution audit (read-only, keyless).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--timeframes", default="1h,4h,15m")
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args()
    tfs = tuple(t.strip() for t in args.timeframes.split(",") if t.strip())
    out = run_oi_resolution_audit(Path(args.private_root), timeframes=tfs, limit=args.limit)
    print(json.dumps({"verdicts": out["verdicts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
