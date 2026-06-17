# -*- coding: utf-8 -*-
"""Flow features: open interest, funding, perp/index divergence.

These read OPTIONAL per-candle fields that the farm merges in when OKX public
OI/funding data was prepared alongside candles:
    ``oi``        open interest (contracts or USD, consistent within a series)
    ``funding``   funding rate (fraction, e.g. 0.0001 = 0.01%)
    ``index_px``  index price (for perp/index basis)
When a field is absent the feature returns ``None`` — flow is context, never a
standalone trading truth, and must degrade gracefully on assets without it.
As-of the close of bar ``idx`` using candles ``0..idx``.
"""

from __future__ import annotations

from typing import Any

Candle = dict[str, Any]


def _val(candle: Candle, key: str) -> float | None:
    raw = candle.get(key)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def oi_delta_at(candles: list[Candle], idx: int, lookback: int = 5) -> float | None:
    """Percent change in open interest over ``lookback`` bars."""
    if idx - lookback < 0:
        return None
    now = _val(candles[idx], "oi")
    prev = _val(candles[idx - lookback], "oi")
    if now is None or prev is None or prev <= 0:
        return None
    return round((now / prev - 1) * 100, 4)


def oi_price_quadrant_at(candles: list[Candle], idx: int, lookback: int = 5) -> str | None:
    """dOI x dPrice quadrant (continuation vs trap hypothesis from old research).

    new_longs        : OI up,   price up   -> fresh longs, continuation-up bias
    new_shorts       : OI up,   price down -> fresh shorts, continuation-down bias
    short_covering   : OI down, price up   -> covering rally, weak/trap-up bias
    long_liquidation : OI down, price down -> long flush, capitulation-down bias
    """
    d_oi = oi_delta_at(candles, idx, lookback)
    if d_oi is None or idx - lookback < 0:
        return None
    p_now = float(candles[idx]["close"])
    p_prev = float(candles[idx - lookback]["close"])
    if p_prev <= 0:
        return None
    d_price = (p_now / p_prev - 1) * 100
    if d_oi >= 0 and d_price >= 0:
        return "new_longs"
    if d_oi >= 0 and d_price < 0:
        return "new_shorts"
    if d_oi < 0 and d_price >= 0:
        return "short_covering"
    return "long_liquidation"


def funding_at(candles: list[Candle], idx: int) -> float | None:
    return _val(candles[idx], "funding")


def funding_zscore_at(candles: list[Candle], idx: int, window: int = 48) -> float | None:
    """Funding z-score vs the trailing ``window`` (funding persistence/squeeze proxy)."""
    if idx - window + 1 < 0 or window < 2:
        return None
    series = [_val(candles[j], "funding") for j in range(idx - window + 1, idx + 1)]
    vals = [v for v in series if v is not None]
    if len(vals) < window:
        return None
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = var ** 0.5
    if std == 0:
        return 0.0
    return round((vals[-1] - mean) / std, 4)


def funding_extreme_at(
    candles: list[Candle], idx: int, warn: float = 0.0005, block: float = 0.001
) -> str | None:
    """Squeeze-risk label from absolute funding (warn/block thresholds as fraction).

    Positive extreme funding = crowded longs (long-squeeze risk); negative = crowded
    shorts (short-squeeze risk). None when funding is missing or normal.
    """
    f = funding_at(candles, idx)
    if f is None:
        return None
    mag = abs(f)
    if mag < warn:
        return None
    level = "block" if mag >= block else "warn"
    side = "long_squeeze_risk" if f > 0 else "short_squeeze_risk"
    return f"{side}:{level}"


def perp_index_divergence_at(candles: list[Candle], idx: int) -> float | None:
    """Perp/index basis in percent ((close - index_px) / index_px * 100)."""
    index_px = _val(candles[idx], "index_px")
    if index_px is None or index_px <= 0:
        return None
    return round((float(candles[idx]["close"]) / index_px - 1) * 100, 4)
