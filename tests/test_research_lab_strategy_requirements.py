# -*- coding: utf-8 -*-

from src.research_lab.strategy_requirements import DEFAULT_LOOKBACK, derive_requirement


def test_lookback_from_registry_defaults():
    r = derive_requirement("momentum_breakout", "BTC_USDT_SWAP", "1d")
    assert r.lookback_bars == 20  # registry parameter_defaults lookback
    assert r.warmup_bars == 20
    assert r.min_rows == 20 + 20 + 30
    assert r.symbol == "BTC_USDT_SWAP" and r.timeframe == "1d"


def test_params_override_lookback():
    r = derive_requirement("momentum_breakout", "ETH_USDT_SWAP", "1d", params={"lookback": 50})
    assert r.lookback_bars == 50 and r.min_rows == 50 + 50 + 30


def test_trend_ma_strategy_uses_ma_window():
    r = derive_requirement("trend_pullback", "BTC_USDT_SWAP", "1d")  # trend_ma=30
    assert r.lookback_bars == 30


def test_volume_family_flags_volume():
    r = derive_requirement("volume_shock_continuation", "BTC_USDT_SWAP", "1d")
    assert r.needs_volume is True


def test_unknown_strategy_falls_back_to_default_lookback():
    r = derive_requirement("does_not_exist", "X_USDT_SWAP", "1d")
    assert r.lookback_bars == DEFAULT_LOOKBACK
    assert r.needs_volume is False


def test_needs_1m_microscope_flag_passthrough():
    r = derive_requirement("momentum_breakout", "BTC_USDT_SWAP", "1m", needs_1m_microscope=True)
    assert r.needs_1m_microscope is True
