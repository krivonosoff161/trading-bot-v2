# -*- coding: utf-8 -*-
"""Tests for farm sweep materialization contracts."""
from __future__ import annotations

from src.research_lab.farm_sweep_runner import build_sweep_spec
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.sweep_compile import compile_sweep
from src.research_lab.timeframes import load_timeframe_profiles


def test_farm_sweep_variants_include_executable_exit_params():
    spec = build_sweep_spec("BTC_USDT_SWAP", "1h", "momentum_breakout", fingerprint="fp")
    exp = compile_sweep(
        spec,
        data_glob="market_data/1h/{symbol}_*.json",
        timeframe_profiles=load_timeframe_profiles(),
        resource_policy=load_resource_policy(),
    )
    variants = exp.parameter_grid["momentum_breakout"]
    assert variants
    for params in variants:
        assert params["lookback"] > 0
        assert params["hold_bars"] > 0
        assert params["stop_pct"] > 0
        assert params["take_pct"] > 0
