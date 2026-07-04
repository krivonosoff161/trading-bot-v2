# -*- coding: utf-8 -*-
"""Micro-scalp search — systematically vary WHEN to enter and HOW FAST to exit (research-only).

Don't fixate on one idea. The two real levers for micro-trading are the ENTRY TRIGGER (when) and the
EXIT SPEED (how fast). This sweeps both axes across timeframes on a broad meme/alt sample and ranks the
combinations honestly (OOS, two cost levels, mirage-guarded), so the data says which "when x how-fast"
actually beats the round-trip cost.

Entry triggers (all fades — momentum is a coin-flip, overshoots revert):
  * range_fade   — bar range >> recent average -> fade the spike
  * body_fade    — bar body >> recent average body -> fade the thrust
  * wick_fade    — a long rejection wick -> fade toward the wick (rejection)
  * nbar_fade    — N consecutive same-direction bars -> fade the exhaustion

Exit speeds (how fast):
  * close_1 / close_2 / close_3 — exit at the close of entry+N bars (close_1 = the fastest micro exit)
  * first_green — exit at the first profitable close (cap at max_hold)
  * tp_sl       — a tight take/stop barrier

Keyless public candles; nothing edge or paper-ready; no order/money/live path.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import mean
from typing import Any, Callable

from src.research_lab.strategies._helpers import sma

TAKER_RT = 0.10
TIGHT_RT = 0.04
OOS_FRAC = 0.35
MIN_OOS_TRADES = 40
MIN_SYMBOLS = 5
SYMBOLS = ("PEPE-USDT-SWAP", "BONK-USDT-SWAP", "WIF-USDT-SWAP", "FLOKI-USDT-SWAP", "SHIB-USDT-SWAP",
           "DOGE-USDT-SWAP", "PUMP-USDT-SWAP", "PENGU-USDT-SWAP", "MEW-USDT-SWAP", "NEIRO-USDT-SWAP",
           "BOME-USDT-SWAP", "TRUMP-USDT-SWAP", "WLD-USDT-SWAP", "SAND-USDT-SWAP")


def _bull(c: dict[str, Any]) -> bool:
    return float(c["close"]) > float(c["open"])


# ── entry triggers: (idx -> "long"/"short"/None), no look-ahead (use bars <= idx) ──────────
def _range_fade(candles, i, n_avg, mult):
    ranges = [(float(c["high"]) - float(c["low"])) / max(1e-12, float(c["open"])) for c in candles]
    avg = sma(ranges, i, n_avg)
    if avg is None or ranges[i] <= avg * mult:
        return None
    return "short" if _bull(candles[i]) else "long"


def _body_fade(candles, i, n_avg, mult):
    bodies = [abs(float(c["close"]) - float(c["open"])) / max(1e-12, float(c["open"])) for c in candles]
    avg = sma(bodies, i, n_avg)
    if avg is None or bodies[i] <= avg * mult:
        return None
    return "short" if _bull(candles[i]) else "long"


def _wick_fade(candles, i, _n, mult):
    c = candles[i]
    o, h, lo, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
    body = abs(cl - o) or 1e-9
    up_wick, dn_wick = h - max(o, cl), min(o, cl) - lo
    if up_wick > body * mult and up_wick > dn_wick:
        return "short"            # long upper wick = rejection of higher prices -> fade down
    if dn_wick > body * mult and dn_wick > up_wick:
        return "long"             # long lower wick = rejection of lower prices -> fade up
    return None


def _nbar_fade(candles, i, n, _mult):
    if i < n:
        return None
    if all(_bull(candles[i - j]) for j in range(n)):
        return "short"
    if all(not _bull(candles[i - j]) for j in range(n)):
        return "long"
    return None


ENTRY_TRIGGERS: dict[str, Callable] = {"range_fade": _range_fade, "body_fade": _body_fade,
                                       "wick_fade": _wick_fade, "nbar_fade": _nbar_fade}


# ── exit speeds: given entry idx + side, return gross return % (no look-ahead beyond the rule) ──
def _exit_gross(candles, idx, side, spec) -> float | None:
    entry = float(candles[idx]["open"])
    if entry <= 0 or idx >= len(candles):
        return None
    long_ = side == "long"
    last = len(candles) - 1
    kind = spec["kind"]
    if kind == "nclose":
        cap = min(idx + spec["n"] - 1, last)
        exit_price = float(candles[cap]["close"])
    elif kind == "first_green":
        cap = min(idx + spec["max_hold"], last)
        exit_price = float(candles[cap]["close"])
        for j in range(idx, cap + 1):
            px = float(candles[j]["close"])
            if (long_ and px > entry) or (not long_ and px < entry):
                exit_price = px
                break
    else:  # tp_sl barrier
        cap = min(idx + spec["max_hold"], last)
        stop = entry * (1 - spec["sl"] / 100) if long_ else entry * (1 + spec["sl"] / 100)
        take = entry * (1 + spec["tp"] / 100) if long_ else entry * (1 - spec["tp"] / 100)
        exit_price = float(candles[cap]["close"])
        for j in range(idx, cap + 1):
            hi, lo = float(candles[j]["high"]), float(candles[j]["low"])
            if (long_ and lo <= stop) or (not long_ and hi >= stop):
                exit_price = stop
                break
            if (long_ and hi >= take) or (not long_ and lo <= take):
                exit_price = take
                break
    return (exit_price / entry - 1) * 100 * (1 if long_ else -1)


def _exit_specs(tf: str) -> dict[str, dict[str, Any]]:
    tp = {"1m": 0.5, "5m": 1.0, "15m": 1.6}.get(tf, 1.0)
    sl = {"1m": 0.4, "5m": 0.8, "15m": 1.2}.get(tf, 0.8)
    return {"close_1": {"kind": "nclose", "n": 1}, "close_2": {"kind": "nclose", "n": 2},
            "close_3": {"kind": "nclose", "n": 3}, "first_green": {"kind": "first_green", "max_hold": 5},
            "tp_sl": {"kind": "tp_sl", "tp": tp, "sl": sl, "max_hold": 4}}


def _entry_grid() -> list[dict[str, Any]]:
    grid = []
    for mult in (2.5, 3.0, 4.0):
        grid.append({"label": f"range_fade|x{mult}", "trigger": "range_fade", "n_avg": 20, "mult": mult})
        grid.append({"label": f"body_fade|x{mult}", "trigger": "body_fade", "n_avg": 20, "mult": mult})
    for mult in (2.0, 3.0):
        grid.append({"label": f"wick_fade|x{mult}", "trigger": "wick_fade", "n_avg": 0, "mult": mult})
    for n in (3, 4, 5):
        grid.append({"label": f"nbar_fade|n{n}", "trigger": "nbar_fade", "n_avg": n, "mult": 0})
    return grid


def run(private_root: Path, *, timeframes=("1m", "5m", "15m"), symbols=SYMBOLS, provider=None) -> dict[str, Any]:
    if provider is None:
        from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
        provider = OkxPublicMarketDataProvider()
    now = int(time.time() * 1000)
    entry_grid = _entry_grid()
    acc: dict[str, dict[str, Any]] = {}
    for tf in timeframes:
        bars_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000}.get(tf, 60_000)
        start = now - 1900 * bars_ms
        specs = _exit_specs(tf)
        for sym in symbols:
            s = sym.replace("-", "_")
            try:
                candles = provider.fetch_ohlcv(s, tf, start, now)
            except Exception:  # noqa: BLE001 - network must not crash the sweep
                continue
            if len(candles) < 200:
                continue
            cut = int(len(candles) * (1.0 - OOS_FRAC))
            for eg in entry_grid:
                trig = ENTRY_TRIGGERS[eg["trigger"]]
                entries = [(i, trig(candles, i, eg["n_avg"], eg["mult"]))
                           for i in range(max(eg["n_avg"], 20), len(candles) - 1)]
                entries = [(i, side) for i, side in entries if side]
                for ex_name, spec in specs.items():
                    key = f"{eg['label']}|{ex_name}::{tf}"
                    for i, side in entries:
                        if i < cut:
                            continue
                        g = _exit_gross(candles, i + 1, side, spec)
                        if g is None:
                            continue
                        cell = acc.setdefault(key, {"label": f"{eg['label']}|{ex_name}", "tf": tf,
                                                    "syms": set(), "oos": []})
                        cell["syms"].add(sym)
                        cell["oos"].append(g)
    return {"symbols": len(symbols), "summary": _summarize(acc)}


def _summarize(acc: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = []
    for cell in acc.values():
        g = cell["oos"]
        if len(g) < MIN_OOS_TRADES or len(cell["syms"]) < MIN_SYMBOLS:
            continue
        gross = mean(g)
        out.append({"label": cell["label"], "tf": cell["tf"], "symbols": len(cell["syms"]),
                    "oos_trades": len(g), "gross": round(gross, 4),
                    "net_taker": round(gross - TAKER_RT, 4), "net_tight": round(gross - TIGHT_RT, 4),
                    "win": round(sum(1 for x in g if x > 0) / len(g), 3)})
    ranked = sorted(out, key=lambda c: -c["net_taker"])
    return {"ranked": ranked, "taker_positive": [c for c in ranked if c["net_taker"] > 0.005],
            "note": "when-to-enter x how-fast-to-exit sweep, pooled OOS, two cost levels. taker-positive "
                    "on a broad base = candidate; nothing paper-ready"}


def write_snapshot(private_root: Path, report: dict[str, Any]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "microscalp_search.json"
    payload = {"schema": "microscalp_search.v1",
               "disclaimer": "Entry-trigger x exit-speed sweep on keyless public candles, OOS, two costs. "
                             "Research-only; nothing edge or paper-ready.", **report}
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
    ap = argparse.ArgumentParser(description="Micro-scalp entry x exit-speed sweep (keyless, research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    report = run(Path(args.private_root))
    s = report["summary"]
    print(f"taker-positive cells: {len(s['taker_positive'])}  TOP 14 by net_taker:")
    print(f"  {'when|how-fast':34s}{'tf':4s}{'sym':4s}{'trd':6s}{'gross':>8s}{'net_tk':>8s}{'net_tt':>8s}{'win':>6s}")
    for c in s["ranked"][:14]:
        print(f"  {c['label']:34s}{c['tf']:4s}{c['symbols']:<4d}{c['oos_trades']:<6d}{c['gross']:+8.3f}"
              f"{c['net_taker']:+8.3f}{c['net_tight']:+8.3f}{c['win']:>6.2f}")
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
