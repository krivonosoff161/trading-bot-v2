# -*- coding: utf-8 -*-
"""Parity: the stdlib lab feature layer must match the numpy old-main indicators.

The lab feature layer (src/research_lab/features) is a pure-Python re-port of
src/strategy/indicators.py. This test asserts they agree within float tolerance
on a deterministic synthetic series, so the research farm computes the SAME math
the live analyst engine does — without importing the live engine.
"""

from __future__ import annotations

import math
import random


from src.research_lab.features import _core, fvg, trend, volatility
from src.strategy import indicators

TOL = 1e-6


def _series(n: int = 400, seed: int = 7) -> list[dict]:
    """Deterministic random-walk OHLCV candles (chronological, lab dict format)."""
    rng = random.Random(seed)
    price = 100.0
    out: list[dict] = []
    ts = 1_700_000_000_000
    for i in range(n):
        drift = math.sin(i / 25.0) * 0.4
        price = max(1.0, price + drift + rng.uniform(-1.2, 1.2))
        high = price + abs(rng.uniform(0.1, 1.5))
        low = price - abs(rng.uniform(0.1, 1.5))
        open_ = low + (high - low) * rng.random()
        close = low + (high - low) * rng.random()
        out.append({
            "ts": ts + i * 3_600_000,
            "open": round(open_, 4), "high": round(high, 4),
            "low": round(low, 4), "close": round(close, 4),
            "vol": round(rng.uniform(100, 5000), 2),
        })
    return out


def _okx_chrono(candles: list[dict]) -> list[list]:
    return [[c["ts"], c["open"], c["high"], c["low"], c["close"], c["vol"]] for c in candles]


def _okx_newest_first(candles: list[dict]) -> list[list]:
    return list(reversed(_okx_chrono(candles)))


def test_ema_parity():
    candles = _series()
    _h, _l, closes = indicators.parse_candles(_okx_newest_first(candles))
    for period in (20, 50):
        expected = float(indicators.calc_ema(closes, period)[-1])
        got = trend.ema_at(candles, len(candles) - 1, period)
        assert got is not None and abs(got - expected) < TOL, f"ema{period}: {got} vs {expected}"


def test_atr_parity():
    candles = _series()
    h, low_arr, closes = indicators.parse_candles(_okx_newest_first(candles))
    expected = indicators.calc_atr(h, low_arr, closes, 14)
    got = volatility.atr_at(candles, len(candles) - 1, 14)
    assert got is not None and abs(got - expected) < TOL


def test_adx_di_parity():
    candles = _series()
    h, low_arr, closes = indicators.parse_candles(_okx_newest_first(candles))
    exp_adx, exp_plus, exp_minus = indicators.calc_adx(h, low_arr, closes, 14, bar_index=-1)
    got = trend.adx_at(candles, len(candles) - 1, 14)
    assert got is not None
    assert abs(got[0] - exp_adx) < TOL
    assert abs(got[1] - exp_plus) < TOL
    assert abs(got[2] - exp_minus) < TOL


def test_bollinger_parity():
    candles = _series()
    _h, _l, closes = indicators.parse_candles(_okx_newest_first(candles))
    exp = indicators.calc_bollinger_bands(closes, 20, 2.0)
    got = volatility.bollinger_at(candles, len(candles) - 1, 20, 2.0)
    assert got is not None
    for key in ("upper", "middle", "lower", "pct_b", "width_pct"):
        assert abs(got[key] - exp[key]) < 1e-4, f"bb {key}: {got[key]} vs {exp[key]}"


def test_rsi_parity():
    candles = _series()
    _h, _l, closes = indicators.parse_candles(_okx_newest_first(candles))
    close_list = [float(c["close"]) for c in candles]
    for period in (3, 14):
        expected = indicators.calc_rsi(closes, period)
        got = _core.rsi_wilder(close_list, len(candles) - 1, period)
        assert got is not None and abs(got - expected) < TOL, f"rsi{period}: {got} vs {expected}"


def test_slope_parity():
    candles = _series()
    _h, _l, closes = indicators.parse_candles(_okx_newest_first(candles))
    for period in (5, 10):
        expected = indicators.calc_slope(closes, period)
        got = trend.slope_deg(candles, len(candles) - 1, period)
        assert abs(got - expected) < 1e-4, f"slope{period}: {got} vs {expected}"


def test_supertrend_direction_parity():
    candles = _series()
    h, low_arr, closes = indicators.parse_candles(_okx_newest_first(candles))
    exp = indicators.calc_supertrend(h, low_arr, closes, 14, 3.0)["direction"]
    got = trend.supertrend_dir_at(candles, len(candles) - 1, 14, 3.0)
    assert got == exp


def test_fvg_parity():
    candles = _series()
    for direction in ("bull", "bear"):
        expected = indicators.find_fvg(_okx_chrono(candles), direction, lookback=30)
        got = fvg.find_fvg(candles, len(candles) - 1, direction, lookback=30)
        assert len(got) == len(expected), f"{direction}: {len(got)} vs {len(expected)} gaps"
        for g, e in zip(got, expected):
            assert abs(g["low"] - e["low"]) < TOL
            assert abs(g["high"] - e["high"]) < TOL
            assert g["age_bars"] == e["age_bars"]
