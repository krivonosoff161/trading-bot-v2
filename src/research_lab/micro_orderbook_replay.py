# -*- coding: utf-8 -*-
"""Orderbook-pressure follow-through replay on the RECORDED book snapshots (Theme 40, research-only).

The tape replay tested executed-flow pressure (null). This tests the DIFFERENT signal the tape can't
see: resting-liquidity pressure (top-N imbalance + a dominant near-mid wall). For each orderbook event
(no look-ahead: the event at snapshot k uses only k and earlier), measure the FORWARD mid-price move at
30s/1m/3m/5m from later snapshots, under a conservative taker cost. Short on ask-heavy pressure, long on
bid-heavy. Classify into the micro buckets.

Reads only the recorder's own public snapshots under the private root. "follow-through observed" is a
mechanical observation, never edge and never paper-ready. No order/account/private/live path.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from statistics import median
from typing import Any

from src.research_lab.micro_features import liquidity_wall, mid_price, orderbook_imbalance, spread_bps
from src.research_lab.micro_memory import OBI_THRESH, SPREAD_MAX_BPS, WALL_DIST_MAX_BPS

HORIZONS_MS = (30_000, 60_000, 180_000, 300_000)
PRIMARY_MS = 60_000
TAKER_ROUNDTRIP_PCT = 0.10   # conservative fast-scalp cost (taker both sides)
MIN_EVENTS = 20


def _series(snaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Time-ordered snapshots reduced to (t, mid, obi, book) — drops snapshots without a 2-sided book."""
    out: list[dict[str, Any]] = []
    for s in sorted(snaps, key=lambda x: int(x.get("recv_ms") or 0)):
        book = {"bids": (s.get("book") or {}).get("bids") or [], "asks": (s.get("book") or {}).get("asks") or []}
        if not book["bids"] or not book["asks"]:
            continue
        mid = mid_price(book)
        if mid <= 0:
            continue
        out.append({"t": int(s.get("recv_ms") or 0), "mid": mid, "obi": orderbook_imbalance(book, depth=5),
                    "spread": spread_bps(book), "book": book})
    return out


def _events_with_followthrough(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for k, s in enumerate(series):
        heavy_ask, heavy_bid = s["obi"] <= -OBI_THRESH, s["obi"] >= OBI_THRESH
        if not (heavy_ask or heavy_bid):
            continue
        if s["spread"] > SPREAD_MAX_BPS:
            continue
        wall = liquidity_wall(s["book"], "ask" if heavy_ask else "bid")
        if not wall.get("present") or wall.get("distance_bps", 1e9) > WALL_DIST_MAX_BPS:
            continue
        side = "short" if heavy_ask else "long"
        rows.append({**_follow(series, k, s["mid"], side), "side": side})
    return rows


def _follow(series: list[dict[str, Any]], k: int, entry: float, side: str) -> dict[str, Any]:
    """Forward mid-price move at each horizon (outcome; future snapshots allowed)."""
    dirn = -1 if side == "short" else 1
    t0 = series[k]["t"]
    horizons: dict[str, float] = {}
    hi = lo = entry
    targets = {h: None for h in HORIZONS_MS}
    for s in series[k + 1:]:
        dt = s["t"] - t0
        if dt > HORIZONS_MS[-1]:
            break
        hi, lo = max(hi, s["mid"]), min(lo, s["mid"])
        for h in HORIZONS_MS:
            if targets[h] is None and dt >= h:
                ret = dirn * (s["mid"] / entry - 1) * 100
                horizons[f"{h//1000}s"] = round(ret - TAKER_ROUNDTRIP_PCT, 4)
                targets[h] = s["mid"]
    mfe = (hi - entry) / entry * 100 if side == "long" else (entry - lo) / entry * 100
    return {"primary_net_pct": horizons.get(f"{PRIMARY_MS//1000}s"), "horizons_net_pct": horizons,
            "mfe_pct": round(max(0.0, mfe), 4)}


def _bucket(rows: list[dict[str, Any]]) -> str:
    scored = [r for r in rows if r.get("primary_net_pct") is not None]
    if len(scored) < MIN_EVENTS:
        return "needs_more_samples"
    nets = [r["primary_net_pct"] for r in scored]
    med, win = median(nets), sum(1 for x in nets if x > 0) / len(nets)
    if med > 0 and win > 0.5:
        return "followthrough_observed"
    if med <= 0 and median([r["mfe_pct"] for r in scored]) > TAKER_ROUNDTRIP_PCT * 2:
        return "valid_pressure_but_bad_exit"
    return "weak_followthrough"


def _horizon_curve(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in HORIZONS_MS:
        key = f"{h//1000}s"
        nets = [r["horizons_net_pct"][key] for r in rows if key in (r.get("horizons_net_pct") or {})]
        if nets:
            out[key] = {"median_net_pct": round(median(nets), 4),
                        "win_rate": round(sum(1 for x in nets if x > 0) / len(nets), 4), "n": len(nets)}
    return out


def _read(private_root: Path) -> dict[str, list[dict[str, Any]]]:
    out_dir = Path(private_root) / "microstructure" / "recordings"
    by_sym: dict[str, list[dict[str, Any]]] = {}
    for p in (sorted(out_dir.rglob("*.jsonl.gz")) if out_dir.exists() else []):
        sym = p.stem.replace(".jsonl", "")
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                for line in f:
                    by_sym.setdefault(sym, []).append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            continue
    return by_sym


def run(private_root: Path) -> dict[str, Any]:
    per_sym = _read(Path(private_root))
    all_rows: list[dict[str, Any]] = []
    by_symbol: dict[str, int] = {}
    for sym, snaps in per_sym.items():
        rows = _events_with_followthrough(_series(snaps))
        by_symbol[sym] = len(rows)
        all_rows.extend(rows)
    return {"snapshots": sum(len(v) for v in per_sym.values()), "events": len(all_rows),
            "summary": _summarize(all_rows, by_symbol)}


def _summarize(rows: list[dict[str, Any]], by_symbol: dict[str, int]) -> dict[str, Any]:
    scored = [r for r in rows if r.get("primary_net_pct") is not None]
    by_side = {}
    for sd in ("short", "long"):
        s = [r for r in scored if r["side"] == sd]
        nets = [r["primary_net_pct"] for r in s]
        by_side[sd] = {"n": len(s), "median_net_pct": round(median(nets), 4) if nets else 0.0,
                       "win_rate": round(sum(1 for x in nets if x > 0) / len(nets), 4) if nets else 0.0,
                       "bucket": _bucket(s)}
    return {"events_scored": len(scored), "primary_horizon": f"{PRIMARY_MS//1000}s",
            "by_side": by_side, "overall_bucket": _bucket(scored),
            "horizon_curve_all": _horizon_curve(scored), "events_by_symbol": by_symbol,
            "note": "orderbook-pressure follow-through on recorded books (taker cost). observed != edge; "
                    "nothing paper-ready"}


def write_snapshot(private_root: Path, report: dict[str, Any]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "micro_orderbook_replay.json"
    payload = {"schema": "micro_orderbook_replay.v1",
               "disclaimer": "Orderbook-pressure follow-through on recorded public books. Research-only; "
                             "follow-through != edge; nothing paper-ready.",
               "snapshots": report.get("snapshots"), "events": report.get("events"),
               "summary": report.get("summary")}
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
    ap = argparse.ArgumentParser(description="Orderbook-pressure follow-through replay (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    report = run(Path(args.private_root))
    print(json.dumps({k: report[k] for k in ("snapshots", "events", "summary")}, ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
