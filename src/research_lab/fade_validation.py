# -*- coding: utf-8 -*-
"""Fade-lead validation — the decisive gates the day's report flagged as missing (research-only).

The meme spike-fade looked like the strongest lead (+0.11 net at flat taker cost, one held-out split).
Before believing it, close the gaps honestly, with comparisons:

  1. SLIPPAGE sensitivity  — you fade a VIOLENT bar, so the fill is worse than a flat taker cost. Model
     slippage proportional to the spike size and find the break-even slip multiplier.
  2. WALK-FORWARD          — net per time-block (K rolling windows), not one tail split, to catch
     regime/overfit.
  3. LOSS TAIL / DRAWDOWN  — an 88%-win profile can hide fat losers; report avg win/loss, worst loss, and
     the equity-curve max drawdown.
  4. SIGNIFICANCE + deflation — t-stat of the per-trade net, with a Sidak deflation by the number of
     grid cells tested, so a cell that is positive by chance is not trusted.

Pooled across a broad meme sample, OOS only. Keyless public candles; nothing edge or paper-ready.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from src.research_lab.strategies._helpers import sma

FEES_RT = 0.06            # 2 taker legs at ~3bps (high-liquidity memes); slippage is added on top
OOS_FRAC = 0.35
N_BLOCKS = 5             # walk-forward time-blocks over the OOS region
GRID_CELLS_TESTED = 50   # ~ the size of the search that produced the lead (for Sidak deflation)
SYMBOLS = ("PEPE-USDT-SWAP", "BONK-USDT-SWAP", "WIF-USDT-SWAP", "FLOKI-USDT-SWAP", "SHIB-USDT-SWAP",
           "DOGE-USDT-SWAP", "PUMP-USDT-SWAP", "PENGU-USDT-SWAP", "MEW-USDT-SWAP", "TRUMP-USDT-SWAP",
           "WLD-USDT-SWAP", "SAND-USDT-SWAP")
SPECS = (
    {"label": "range_fade x3 | tp_sl", "tf": "5m", "trigger": "range", "mult": 3.0,
     "exit": "tp_sl", "tp": 1.0, "sl": 0.8, "max_hold": 6},
    {"label": "nbar_fade n4 | first_green", "tf": "15m", "trigger": "nbar", "n": 4,
     "exit": "first_green", "tp": 1.6, "sl": 1.2, "max_hold": 28},
)


def _bull(c):
    return float(c["close"]) > float(c["open"])


def _range_pct(c):
    return (float(c["high"]) - float(c["low"])) / max(1e-12, float(c["open"])) * 100


def _entries(candles, spec):
    out = []
    if spec["trigger"] == "range":
        ranges = [_range_pct(c) / 100 for c in candles]
        for i in range(20, len(candles) - 1):
            avg = sma(ranges, i, 20)
            if avg and ranges[i] > avg * spec["mult"]:
                out.append((i, "short" if _bull(candles[i]) else "long"))
    else:  # nbar exhaustion
        n = spec["n"]
        for i in range(n, len(candles) - 1):
            if all(_bull(candles[i - j]) for j in range(n)):
                out.append((i, "short"))
            elif all(not _bull(candles[i - j]) for j in range(n)):
                out.append((i, "long"))
    return out


def _trade(candles, idx, side, spec):
    """Return (gross_pct, spike_range_pct) for a fade entered at idx (the bar after the trigger)."""
    entry = float(candles[idx]["open"])
    if entry <= 0:
        return None
    long_ = side == "long"
    cap = min(idx + spec["max_hold"], len(candles) - 1)
    stop = entry * (1 - spec["sl"] / 100) if long_ else entry * (1 + spec["sl"] / 100)
    take = entry * (1 + spec["tp"] / 100) if long_ else entry * (1 - spec["tp"] / 100)
    exit_price = float(candles[cap]["close"])
    for j in range(idx, cap + 1):
        hi, lo, cl = float(candles[j]["high"]), float(candles[j]["low"]), float(candles[j]["close"])
        if (long_ and lo <= stop) or (not long_ and hi >= stop):
            exit_price = stop
            break
        if (long_ and hi >= take) or (not long_ and lo <= take):
            exit_price = take
            break
        if spec["exit"] == "first_green" and ((long_ and cl > entry) or (not long_ and cl < entry)):
            exit_price = cl
            break
    gross = (exit_price / entry - 1) * 100 * (1 if long_ else -1)
    return gross, _range_pct(candles[idx - 1])   # the trigger (spike) bar's range drives slippage


def collect(private_root, spec, *, provider, symbols=SYMBOLS):
    now = int(time.time() * 1000)
    bars_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000}[spec["tf"]]
    start = now - 1900 * bars_ms
    rows = []
    for sym in symbols:
        s = sym.replace("-", "_")
        try:
            candles = provider.fetch_ohlcv(s, spec["tf"], start, now)
        except Exception:  # noqa: BLE001
            continue
        if len(candles) < 200:
            continue
        cut = int(len(candles) * (1.0 - OOS_FRAC))
        oos_len = len(candles) - cut
        for idx, side in _entries(candles, spec):
            ent = idx + 1
            if ent < cut or ent >= len(candles):
                continue
            t = _trade(candles, ent, side, spec)
            if t:
                block = min(N_BLOCKS - 1, int((ent - cut) / max(1, oos_len) * N_BLOCKS))
                rows.append({"gross": t[0], "spike_range": t[1], "block": block})
    return rows


def _net(rows, slip_mult):
    """net per trade = gross - fees - slip, slip = slip_mult * spike_range (worse fill on bigger spikes)."""
    return [r["gross"] - FEES_RT - slip_mult * r["spike_range"] for r in rows]


def analyze(rows) -> dict[str, Any]:
    if len(rows) < 30:
        return {"trades": len(rows), "verdict": "underpowered"}
    # 1. slippage sensitivity + break-even
    slip_curve = {}
    breakeven = None
    for sm in (0.0, 0.01, 0.02, 0.03, 0.05, 0.08):
        nets = _net(rows, sm)
        m = mean(nets)
        slip_curve[sm] = round(m, 4)
        if breakeven is None and m <= 0:
            breakeven = sm
    # 2. walk-forward at a realistic slip (0.02)
    real = _net(rows, 0.02)
    by_block = {}
    for r, n in zip(rows, real):
        by_block.setdefault(r["block"], []).append(n)
    wf = {b: round(mean(v), 4) for b, v in sorted(by_block.items())}
    wf_positive = sum(1 for v in wf.values() if v > 0)
    # 3. loss tail / drawdown at realistic slip
    wins = [x for x in real if x > 0]
    losses = [x for x in real if x <= 0]
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for x in real:
        equity += x
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    # 4. significance (t-stat) + Sidak deflation across the grid
    mu, sd, n = mean(real), pstdev(real) or 1e-9, len(real)
    t = mu / (sd / (n ** 0.5))
    # rough two-sided p from t via normal approx, then Sidak over the grid
    import math
    p_raw = math.erfc(abs(t) / (2 ** 0.5))
    p_adj = 1 - (1 - p_raw) ** GRID_CELLS_TESTED
    return {
        "trades": len(rows),
        "slippage_curve_net": slip_curve, "breakeven_slip_mult": breakeven,
        "walk_forward_by_block": wf, "wf_blocks_positive": f"{wf_positive}/{len(wf)}",
        "realistic_net_mean": round(mu, 4), "win_rate": round(len(wins) / n, 3),
        "avg_win": round(mean(wins), 3) if wins else 0.0,
        "avg_loss": round(mean(losses), 3) if losses else 0.0,
        "worst_loss": round(min(real), 3), "max_drawdown_pct_sum": round(max_dd, 3),
        "t_stat": round(t, 2), "p_raw": round(p_raw, 4), "p_sidak_deflated": round(p_adj, 4),
        "verdict": _verdict(slip_curve, wf_positive, len(wf), p_adj, breakeven),
    }


def _verdict(slip_curve, wf_pos, wf_n, p_adj, breakeven) -> str:
    real_net = slip_curve.get(0.02, 0.0)
    if real_net <= 0:
        return "dies_under_realistic_slippage"
    if wf_pos < (wf_n + 1) // 2:
        return "not_time_robust"
    if p_adj >= 0.05:
        return "not_significant_after_deflation"
    return "survives_all_gates_paper_forward_candidate"


def run(private_root, *, provider=None):
    if provider is None:
        from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
        provider = OkxPublicMarketDataProvider()
    out = {}
    for spec in SPECS:
        out[spec["label"] + " :: " + spec["tf"]] = analyze(collect(Path(private_root), spec, provider=provider))
    return {"specs": out, "note": "slippage/walk-forward/loss-tail/deflation gates on the fade lead. "
                                   "Research-only; survives_all_gates != edge, only a paper-forward candidate"}


def write_snapshot(private_root, report) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "fade_validation.json"
    path.write_text(json.dumps({"schema": "fade_validation.v1", **report}, ensure_ascii=False,
                               indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> None:
    import argparse
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="Fade-lead validation: slippage/walk-forward/tail/deflation.")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    report = run(Path(args.private_root))
    for label, a in report["specs"].items():
        print(f"\n=== {label} ===")
        print(json.dumps(a, ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
