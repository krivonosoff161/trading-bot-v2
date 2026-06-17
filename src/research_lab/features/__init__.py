# -*- coding: utf-8 -*-
"""Unified lab feature layer.

One causal, no-lookahead feature surface computed per (asset, timeframe, bar).
Submodules group the families: trend, volatility, volume, structure, vwap, fvg,
flow (OI/funding/basis), microstructure. ``feature_snapshot`` builds a flat dict
of everything available as-of one bar — used for result provenance and the
feature_snapshots store. Missing inputs degrade to ``None`` (never invented).
"""

from __future__ import annotations

from typing import Any

from src.research_lab.features import (
    flow,
    fvg,
    microstructure,
    structure,
    trend,
    volatility,
    volume,
    vwap,
)

Candle = dict[str, Any]

DEFAULT_PARAMS: dict[str, Any] = {
    "ema_fast": 20,
    "ema_slow": 50,
    "slope_period": 5,
    "adx_period": 14,
    "atr_period": 14,
    "atr_history": 50,
    "bb_period": 20,
    "bb_std": 2.0,
    "squeeze_lookback": 20,
    "vol_period": 20,
    "swing_lookback": 3,
    "range_lookback": 20,
    "vwap_period": 20,
    "fvg_lookback": 30,
    "oi_lookback": 5,
    "funding_window": 48,
}

__all__ = [
    "trend", "volatility", "volume", "structure", "vwap", "fvg", "flow",
    "microstructure", "feature_snapshot", "DEFAULT_PARAMS",
]


def feature_snapshot(candles: list[Candle], idx: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Flat dict of every available feature as of the close of bar ``idx``.

    Values are ``None`` where warmup/data is insufficient. This is a research
    snapshot for provenance, not a signal — it makes no directional claim.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    if idx < 0 or idx >= len(candles):
        return {}

    adx = trend.adx_at(candles, idx, p["adx_period"])
    bb = volatility.bollinger_at(candles, idx, p["bb_period"], p["bb_std"])
    swings = structure.swing_levels(candles, idx, p["swing_lookback"])
    brk = structure.breakout_quality_at(candles, idx, p["range_lookback"])
    sideways = volume.sideways_range_volume_at(candles, idx, p["range_lookback"])
    day = vwap.day_position_at(candles, idx)

    snap: dict[str, Any] = {
        # trend
        "ema_fast": trend.ema_at(candles, idx, p["ema_fast"]),
        "ema_slow": trend.ema_at(candles, idx, p["ema_slow"]),
        "ema_fast_slope_deg": trend.slope_deg(candles, idx, p["slope_period"]),
        "adx": adx[0] if adx else None,
        "plus_di": adx[1] if adx else None,
        "minus_di": adx[2] if adx else None,
        "di_spread": (adx[1] - adx[2]) if adx else None,
        "supertrend_dir": trend.supertrend_dir_at(candles, idx, p["atr_period"]),
        # volatility
        "atr": volatility.atr_at(candles, idx, p["atr_period"]),
        "atr_pct": volatility.atr_percentile_at(candles, idx, p["atr_period"], p["atr_history"]),
        "bb_width_pct": bb["width_pct"] if bb else None,
        "bb_pct_b": bb["pct_b"] if bb else None,
        "squeeze_ratio": volatility.squeeze_ratio_at(candles, idx, p["squeeze_lookback"]),
        # volume
        "vol_ratio": volume.vol_ratio_at(candles, idx, p["vol_period"]),
        "volume_decay": volume.volume_decay_at(candles, idx),
        "sideways": sideways,
        # structure
        "swing_highs": swings["recent_highs"],
        "swing_lows": swings["recent_lows"],
        "breakout": brk,
        # vwap / intraday
        "vwap_stretch_pct": vwap.vwap_stretch_at(candles, idx, p["vwap_period"]),
        "vwap_reaction": vwap.vwap_reclaim_reject_at(candles, idx, p["vwap_period"]),
        "day_position": day["day_position"] if day else None,
        "daily_range_pct": day["daily_range_pct"] if day else None,
        # fvg
        "fvg_bull_distance_pct": fvg.nearest_fvg_distance_at(candles, idx, "bull", p["fvg_lookback"]),
        "fvg_bear_distance_pct": fvg.nearest_fvg_distance_at(candles, idx, "bear", p["fvg_lookback"]),
        "fvg_bull_reaction": fvg.fvg_reaction_at(candles, idx, "bull", p["fvg_lookback"]),
        "fvg_bear_reaction": fvg.fvg_reaction_at(candles, idx, "bear", p["fvg_lookback"]),
        # flow (None on assets without OI/funding/index)
        "oi_delta_pct": flow.oi_delta_at(candles, idx, p["oi_lookback"]),
        "oi_quadrant": flow.oi_price_quadrant_at(candles, idx, p["oi_lookback"]),
        "funding": flow.funding_at(candles, idx),
        "funding_zscore": flow.funding_zscore_at(candles, idx, p["funding_window"]),
        "funding_extreme": flow.funding_extreme_at(candles, idx),
        "perp_index_div_pct": flow.perp_index_divergence_at(candles, idx),
        # microstructure (None unless a snapshot was merged)
        "obi_top5": microstructure.obi_top5_at(candles, idx),
        "spread_bps": microstructure.spread_bps_at(candles, idx),
        "trade_delta_100": microstructure.trade_delta_at(candles, idx),
        "has_microstructure": microstructure.has_microstructure(candles, idx),
    }
    return snap
