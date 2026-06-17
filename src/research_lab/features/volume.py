# -*- coding: utf-8 -*-
"""Volume features: vol_ratio, spike, decay, sideways-range accumulation.

As-of the close of bar ``idx`` using candles ``0..idx``. None during warmup.
The baseline window is strictly before ``idx`` so a bar never dilutes its own
ratio (matches the old screener's vol_sma on vols[:-trigger]).
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import _core

Candle = dict[str, Any]


def _baseline(vol_list: list[float], idx: int, period: int) -> float | None:
    if idx - period < 0 or period <= 0:
        return None
    window = vol_list[idx - period:idx]
    base = sum(window) / len(window) if window else 0.0
    return base if base > 0 else None


def vol_ratio_at(candles: list[Candle], idx: int, period: int = 20) -> float | None:
    """Current bar volume / mean volume of the prior ``period`` bars."""
    v = _core.vols(candles)
    base = _baseline(v, idx, period)
    if base is None:
        return None
    return round(v[idx] / base, 4)


def volume_spike_at(candles: list[Candle], idx: int, period: int = 20, mult: float = 2.0) -> bool:
    ratio = vol_ratio_at(candles, idx, period)
    return ratio is not None and ratio >= mult


def volume_decay_at(candles: list[Candle], idx: int, recent: int = 3, prior: int = 6) -> float | None:
    """Recent avg volume / prior avg volume (<1 = fading; pullback context)."""
    v = _core.vols(candles)
    if idx - (recent + prior) < 0 or recent <= 0 or prior <= 0:
        return None
    recent_window = v[idx - recent + 1:idx + 1]
    prior_window = v[idx - recent - prior + 1:idx - recent + 1]
    avg_recent = sum(recent_window) / len(recent_window) if recent_window else 0.0
    avg_prior = sum(prior_window) / len(prior_window) if prior_window else 0.0
    if avg_prior <= 0:
        return None
    return round(avg_recent / avg_prior, 4)


def sideways_range_volume_at(
    candles: list[Candle], idx: int, lookback: int = 20, max_range_pct: float = 12.0
) -> dict[str, Any] | None:
    """Volume accumulated inside a tight (sideways) range over the last ``lookback`` bars.

    Returns {is_sideways, range_pct, accumulation_ratio} where accumulation_ratio is
    the total volume in the window vs the prior-window baseline volume. A high ratio
    inside a tight range = accumulation before a possible breakout.
    """
    if idx - 2 * lookback < 0 or lookback <= 0:
        return None
    h, low_arr, v = _core.highs(candles), _core.lows(candles), _core.vols(candles)
    win_high = max(h[idx - lookback:idx])
    win_low = min(low_arr[idx - lookback:idx])
    if win_low <= 0:
        return None
    range_pct = round((win_high / win_low - 1) * 100, 4)
    window_vol = sum(v[idx - lookback:idx])
    prior_vol = sum(v[idx - 2 * lookback:idx - lookback])
    accumulation = round(window_vol / prior_vol, 4) if prior_vol > 0 else None
    return {
        "is_sideways": range_pct <= max_range_pct,
        "range_pct": range_pct,
        "accumulation_ratio": accumulation,
    }
