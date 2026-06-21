# -*- coding: utf-8 -*-
"""Confirmed-fade search — does candlestick / technical confirmation add edge to the fade? (research-only)

Builds on the robust finding (fade overshoots + fast exit beats cost on 5m/15m). The owner asked to also
use TECHNICAL ANALYSIS and CANDLESTICK ANALYSIS, and to hold positions only within realistic per-TF
horizons. So this takes the base spike-fade and tests whether requiring a confirmation at the spike bar
LIFTS the net/win over the raw fade:

  candlestick: pin_bar (rejection wick) · engulfing · doji (indecision)
  technical:   rsi_extreme (RSI>70 fade short / <30 fade long) · bb_break (close beyond Bollinger band)

Exit = first profitable close OR a tight take/stop, time-stopped at the per-TF holding horizon the owner
specified (1m~5min, 5m~2h, 15m~7h, 1h~1.5d, 1d~5d). Pooled OOS across a broad meme/alt sample, two cost
levels, mirage-guarded. A confirmation is worth it only if it raises net/win on a broad base.

Keyless public candles; nothing edge or paper-ready; no order/money/live path.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import mean
from typing import Any

from src.research_lab.strategies._helpers import rsi_at, sma

TAKER_RT = 0.10
TIGHT_RT = 0.04
OOS_FRAC = 0.35
MIN_OOS_TRADES = 30
MIN_SYMBOLS = 5
RANGE_MULT = 3.0
# the owner's per-TF holding horizons, expressed as max bars (time-stop)
HORIZON_BARS = {"1m": 5, "5m": 24, "15m": 28, "1h": 36, "1d": 5}
SYMBOLS = ("PEPE-USDT-SWAP", "BONK-USDT-SWAP", "WIF-USDT-SWAP", "FLOKI-USDT-SWAP", "SHIB-USDT-SWAP",
           "DOGE-USDT-SWAP", "PUMP-USDT-SWAP", "PENGU-USDT-SWAP", "MEW-USDT-SWAP", "TRUMP-USDT-SWAP",
           "WLD-USDT-SWAP", "SAND-USDT-SWAP")


def _of(c, k):
    return float(c[k])


def _spike(candles, i, n_avg, mult):
    ranges = [(_of(c, "high") - _of(c, "low")) / max(1e-12, _of(c, "open")) for c in candles]
    avg = sma(ranges, i, n_avg)
    if avg is None or ranges[i] <= avg * mult:
        return None
    return "short" if _of(candles[i], "close") > _of(candles[i], "open") else "long"


# ── candlestick confirmations (on the spike bar i; pure, no look-ahead) ──
def _pin_bar(candles, i, side):
    c = candles[i]
    o, h, lo, cl = _of(c, "open"), _of(c, "high"), _of(c, "low"), _of(c, "close")
    body = abs(cl - o) or 1e-9
    up_wick, dn_wick = h - max(o, cl), min(o, cl) - lo
    return up_wick > body * 1.5 if side == "short" else dn_wick > body * 1.5


def _engulfing(candles, i, side):
    if i < 1:
        return False
    o, cl = _of(candles[i], "open"), _of(candles[i], "close")
    po, pc = _of(candles[i - 1], "open"), _of(candles[i - 1], "close")
    if side == "short":                       # bearish engulfing: down bar engulfs prior up body
        return cl < o and pc > po and cl <= po and o >= pc
    return cl > o and pc < po and cl >= po and o <= pc   # bullish engulfing


def _doji(candles, i, _side):
    c = candles[i]
    rng = _of(c, "high") - _of(c, "low")
    return rng > 0 and abs(_of(c, "close") - _of(c, "open")) <= rng * 0.1


def _rsi_extreme(candles, i, side):
    closes = [_of(c, "close") for c in candles]
    r = rsi_at(closes, i, 14)
    if r is None:
        return False
    return r >= 70 if side == "short" else r <= 30


def _bb_break(candles, i, side):
    closes = [_of(c, "close") for c in candles]
    if i < 20:
        return False
    window = closes[i - 20:i]
    m = sum(window) / 20
    sd = (sum((x - m) ** 2 for x in window) / 20) ** 0.5
    cl = closes[i]
    return cl > m + 2 * sd if side == "short" else cl < m - 2 * sd


CONFIRMATIONS = {"none": lambda *_: True, "pin_bar": _pin_bar, "engulfing": _engulfing,
                 "doji": _doji, "rsi_extreme": _rsi_extreme, "bb_break": _bb_break}


def _exit_gross(candles, idx, side, *, tp, sl, max_hold, first_green) -> float | None:
    entry = _of(candles[idx], "open")
    if entry <= 0:
        return None
    long_ = side == "long"
    cap = min(idx + max_hold, len(candles) - 1)
    stop = entry * (1 - sl / 100) if long_ else entry * (1 + sl / 100)
    take = entry * (1 + tp / 100) if long_ else entry * (1 - tp / 100)
    exit_price = _of(candles[cap], "close")
    for j in range(idx, cap + 1):
        hi, lo, cl = _of(candles[j], "high"), _of(candles[j], "low"), _of(candles[j], "close")
        if (long_ and lo <= stop) or (not long_ and hi >= stop):
            exit_price = stop
            break
        if (long_ and hi >= take) or (not long_ and lo <= take):
            exit_price = take
            break
        if first_green and ((long_ and cl > entry) or (not long_ and cl < entry)):
            exit_price = cl
            break
    return (exit_price / entry - 1) * 100 * (1 if long_ else -1)


def run(private_root: Path, *, timeframes=("5m", "15m"), symbols=SYMBOLS, provider=None,
        first_green=True) -> dict[str, Any]:
    if provider is None:
        from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
        provider = OkxPublicMarketDataProvider()
    now = int(time.time() * 1000)
    acc: dict[str, dict[str, Any]] = {}
    for tf in timeframes:
        bars_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000}.get(tf, 300_000)
        start = now - 1900 * bars_ms
        tp = {"5m": 1.0, "15m": 1.6}.get(tf, 1.0)
        sl = {"5m": 0.8, "15m": 1.2}.get(tf, 0.8)
        max_hold = HORIZON_BARS.get(tf, 12)
        for sym in symbols:
            s = sym.replace("-", "_")
            try:
                candles = provider.fetch_ohlcv(s, tf, start, now)
            except Exception:  # noqa: BLE001
                continue
            if len(candles) < 200:
                continue
            cut = int(len(candles) * (1.0 - OOS_FRAC))
            for i in range(20, len(candles) - 1):
                side = _spike(candles, i, 20, RANGE_MULT)
                if not side or i < cut:
                    continue
                g = _exit_gross(candles, i + 1, side, tp=tp, sl=sl, max_hold=max_hold, first_green=first_green)
                if g is None:
                    continue
                for cname, cfn in CONFIRMATIONS.items():
                    if not cfn(candles, i, side):
                        continue
                    key = f"{cname}::{tf}"
                    cell = acc.setdefault(key, {"confirm": cname, "tf": tf, "syms": set(), "oos": []})
                    cell["syms"].add(sym)
                    cell["oos"].append(g)
    return {"symbols": len(symbols), "max_hold_bars": HORIZON_BARS, "summary": _summarize(acc)}


def _summarize(acc: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = []
    for cell in acc.values():
        g = cell["oos"]
        if len(g) < MIN_OOS_TRADES or len(cell["syms"]) < MIN_SYMBOLS:
            continue
        gm = mean(g)
        out.append({"confirm": cell["confirm"], "tf": cell["tf"], "symbols": len(cell["syms"]),
                    "oos_trades": len(g), "gross": round(gm, 4), "net_taker": round(gm - TAKER_RT, 4),
                    "net_tight": round(gm - TIGHT_RT, 4), "win": round(sum(1 for x in g if x > 0) / len(g), 3)})
    ranked = sorted(out, key=lambda c: -c["net_taker"])
    base = {(c["tf"]): c for c in out if c["confirm"] == "none"}
    for c in ranked:                       # lift vs the unconfirmed fade on the same TF
        b = base.get(c["tf"])
        c["lift_net_vs_none"] = round(c["net_taker"] - b["net_taker"], 4) if b else None
        c["lift_win_vs_none"] = round(c["win"] - b["win"], 3) if b else None
    return {"ranked": ranked,
            "note": "confirmed fade vs raw fade (lift_*_vs_none), per-TF holding horizon as the time-stop. "
                    "A confirmation earns its keep only if lift is positive on a broad base. Nothing paper-ready"}


def write_snapshot(private_root: Path, report: dict[str, Any]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "confirmed_fade_search.json"
    payload = {"schema": "confirmed_fade_search.v1",
               "disclaimer": "Spike-fade + candlestick/TA confirmation, per-TF horizon time-stop, OOS, "
                             "two costs. Research-only; nothing edge or paper-ready.", **report}
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
    ap = argparse.ArgumentParser(description="Confirmed-fade search: candlestick/TA + horizon (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    report = run(Path(args.private_root))
    print(f"holding horizons (bars): {report['max_hold_bars']}")
    print(f"  {'confirm':14s}{'tf':4s}{'sym':4s}{'trd':6s}{'gross':>8s}{'net_tk':>8s}{'win':>6s}{'lift_net':>9s}{'lift_win':>9s}")
    for c in report["summary"]["ranked"]:
        ln = "" if c["lift_net_vs_none"] is None else f"{c['lift_net_vs_none']:+.3f}"
        lw = "" if c["lift_win_vs_none"] is None else f"{c['lift_win_vs_none']:+.3f}"
        print(f"  {c['confirm']:14s}{c['tf']:4s}{c['symbols']:<4d}{c['oos_trades']:<6d}{c['gross']:+8.3f}"
              f"{c['net_taker']:+8.3f}{c['win']:>6.2f}{ln:>9s}{lw:>9s}")
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
