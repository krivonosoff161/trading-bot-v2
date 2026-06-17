# -*- coding: utf-8 -*-
"""Microstructure features: order-book imbalance, spread, trade delta.

Order-book and trade-tape state is point-in-time and is NOT reconstructable from
historical OHLCV candles, so these features only return a value when the farm
merged a microstructure snapshot into the candle (optional fields ``obi_top5``,
``spread_bps``, ``trade_delta_100``). Absent fields -> ``None`` so the
microstructure-confirmed family reports NEEDS_DATA instead of inventing a value.
No private endpoints: only public books/trades, and only when already captured.
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


def obi_top5_at(candles: list[Candle], idx: int) -> float | None:
    """Order-book imbalance of the top 5 levels (-1 sell-heavy .. +1 buy-heavy)."""
    return _val(candles[idx], "obi_top5")


def spread_bps_at(candles: list[Candle], idx: int) -> float | None:
    """Best bid/ask spread in basis points."""
    return _val(candles[idx], "spread_bps")


def trade_delta_at(candles: list[Candle], idx: int) -> float | None:
    """Signed taker volume delta over the last ~100 trades (buy - sell)."""
    return _val(candles[idx], "trade_delta_100")


def has_microstructure(candles: list[Candle], idx: int) -> bool:
    """True when at least one microstructure field is present on bar ``idx``."""
    return any(
        _val(candles[idx], key) is not None
        for key in ("obi_top5", "spread_bps", "trade_delta_100")
    )
