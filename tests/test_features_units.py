# -*- coding: utf-8 -*-
"""Known-answer unit tests for the non-parity feature layer pieces.

Parity vs old-main is covered by test_features_parity. This file pins the new
features (swings, breakout quality, VWAP, volume/sideways, FVG reactions, flow
OI/funding, microstructure) on small hand-built series with exact expectations.
"""

from __future__ import annotations

from src.research_lab.features import flow, fvg, microstructure, structure, volume, vwap

HOUR = 3_600_000


def _c(ts, o, h, low, cl, v=1000.0, **extra):
    return {"ts": ts, "open": o, "high": h, "low": low, "close": cl, "vol": v, **extra}


# ---- structure: swings + breakout quality ----

def test_swing_levels_finds_confirmed_pivot():
    # bar 3 is a clean pivot high (10.0), bars around it lower; lookback=2 confirms at bar 5
    highs = [5, 6, 7, 10, 7, 6, 5, 4, 3]
    candles = [_c(i * HOUR, h - 1, h, h - 2, h - 1) for i, h in enumerate(highs)]
    sw = structure.swing_levels(candles, idx=len(candles) - 1, lookback=2)
    assert 10 in sw["recent_highs"]


def test_breakout_quality_long_break():
    # 20 flat bars then a strong close above the range
    candles = [_c(i * HOUR, 10, 10.5, 9.5, 10) for i in range(20)]
    candles.append(_c(20 * HOUR, 10, 12, 10, 11.8))  # closes above prior high 10.5
    brk = structure.breakout_quality_at(candles, idx=20, lookback=20)
    assert brk["side"] == "long"
    assert brk["close_position"] > 0.5  # closed in the upper half


# ---- vwap + day position ----

def test_rolling_vwap_flat_volume_equals_mean_typical():
    candles = [_c(i * HOUR, 10, 12, 8, 10, v=100.0) for i in range(10)]  # typical=(12+8+10)/3=10
    assert abs(vwap.rolling_vwap_at(candles, 9, period=5) - 10.0) < 1e-9


def test_day_position_mid_range():
    # one UTC day, range 8..12, close at 10 -> day_position 0.5
    candles = [_c(i * HOUR, 10, 12, 8, 10) for i in range(5)]
    dp = vwap.day_position_at(candles, idx=4)
    assert abs(dp["day_position"] - 0.5) < 1e-9


def test_vwap_reclaim_up():
    # price below vwap then closes back above -> reclaim_up
    candles = [_c(i * HOUR, 10, 10.2, 9.8, 10.0, v=100.0) for i in range(10)]
    candles.append(_c(10 * HOUR, 9.0, 9.1, 8.9, 9.0, v=100.0))   # dip below
    candles.append(_c(11 * HOUR, 9.0, 12.0, 9.0, 11.5, v=100.0))  # reclaim above vwap
    assert vwap.vwap_reclaim_reject_at(candles, idx=11, period=10) == "reclaim_up"


# ---- volume: vol_ratio + sideways accumulation ----

def test_vol_ratio_doubles():
    candles = [_c(i * HOUR, 10, 10, 10, 10, v=100.0) for i in range(20)]
    candles.append(_c(20 * HOUR, 10, 10, 10, 10, v=200.0))
    assert abs(volume.vol_ratio_at(candles, idx=20, period=20) - 2.0) < 1e-9


def test_sideways_range_volume_detects_tight_range():
    # 40 tight-range bars; window vol == prior window vol -> accumulation ~1, is_sideways True
    candles = [_c(i * HOUR, 100, 101, 99, 100, v=500.0) for i in range(41)]
    sw = volume.sideways_range_volume_at(candles, idx=40, lookback=20, max_range_pct=12.0)
    assert sw["is_sideways"] is True
    assert abs(sw["accumulation_ratio"] - 1.0) < 1e-9


# ---- flow: OI / funding / basis ----

def test_oi_delta_and_quadrant_new_longs():
    candles = []
    for i in range(10):
        oi = 1000.0 * (1 + 0.01 * i)   # OI rising
        candles.append(_c(i * HOUR, 10 + i, 11 + i, 9 + i, 10 + i, oi=oi))  # price rising
    assert flow.oi_delta_at(candles, idx=9, lookback=5) > 0
    assert flow.oi_price_quadrant_at(candles, idx=9, lookback=5) == "new_longs"


def test_funding_extreme_and_zscore():
    candles = [_c(i * HOUR, 10, 10, 10, 10, funding=0.0001) for i in range(48)]
    candles.append(_c(48 * HOUR, 10, 10, 10, 10, funding=0.0015))  # extreme positive
    assert flow.funding_extreme_at(candles, idx=48, warn=0.0005, block=0.001).startswith("long_squeeze_risk")
    # constant funding region -> zscore 0
    assert flow.funding_zscore_at(candles, idx=40, window=40) == 0.0


def test_perp_index_divergence_and_missing_flow():
    c = _c(0, 10, 10, 10, 101.0, index_px=100.0)
    assert abs(flow.perp_index_divergence_at([c], 0) - 1.0) < 1e-6
    # no oi field -> None (graceful, never invented)
    assert flow.oi_delta_at([_c(0, 10, 10, 10, 10)], 0, lookback=1) is None


# ---- FVG reaction ----

def test_fvg_bull_gap_reclaim():
    # bars 0,1,2 form a bull gap: high[0]=9 < low[2]=11 -> gap [9,11]
    candles = [_c(0, 8, 9, 8, 8.5), _c(HOUR, 10, 12, 10, 11), _c(2 * HOUR, 11.5, 13, 11, 12)]
    # bar 3 dips into the gap (low=9.5 <= 11) and closes back above it (close=11.5 > 11)
    candles.append(_c(3 * HOUR, 11.5, 11.6, 9.5, 11.5))
    gaps = fvg.find_fvg(candles, idx=3, direction="bull", lookback=30)
    assert gaps and gaps[0]["low"] == 9 and gaps[0]["high"] == 11
    assert fvg.fvg_reaction_at(candles, idx=3, direction="bull", lookback=30) == "reclaim"


# ---- microstructure ----

def test_microstructure_present_and_absent():
    with_micro = _c(0, 10, 10, 10, 10, obi_top5=0.3, trade_delta_100=40.0, spread_bps=2.0)
    assert microstructure.has_microstructure([with_micro], 0) is True
    assert microstructure.obi_top5_at([with_micro], 0) == 0.3
    plain = _c(0, 10, 10, 10, 10)
    assert microstructure.has_microstructure([plain], 0) is False
    assert microstructure.spread_bps_at([plain], 0) is None
