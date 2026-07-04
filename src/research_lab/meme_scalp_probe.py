# -*- coding: utf-8 -*-
"""Meme/alt 1m-5m scalp probe — a fresh creative attempt at high-frequency balance acceleration.

High-frequency scalping is the most cost-bound regime (proven earlier). But high-liquidity memes have
tight spreads and violent 1m-5m moves, so the honest question is: does any scalp signal capture a move
bigger than the round-trip cost, often enough, OUT OF SAMPLE, on a broad symbol base?

Three authored scalp hypotheses (no look-ahead, entry at idx+1):
  * burst_momentum   — K consecutive same-direction bars + a volume surge -> ride the burst
  * micro_breakout   — break of the recent N-bar high/low -> ride the break
  * vol_expansion_fade — an oversized bar (range >> average) -> fade the spike (overshoot reverts)

Each is evaluated with a tight scalp exit, pooled across memes, split in/out-of-sample, and reported at
TWO cost levels (taker 0.10% round-trip and a tight 0.04%) so the cost dependence is explicit. A cell is
a candidate only on a broad symbol base (mirage guard). Keyless public candles; nothing edge or
paper-ready; no order/money/live path.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import mean
from typing import Any

from src.research_lab.strategies._helpers import sma, vols

TAKER_RT = 0.10        # taker round-trip, percent points
TIGHT_RT = 0.04        # tight/maker-ish round-trip
OOS_FRAC = 0.35
MIN_OOS_TRADES = 40
MIN_SYMBOLS = 5
MEMES = ("PEPE-USDT-SWAP", "BONK-USDT-SWAP", "WIF-USDT-SWAP", "FLOKI-USDT-SWAP", "SHIB-USDT-SWAP",
         "DOGE-USDT-SWAP", "PUMP-USDT-SWAP", "PENGU-USDT-SWAP", "MEW-USDT-SWAP", "NEIRO-USDT-SWAP",
         "BOME-USDT-SWAP", "TRUMP-USDT-SWAP")


def _bull(c: dict[str, Any]) -> bool:
    return float(c["close"]) > float(c["open"])


def burst_momentum(candles: list[dict[str, Any]], k: int, vol_mult: float) -> list[dict[str, Any]]:
    vseries = vols(candles)
    out: list[dict[str, Any]] = []
    for i in range(max(k, 20), len(candles) - 1):
        avg_vol = sma(vseries, i, 20)
        if avg_vol is None or vseries[i] <= avg_vol * vol_mult:
            continue
        if all(_bull(candles[i - j]) for j in range(k)):
            out.append({"idx": i + 1, "side": "long"})
        elif all(not _bull(candles[i - j]) for j in range(k)):
            out.append({"idx": i + 1, "side": "short"})
    return out


def micro_breakout(candles: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(n, len(candles) - 1):
        hi = max(float(candles[j]["high"]) for j in range(i - n, i))
        lo = min(float(candles[j]["low"]) for j in range(i - n, i))
        close = float(candles[i]["close"])
        if close > hi:
            out.append({"idx": i + 1, "side": "long"})
        elif close < lo:
            out.append({"idx": i + 1, "side": "short"})
    return out


def vol_expansion_fade(candles: list[dict[str, Any]], range_mult: float) -> list[dict[str, Any]]:
    ranges = [(float(c["high"]) - float(c["low"])) / max(1e-12, float(c["open"])) for c in candles]
    out: list[dict[str, Any]] = []
    for i in range(20, len(candles) - 1):
        avg_r = sma(ranges, i, 20)
        if avg_r is None or ranges[i] <= avg_r * range_mult:
            continue
        out.append({"idx": i + 1, "side": "short" if _bull(candles[i]) else "long"})  # fade the spike
    return out


def _gross_returns(candles: list[dict[str, Any]], sigs: list[dict[str, Any]], *, hold: int,
                   stop_pct: float, take_pct: float) -> list[tuple[int, float]]:
    """(entry_idx, gross_return_pct) under a tight scalp exit. No look-ahead beyond the decided hold."""
    out: list[tuple[int, float]] = []
    for s in sigs:
        idx = int(s["idx"])
        cap = min(idx + hold, len(candles) - 1)
        if idx >= len(candles) or cap <= idx:
            continue
        entry = float(candles[idx]["open"])
        if entry <= 0:
            continue
        long_ = s["side"] == "long"
        stop = entry * (1 - stop_pct / 100) if long_ else entry * (1 + stop_pct / 100)
        take = entry * (1 + take_pct / 100) if long_ else entry * (1 - take_pct / 100)
        exit_price = float(candles[cap]["close"])
        for j in range(idx, cap + 1):
            hi, lo = float(candles[j]["high"]), float(candles[j]["low"])
            if (long_ and lo <= stop) or (not long_ and hi >= stop):
                exit_price = stop
                break
            if (long_ and hi >= take) or (not long_ and lo <= take):
                exit_price = take
                break
        ret = (exit_price / entry - 1) * 100 * (1 if long_ else -1)
        out.append((idx, ret))
    return out


def _hypotheses(tf: str) -> list[dict[str, Any]]:
    # tighter barriers on 1m than 5m (realistic scalp horizons)
    if tf == "1m":
        exit_p = {"hold": 3, "stop_pct": 0.4, "take_pct": 0.8}
    else:
        exit_p = {"hold": 3, "stop_pct": 0.8, "take_pct": 1.6}
    return [
        {"label": "burst_momentum|k3", "fn": lambda c: burst_momentum(c, 3, 1.8), **exit_p},
        {"label": "micro_breakout|n20", "fn": lambda c: micro_breakout(c, 20), **exit_p},
        # vol_expansion_fade across thresholds: the edge should STRENGTHEN with the threshold (bigger
        # overshoots revert more), not be a knife-edge at one value -> a built-in robustness check.
        {"label": "vol_expansion_fade|x2.5", "fn": lambda c: vol_expansion_fade(c, 2.5), **exit_p},
        {"label": "vol_expansion_fade|x3", "fn": lambda c: vol_expansion_fade(c, 3.0), **exit_p},
        {"label": "vol_expansion_fade|x4", "fn": lambda c: vol_expansion_fade(c, 4.0), **exit_p},
    ]


def run(private_root: Path, *, timeframes: tuple[str, ...] = ("1m", "5m"),
        symbols: tuple[str, ...] = MEMES, provider: Any = None) -> dict[str, Any]:
    if provider is None:
        from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
        provider = OkxPublicMarketDataProvider()
    now = int(time.time() * 1000)
    acc: dict[str, dict[str, Any]] = {}
    for tf in timeframes:
        bars_ms = {"1m": 60_000, "5m": 300_000}.get(tf, 60_000)
        start = now - 1900 * bars_ms          # ~1900 bars (under the 2000 cap)
        for sym in symbols:
            s = sym.replace("-", "_")
            try:
                candles = provider.fetch_ohlcv(s, tf, start, now)
            except Exception:  # noqa: BLE001 - network/parse must not crash the probe
                continue
            if len(candles) < 200:
                continue
            cut = int(len(candles) * (1.0 - OOS_FRAC))
            for h in _hypotheses(tf):
                rets = _gross_returns(candles, h["fn"](candles), hold=h["hold"],
                                      stop_pct=h["stop_pct"], take_pct=h["take_pct"])
                oos = [r for idx, r in rets if idx >= cut]
                if len(oos) < 5:
                    continue
                key = f"{h['label']}::{tf}"
                cell = acc.setdefault(key, {"label": h["label"], "timeframe": tf, "symbols": 0, "oos_gross": []})
                cell["symbols"] += 1
                cell["oos_gross"].extend(oos)
    return {"symbols_tested": len(symbols), "summary": _summarize(acc)}


def _summarize(acc: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    for cell in acc.values():
        g = cell["oos_gross"]
        if len(g) < MIN_OOS_TRADES or cell["symbols"] < MIN_SYMBOLS:
            continue
        gross = mean(g)
        net_taker = gross - TAKER_RT
        net_tight = gross - TIGHT_RT
        out.append({
            "label": cell["label"], "timeframe": cell["timeframe"], "symbols": cell["symbols"],
            "oos_trades": len(g), "gross_mean_pct": round(gross, 4),
            "net_taker_pct": round(net_taker, 4), "net_tight_pct": round(net_tight, 4),
            "win_gross": round(sum(1 for x in g if x > 0) / len(g), 3),
            "verdict": _verdict(net_taker, net_tight),
        })
    ranked = sorted(out, key=lambda c: -c["net_taker_pct"])
    return {"by_cell": ranked,
            "any_taker_positive": any(c["net_taker_pct"] > 0 for c in ranked),
            "any_tight_positive": any(c["net_tight_pct"] > 0 for c in ranked),
            "note": "1m/5m meme scalp, pooled OOS, two cost levels. A 1m gross move must beat the round-"
                    "trip; taker-positive on a broad base is the only real candidate. Nothing paper-ready"}


def _verdict(net_taker: float, net_tight: float) -> str:
    if net_taker > 0.005:
        return "beats_taker_candidate"
    if net_tight > 0.005:
        return "needs_tight_execution"      # only works if execution cost is near-maker
    return "cost_bound_dead"


def write_snapshot(private_root: Path, report: dict[str, Any]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "meme_scalp_probe.json"
    payload = {"schema": "meme_scalp_probe.v1",
               "disclaimer": "1m/5m meme scalp on keyless public candles, OOS, two cost levels. "
                             "Research-only; cost-bound HF regime; nothing edge or paper-ready.",
               **report}
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
    ap = argparse.ArgumentParser(description="Meme/alt 1m-5m scalp probe (keyless, research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    report = run(Path(args.private_root))
    s = report["summary"]
    print(f"taker_positive_any={s['any_taker_positive']} tight_positive_any={s['any_tight_positive']}")
    print(f"{'hypothesis':24s}{'tf':4s}{'sym':4s}{'trades':7s}{'gross':>8s}{'net_taker':>10s}{'net_tight':>10s}{'win':>6s}  verdict")
    for c in s["by_cell"]:
        print(f"  {c['label']:22s}{c['timeframe']:4s}{c['symbols']:<4d}{c['oos_trades']:<7d}{c['gross_mean_pct']:+8.3f}"
              f"{c['net_taker_pct']:+10.3f}{c['net_tight_pct']:+10.3f}{c['win_gross']:>6.2f}  {c['verdict']}")
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
