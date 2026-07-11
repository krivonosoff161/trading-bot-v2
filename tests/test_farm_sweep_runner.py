# -*- coding: utf-8 -*-
"""Tests for farm sweep materialization contracts."""
from __future__ import annotations

from src.research_lab.farm_sweep_runner import build_sweep_spec
from src.research_lab.param_schemas import parameter_search_contract
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


def _variants_for_dimension(dimension: str):
    spec = build_sweep_spec(
        "BTC_USDT_SWAP",
        "1h",
        "momentum_breakout",
        fingerprint="fp",
        dimensions=(dimension,),
    )
    exp = compile_sweep(
        spec,
        data_glob="market_data/1h/{symbol}_*.json",
        timeframe_profiles=load_timeframe_profiles(),
        resource_policy=load_resource_policy(),
    )
    return exp.parameter_grid["momentum_breakout"]


def test_trailing_dimension_adds_dynamic_exit_modes():
    variants = _variants_for_dimension("trailing")
    modes = {row.get("exit_mode") for row in variants}
    assert {"baseline", "trailing", "break_even"} <= modes


def test_stop_dimension_widens_stop_axis():
    base = _variants_for_dimension("hold")
    stop = _variants_for_dimension("stop")
    assert len({row["stop_pct"] for row in stop}) > len({row["stop_pct"] for row in base})


def test_take_profit_dimension_widens_take_axis():
    base = _variants_for_dimension("hold")
    take = _variants_for_dimension("take_profit")
    assert len({row["take_pct"] for row in take}) > len({row["take_pct"] for row in base})


def test_entry_timing_dimension_widens_size_axis():
    base = _variants_for_dimension("hold")
    entry = _variants_for_dimension("entry_timing")
    assert len({row["lookback"] for row in entry}) > len({row["lookback"] for row in base})


def test_non_lookback_family_uses_registry_owned_axis():
    spec = build_sweep_spec(
        "BTC_USDT_SWAP", "1h", "rsi_reversal", fingerprint="fp",
        dimensions=("entry_timing",),
    )
    assert "period" in spec.setup_grid
    assert "lookback" not in spec.setup_grid
    assert len(spec.setup_grid["period"]) > 1


def test_farm_varies_every_declared_axis_within_typed_bounds():
    spec = build_sweep_spec(
        "BTC_USDT_SWAP", "1h", "momentum_breakout", fingerprint="fp"
    )
    contract = parameter_search_contract("momentum_breakout")
    for axis in contract.adaptive_axes:
        assert axis.name in spec.setup_grid
        assert all(
            axis.minimum <= float(value) <= axis.maximum
            for value in spec.setup_grid[axis.name]
        )


def test_zero_default_adaptive_axes_receive_absolute_search_levels():
    for family, axis_name in (
        ("momentum_breakout", "threshold_pct"),
        ("sfp_liquidity_sweep", "vol_mult"),
        ("sfp_liquidity_sweep", "reclaim_buf_pct"),
        ("microstructure_confirmed_breakout", "min_trade_delta"),
    ):
        spec = build_sweep_spec("BTC_USDT_SWAP", "1h", family, fingerprint="fp")
        assert len(spec.setup_grid[axis_name]) > 1
        assert any(float(value) > 0 for value in spec.setup_grid[axis_name])
