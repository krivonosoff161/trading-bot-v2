# -*- coding: utf-8 -*-

from src.research_lab.strategy_registry import REGISTRY
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


def test_every_registered_strategy_has_an_explicit_history_manifest():
    assert len(REGISTRY) == 27
    assert all(definition.history_formulas for definition in REGISTRY.values())
    assert all(definition.history_formula_labels() for definition in REGISTRY.values())


def test_complex_strategy_uses_actual_generator_windows():
    regime = derive_requirement("main_fast_swing_regime", "BTC_USDT_SWAP", "1h")
    assert regime.warmup_bars == 50
    assert regime.lookback_bars == 50
    squeeze = derive_requirement("volatility_squeeze_breakout_v2", "BTC_USDT_SWAP", "15m")
    assert squeeze.warmup_bars == 40
    assert "2*squeeze_lookback" in squeeze.history_formulas


def test_flow_strategy_declares_non_ohlcv_requirements():
    req = derive_requirement("oi_funding_squeeze", "BTC_USDT_SWAP", "1h")
    assert req.required_data == ("oi", "funding")
