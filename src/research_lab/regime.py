# -*- coding: utf-8 -*-
"""Simple regime labeling for lab runs.

Regime labels are metadata and gating only — they are deliberately crude and
carry no profitability claim. Default thresholds are tuned for 1D crypto-perp
candles; specs may override them via the optional "regime_params" block.
"""

from __future__ import annotations

from typing import Any

Candle = dict[str, Any]

DEFAULTS = {
    "lookback": 20,
    "vol_low_pct": 4.0,       # avg bar range below this -> low volatility
    "vol_high_pct": 9.0,      # avg bar range above this -> high volatility
    "trend_band_pct": 2.0,    # close within +-band of MA -> sideways
    "volume_window": 5,
    "volume_thin_ratio": 0.5,
    "volume_elevated_ratio": 1.5,
}

DIMENSIONS = ("volatility", "trend", "volume")


def regime_at(candles: list[Candle], idx: int, params: dict[str, Any] | None = None) -> dict[str, str]:
    """Label the regime using bars strictly before `idx` (no lookahead)."""
    p = {**DEFAULTS, **(params or {})}
    lookback = int(p["lookback"])
    if idx < lookback:
        return {"volatility": "unknown", "trend": "unknown", "volume": "unknown"}
    window = candles[idx - lookback:idx]
    return {
        "volatility": _volatility_bucket(window, p),
        "trend": _trend_bucket(window, p),
        "volume": _volume_bucket(window, p),
    }


def _volatility_bucket(window: list[Candle], p: dict[str, Any]) -> str:
    ranges = []
    for c in window:
        close = float(c["close"])
        if close <= 0:
            continue
        ranges.append((float(c["high"]) - float(c["low"])) / close * 100)
    if not ranges:
        return "unknown"
    avg_range = sum(ranges) / len(ranges)
    if avg_range < float(p["vol_low_pct"]):
        return "low"
    if avg_range > float(p["vol_high_pct"]):
        return "high"
    return "medium"


def _trend_bucket(window: list[Candle], p: dict[str, Any]) -> str:
    closes = [float(c["close"]) for c in window]
    ma = sum(closes) / len(closes)
    if ma <= 0:
        return "unknown"
    last = closes[-1]
    band = float(p["trend_band_pct"]) / 100
    if last > ma * (1 + band):
        return "up"
    if last < ma * (1 - band):
        return "down"
    return "sideways"


def _volume_bucket(window: list[Candle], p: dict[str, Any]) -> str:
    vols = [float(c.get("vol") or 0.0) for c in window]
    base = sum(vols) / len(vols)
    if base <= 0:
        return "unknown"
    recent_n = min(int(p["volume_window"]), len(vols))
    recent = sum(vols[-recent_n:]) / recent_n
    ratio = recent / base
    if ratio < float(p["volume_thin_ratio"]):
        return "thin"
    if ratio > float(p["volume_elevated_ratio"]):
        return "elevated"
    return "normal"


def regime_matches(regime: dict[str, str], filters: dict[str, list[str]]) -> bool:
    """True when every requested dimension matches; empty filters match all."""
    for dim, allowed in (filters or {}).items():
        if not allowed:
            continue
        if regime.get(dim, "unknown") not in allowed:
            return False
    return True


def regime_breakdown(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate trade outcomes per regime bucket key 'vol|trend|volume'."""
    buckets: dict[str, dict[str, Any]] = {}
    for trade in trades:
        regime = trade.get("regime") or {}
        key = "|".join(str(regime.get(dim, "unknown")) for dim in DIMENSIONS)
        bucket = buckets.setdefault(key, {"n_trades": 0, "sum_net_pct": 0.0, "wins": 0})
        bucket["n_trades"] += 1
        net = float(trade.get("net_pct") or 0.0)
        bucket["sum_net_pct"] += net
        if net > 0:
            bucket["wins"] += 1
    for bucket in buckets.values():
        n = bucket["n_trades"]
        bucket["avg_net_pct"] = round(bucket["sum_net_pct"] / n, 4) if n else 0.0
        bucket["sum_net_pct"] = round(bucket["sum_net_pct"], 4)
        bucket["win_rate"] = round(bucket["wins"] / n, 4) if n else 0.0
    return buckets
