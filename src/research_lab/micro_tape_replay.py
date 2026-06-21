# -*- coding: utf-8 -*-
"""Tape-pressure replay on the REAL historical tick tape — does aggression mechanically follow through?

Research-only. Reads the existing trade tape (manifest -> E:\\... csv[.gz], trades-only with aggressor
side), detects tape-pressure events with NO look-ahead (the event at time t uses only trades in
[t-lookback, t]), then measures FORWARD follow-through at horizons (10s/30s/1m/3m/5m) under conservative
taker costs. It stores positive AND negative outcomes and classifies them into micro buckets.

This is the trades sub-lane (orderbook walls need the forward recorder). "follow-through observed" is a
mechanical observation, NEVER edge and never paper-ready. Bounded: file/event caps + stop-file. No
order/account/private/live path; the tape is read-only local data.
"""
from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path
from statistics import median
from typing import Any

from src.research_lab.micro_features import tape_delta, tape_speed

LOOKBACK_MS = 10_000          # event window: aggression measured over the last 10s
COOLDOWN_MS = 60_000          # min gap between events on one symbol (non-overlapping)
CVD_THRESH = 0.35             # |buy-sell|/(buy+sell) over lookback to call a pressure event
SPEED_THRESH = 1.0            # trades/sec over lookback (liquidity filter)
HORIZONS_MS = (10_000, 30_000, 60_000, 180_000, 300_000)
TAKER_ROUNDTRIP_PCT = 0.10    # conservative fast-scalp cost (taker both sides), percent points
PRIMARY_HORIZON_MS = 60_000   # the horizon the bucket verdict is based on


def _read_tape(path: str) -> list[tuple[int, str, float, float]]:
    """Load one tape file as (ts_ms, side, price, size); GAP markers and bad rows dropped."""
    op = gzip.open if str(path).endswith(".gz") else open
    out: list[tuple[int, str, float, float]] = []
    try:
        with op(path, "rt", encoding="utf-8", newline="") as f:
            rd = csv.DictReader(f)
            for row in rd:
                side = (row.get("side") or "").lower()
                if side not in ("buy", "sell"):
                    continue
                try:
                    out.append((int(row["ts_ms"]), side, float(row["price"]), float(row["size"])))
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        return []
    return out


def _events(trades: list[tuple[int, str, float, float]], *, max_events: int) -> list[dict[str, Any]]:
    """Non-overlapping tape-pressure events (no look-ahead: lookback window ends at t)."""
    if not trades:
        return []
    events: list[dict[str, Any]] = []
    last_ev_ts = -COOLDOWN_MS
    lo = 0
    for i, (ts, _side, price, _sz) in enumerate(trades):
        if ts - last_ev_ts < COOLDOWN_MS:
            continue
        while trades[lo][0] < ts - LOOKBACK_MS:
            lo += 1
        window = [{"ts_ms": t, "side": s, "size": z} for (t, s, _p, z) in trades[lo:i + 1]]
        d = tape_delta(window)
        speed = tape_speed(window, window_ms=LOOKBACK_MS)
        if speed < SPEED_THRESH or abs(d["cvd_ratio"]) < CVD_THRESH:
            continue
        side = "short" if d["cvd_ratio"] <= -CVD_THRESH else "long"
        events.append({"idx": i, "ts_ms": ts, "entry": price, "side": side,
                       "cvd": d["cvd_ratio"], "speed": speed})
        last_ev_ts = ts
        if len(events) >= max_events:
            break
    return events


def _follow_through(trades: list[tuple[int, str, float, float]], ev: dict[str, Any]) -> dict[str, Any]:
    """Forward price path after the event (this is the OUTCOME — future data is allowed here)."""
    t0, entry, short = ev["ts_ms"], ev["entry"], ev["side"] == "short"
    dirn = -1 if short else 1
    horizons: dict[str, float] = {}
    target_idx = {h: None for h in HORIZONS_MS}
    hi = lo = entry
    for (ts, _s, price, _z) in trades[ev["idx"] + 1:]:
        dt = ts - t0
        if dt > HORIZONS_MS[-1]:
            break
        hi, lo = max(hi, price), min(lo, price)
        for h in HORIZONS_MS:
            if target_idx[h] is None and dt >= h:
                ret = dirn * (price / entry - 1) * 100
                horizons[f"{h//1000}s"] = round(ret - TAKER_ROUNDTRIP_PCT, 4)
                target_idx[h] = price
    mfe = dirn * ((hi if not short else entry) / entry - 1) * 100 if not short else (entry - lo) / entry * 100
    mae = (entry - lo) / entry * 100 if not short else (hi - entry) / entry * 100
    prim = horizons.get(f"{PRIMARY_HORIZON_MS//1000}s")
    return {"horizons_net_pct": horizons, "primary_net_pct": prim,
            "mfe_pct": round(max(0.0, mfe), 4), "mae_pct": round(max(0.0, mae), 4)}


# Micro outcome buckets (research-only; none is edge or paper-ready)
def _bucket(rows: list[dict[str, Any]]) -> str:
    scored = [r for r in rows if r.get("primary_net_pct") is not None]
    if len(scored) < 20:
        return "needs_more_samples"
    nets = [r["primary_net_pct"] for r in scored]
    med = median(nets)
    win = sum(1 for x in nets if x > 0) / len(nets)
    mfe_med = median([r["mfe_pct"] for r in scored])
    if med > 0 and win > 0.5:
        return "followthrough_observed"          # mechanical follow-through (NOT edge)
    if med <= 0 and mfe_med > TAKER_ROUNDTRIP_PCT * 2:
        return "valid_pressure_but_bad_exit"      # move existed but the fixed horizon gave it back
    return "weak_followthrough"


def replay_file(path: str, symbol: str, *, max_events: int) -> dict[str, Any]:
    trades = _read_tape(path)
    if len(trades) < 100:
        return {"symbol": symbol, "skipped": "too_few_trades", "n": len(trades)}
    evs = _events(trades, max_events=max_events)
    rows = [{**e, **_follow_through(trades, e)} for e in evs]
    return {"symbol": symbol, "n_trades": len(trades), "n_events": len(rows), "rows": rows}


def _manifest_files(private_root: Path, *, max_files: int, symbols: list[str] | None) -> list[tuple[str, str]]:
    """Round-robin one file per symbol so a bounded run samples ACROSS symbols, not one symbol's files."""
    man_dir = Path(private_root) / "manifests"
    files = sorted(man_dir.glob("tape_files_*.csv"))
    if not files:
        return []
    by_sym: dict[str, list[str]] = {}
    with files[-1].open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sym = row.get("symbol") or ""
            if symbols and sym not in symbols:
                continue
            ap = row.get("abs_path") or ""
            if ap and Path(ap).exists():
                by_sym.setdefault(sym, []).append(ap)
    out: list[tuple[str, str]] = []
    round_i = 0
    while len(out) < max_files and any(round_i < len(v) for v in by_sym.values()):
        for sym in sorted(by_sym):
            if round_i < len(by_sym[sym]):
                out.append((by_sym[sym][round_i], sym))
                if len(out) >= max_files:
                    break
        round_i += 1
    return out


def run(private_root: Path, *, max_files: int = 8, max_events_per_file: int = 60,
        symbols: list[str] | None = None) -> dict[str, Any]:
    from src.research_lab.stop_intent import is_stop_requested
    private_root = Path(private_root)
    files = _manifest_files(private_root, max_files=max_files, symbols=symbols)
    per_symbol: dict[str, list[dict[str, Any]]] = {}
    files_done = 0
    for ap, sym in files:
        if is_stop_requested(private_root):
            break
        res = replay_file(ap, sym, max_events=max_events_per_file)
        files_done += 1
        if "rows" in res:
            per_symbol.setdefault(sym, []).extend(res["rows"])
    all_rows = [r for rows in per_symbol.values() for r in rows]
    return {"files_available": len(files), "files_replayed": files_done,
            "events": len(all_rows), "summary": _summarize(all_rows, per_symbol)}


def _horizon_curve(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Median net at EVERY horizon (10s..5m) so a short-horizon effect that decays is not hidden."""
    out: dict[str, Any] = {}
    for h in HORIZONS_MS:
        key = f"{h//1000}s"
        nets = [r["horizons_net_pct"][key] for r in rows if key in (r.get("horizons_net_pct") or {})]
        if nets:
            out[key] = {"median_net_pct": round(median(nets), 4),
                        "win_rate": round(sum(1 for x in nets if x > 0) / len(nets), 4), "n": len(nets)}
    return out


def _summarize(rows: list[dict[str, Any]], per_symbol: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    scored = [r for r in rows if r.get("primary_net_pct") is not None]
    by_side: dict[str, dict[str, Any]] = {}
    for sd in ("short", "long"):
        s = [r for r in scored if r["side"] == sd]
        nets = [r["primary_net_pct"] for r in s]
        by_side[sd] = {"n": len(s), "median_net_pct": round(median(nets), 4) if nets else 0.0,
                       "win_rate": round(sum(1 for x in nets if x > 0) / len(nets), 4) if nets else 0.0,
                       "bucket": _bucket(s), "horizon_curve": _horizon_curve(s)}
    return {"events_scored": len(scored), "primary_horizon": f"{PRIMARY_HORIZON_MS//1000}s",
            "by_side": by_side, "overall_bucket": _bucket(scored),
            "horizon_curve_all": _horizon_curve(scored),
            "symbols": {k: len(v) for k, v in per_symbol.items()},
            "note": "mechanical follow-through on the real tape (taker costs). observed != edge; "
                    "no orderbook/wall features here (no book history); nothing paper-ready"}


def write_snapshot(private_root: Path, report: dict[str, Any]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "micro_tape_replay.json"
    payload = {"schema": "micro_tape_replay.v1",
               "disclaimer": "Tape-pressure follow-through on the real historical tick tape. Research-only; "
                             "trades-only (no orderbook walls); follow-through != edge; nothing paper-ready.",
               "files_available": report.get("files_available"), "files_replayed": report.get("files_replayed"),
               "events": report.get("events"), "summary": report.get("summary")}
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
    ap = argparse.ArgumentParser(description="Tape-pressure follow-through replay on the real tape (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--max-files", type=int, default=8)
    ap.add_argument("--max-events-per-file", type=int, default=60)
    ap.add_argument("--symbols", default="", help="comma-separated instIds to restrict to")
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
    report = run(Path(args.private_root), max_files=args.max_files,
                 max_events_per_file=args.max_events_per_file, symbols=syms)
    print(json.dumps({k: report[k] for k in ("files_available", "files_replayed", "events", "summary")},
                     ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
