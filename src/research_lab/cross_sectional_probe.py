# -*- coding: utf-8 -*-
"""Cross-sectional relative-value probe — market-neutral, no direction needed (research-only).

Predicting a single symbol's direction is a coin-flip (proven). Cross-sectional RV needs no market
direction: each period, rank a basket by trailing return, go LONG the top quantile and SHORT the bottom
quantile (equal notional -> market-neutral), hold H periods, and measure the SPREAD return (top - bottom)
net of cost. Tests both signs:
  * momentum  — long recent winners, short recent losers (does strength persist?)
  * reversal  — long recent losers, short recent winners (does it mean-revert?)

Aligned bars across the basket, OOS only, broad sample. If neither sign produces a positive net spread,
cross-sectional RV is dead here too. Keyless public candles; nothing edge or paper-ready.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

FEES_RT = 0.10           # long + short legs, round-trip, taker
OOS_FRAC = 0.35
SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "DOGE-USDT-SWAP", "XRP-USDT-SWAP",
           "BNB-USDT-SWAP", "PEPE-USDT-SWAP", "WIF-USDT-SWAP", "BONK-USDT-SWAP", "FLOKI-USDT-SWAP",
           "SHIB-USDT-SWAP", "AVAX-USDT-SWAP", "LINK-USDT-SWAP", "SAND-USDT-SWAP", "WLD-USDT-SWAP",
           "TRUMP-USDT-SWAP", "ZEC-USDT-SWAP", "AXS-USDT-SWAP")


def _aligned_closes(provider, symbols, tf, start, now) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    for sym in symbols:
        s = sym.replace("-", "_")
        try:
            candles = provider.fetch_ohlcv(s, tf, start, now)
        except Exception:  # noqa: BLE001
            continue
        if len(candles) < 100:
            continue
        out[sym] = {int(c["ts"]): float(c["close"]) for c in candles}
    return out


def _spread_returns(closes: dict[str, dict[int, float]], *, lookback: int, hold: int,
                    quantile: int, direction: str) -> list[tuple[int, float]]:
    """(ts_index, spread_return_pct) for a market-neutral long-top/short-bottom rebalance."""
    ts_sets = [set(v.keys()) for v in closes.values()]
    common = sorted(set.intersection(*ts_sets)) if ts_sets else []
    if len(common) < lookback + hold + 10:
        return []
    out: list[tuple[int, float]] = []
    i = lookback
    while i + hold < len(common):
        t0, tL, tH = common[i], common[i - lookback], common[i + hold]
        rank = []
        for sym, series in closes.items():
            if t0 in series and tL in series and series[tL] > 0:
                rank.append((sym, (series[t0] / series[tL] - 1) * 100))
        if len(rank) >= quantile * 2:
            rank.sort(key=lambda kv: kv[1])
            losers = rank[:quantile]
            winners = rank[-quantile:]
            longs, shorts = (winners, losers) if direction == "momentum" else (losers, winners)

            def _fwd(group):
                vals = [(closes[sym][tH] / closes[sym][t0] - 1) * 100 for sym, _ in group
                        if tH in closes[sym] and closes[sym][t0] > 0]
                return mean(vals) if vals else 0.0
            spread = _fwd(longs) - _fwd(shorts) - FEES_RT
            out.append((i, spread))
            i += hold     # non-overlapping rebalances
        else:
            i += 1
    return out


def run(private_root, *, tf="4h", lookback=6, hold=6, quantile=4, provider=None) -> dict[str, Any]:
    if provider is None:
        from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
        provider = OkxPublicMarketDataProvider()
    now = int(time.time() * 1000)
    bars_ms = {"1h": 3_600_000, "4h": 14_400_000}.get(tf, 14_400_000)
    closes = _aligned_closes(provider, SYMBOLS, tf, now - 1900 * bars_ms, now)
    out = {}
    for direction in ("momentum", "reversal"):
        series = _spread_returns(closes, lookback=lookback, hold=hold, quantile=quantile, direction=direction)
        n_oos = int(len(series) * OOS_FRAC)
        oos = [s for _, s in series[-n_oos:]] if n_oos >= 10 else [s for _, s in series]
        out[direction] = _stats(oos)
    return {"symbols_aligned": len(closes), "tf": tf, "lookback": lookback, "hold": hold,
            "quantile": quantile, "by_direction": out,
            "note": "market-neutral cross-sectional spread, net of round-trip. Research-only; positive "
                    "spread on a broad OOS sample = candidate, nothing paper-ready"}


def _stats(oos: list[float]) -> dict[str, Any]:
    if len(oos) < 10:
        return {"rebalances": len(oos), "verdict": "underpowered"}
    mu, sd = mean(oos), pstdev(oos) or 1e-9
    t = mu / (sd / (len(oos) ** 0.5))
    return {"rebalances": len(oos), "mean_spread_net_pct": round(mu, 4),
            "win_rate": round(sum(1 for x in oos if x > 0) / len(oos), 3),
            "t_stat": round(t, 2),
            "verdict": "positive_spread_candidate" if mu > 0.05 and t > 1.5 else
                       "weak_or_zero" if mu > -0.05 else "negative"}


def write_snapshot(private_root, report) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "cross_sectional_probe.json"
    path.write_text(json.dumps({"schema": "cross_sectional_probe.v1", **report}, ensure_ascii=False,
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
    ap = argparse.ArgumentParser(description="Cross-sectional relative-value probe (market-neutral, research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    report = run(Path(args.private_root))
    print(f"aligned={report['symbols_aligned']} tf={report['tf']} lookback={report['lookback']} hold={report['hold']}")
    for d, s in report["by_direction"].items():
        print(f"  {d:10s} {s}")
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
