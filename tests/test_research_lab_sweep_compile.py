# -*- coding: utf-8 -*-

import pytest

from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.sweep_compile import (
    bounded_uniform_sample,
    compile_sweep,
    expand_grids,
    expand_grids_bounded,
)
from src.research_lab.sweep_spec import SweepSpec
from src.research_lab.timeframes import load_timeframe_profiles

GLOB = "data/{symbol}_*.json"


def _ctx():
    return load_timeframe_profiles(), load_resource_policy()


def _spec(**over):
    base = dict(
        sweep_id="s1",
        anchor_symbol="SOL_USDT_SWAP",
        related_symbols=("BTC_USDT_SWAP",),
        timeframe="15m",
        setup_family="momentum_breakout",
        setup_grid={"lookback": [10, 20]},
        max_variants=16,
        backend="cpu",
        resource_class="light",
    )
    base.update(over)
    return SweepSpec(**base)


def test_expand_grids_cartesian():
    out = expand_grids({"a": [1, 2]}, {"b": [3, 4, 5]})
    assert len(out) == 6
    assert {"a": 1, "b": 3} in out
    assert expand_grids({}, {}) == [{}]


def test_compile_valid_sweep_builds_experiment_spec():
    profiles, policy = _ctx()
    spec = _spec(
        setup_grid={"lookback": [10, 20]},
        exit_grid={"hold_bars": [3, 5]},
        filter_grid={"trend": ["up"], "volatility": ["high"]},
    )
    exp = compile_sweep(spec, data_glob=GLOB, timeframe_profiles=profiles, resource_policy=policy)
    assert exp.families == ["momentum_breakout"]
    assert exp.symbols == ["SOL_USDT_SWAP", "BTC_USDT_SWAP"]
    assert len(exp.parameter_grid["momentum_breakout"]) == 4  # 2x2
    assert all("trend" not in row and "volatility" not in row for row in exp.parameter_grid["momentum_breakout"])
    assert exp.filters == {"trend": ["up"], "volatility": ["high"]}
    assert exp.experiment_id == "sweep_s1"


def test_compile_caps_variant_explosion():
    profiles, policy = _ctx()
    # 5 * 4 = 20 variants -> clipped to 15m profile cap (16); job runs capped to policy (24)
    spec = _spec(
        max_variants=999,
        setup_grid={"lookback": [10, 20, 30, 40, 50]},
        exit_grid={"hold_bars": [2, 4, 6, 8]},
    )
    exp = compile_sweep(spec, data_glob=GLOB, timeframe_profiles=profiles, resource_policy=policy)
    assert len(exp.parameter_grid["momentum_breakout"]) == 16  # profile max_variants_per_setup
    assert exp.max_runs == 24  # resource policy max_variants_per_job


def test_bounded_sampler_is_deterministic_and_not_prefix_biased():
    variants = [{"axis": value} for value in range(100)]
    first = bounded_uniform_sample(variants, 10, seed_material="same")
    second = bounded_uniform_sample(variants, 10, seed_material="same")
    assert first == second
    assert len(first) == 10
    assert first != variants[:10]
    assert any(row["axis"] >= 50 for row in first)


def test_bounded_cartesian_expansion_does_not_materialize_prefix_only():
    first = expand_grids_bounded(
        {"a": list(range(10_000))}, {"b": list(range(10_000))},
        cap=12, seed_material="large-space",
    )
    second = expand_grids_bounded(
        {"a": list(range(10_000))}, {"b": list(range(10_000))},
        cap=12, seed_material="large-space",
    )
    assert first == second
    assert len(first) == 12
    assert any(row["a"] > 100 or row["b"] > 100 for row in first)


def test_bounded_cartesian_reserves_baseline_and_axis_coverage():
    rows = expand_grids_bounded(
        {"a": [0, 1, 2]}, {"b": [0, 1, 2]}, {"c": [0, 1, 2]},
        cap=8, seed_material="coverage", baseline={"a": 1, "b": 1, "c": 1},
    )
    assert {"a": 1, "b": 1, "c": 1} in rows
    assert {row["a"] for row in rows} == {0, 1, 2}
    assert {row["b"] for row in rows} == {0, 1, 2}
    assert {row["c"] for row in rows} == {0, 1, 2}


def test_compile_rejects_heavy_under_quiet():
    profiles, policy = _ctx()
    spec = _spec(resource_class="heavy")
    with pytest.raises(ValueError):
        compile_sweep(spec, data_glob=GLOB, timeframe_profiles=profiles, resource_policy=policy)


def test_compile_rejects_1m_full_scope():
    profiles, policy = _ctx()
    spec = _spec(
        timeframe="1m",
        resource_class="event_1m",
        related_symbols=tuple(f"S{i}_USDT_SWAP" for i in range(8)),
    )
    with pytest.raises(ValueError):
        compile_sweep(spec, data_glob=GLOB, timeframe_profiles=profiles, resource_policy=policy)
