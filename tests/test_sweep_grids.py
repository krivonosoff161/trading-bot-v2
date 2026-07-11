# -*- coding: utf-8 -*-
"""Phase 1.1 — real per-family parameter grids with bounded tiers.

The old grid varied one size knob and froze stop/take (~4 variants). Tiers now actually
search stop/take/hold while preserving the executable RR>=2 gate, bounded per tier.
"""
from __future__ import annotations

import itertools

from src.research_lab.farm_sweep_runner import (
    DEFAULT_TIER,
    _family_grids,
    build_sweep_spec,
)
from src.research_lab.param_schemas import executable_exit_params, validate_params
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.sweep_compile import compile_sweep
from src.research_lab.timeframes import load_timeframe_profiles

FAMILIES = ("momentum_breakout", "mean_reversion_fade", "bb_volume_fade")
PROFILES = load_timeframe_profiles()
POLICY = load_resource_policy()


def _grid_count(setup: dict, exit_grid: dict) -> int:
    total = 1
    for g in (setup, exit_grid):
        for v in g.values():
            if v:
                total *= len(v)
    return total


def _defaults(family: str) -> dict:
    return executable_exit_params(family, get_strategy(family).parameter_defaults)


class TestRewardRiskPreserved:
    def test_all_stop_take_combos_are_rr2(self) -> None:
        for fam in FAMILIES:
            for tier in ("smoke", "normal", "deep"):
                _setup, exit_grid = _family_grids(fam, _defaults(fam), tier)
                stops = exit_grid.get("stop_pct") or []
                takes = exit_grid.get("take_pct") or []
                if stops and takes:
                    # take levels derived from max stop => every combo is RR>=2
                    assert min(takes) >= 2 * max(stops) - 1e-9, (fam, tier, stops, takes)

    def test_generated_variants_pass_executable_gate(self) -> None:
        for fam in FAMILIES:
            setup, exit_grid = _family_grids(fam, _defaults(fam), "deep")
            keys = sorted({*setup, *exit_grid})
            axes = {**setup, **exit_grid}
            combos = list(itertools.product(*(axes[k] for k in keys)))[:40]
            for combo in combos:
                params = dict(zip(keys, combo))
                res = validate_params(fam, params, require_executable=True)
                assert res.ok, (fam, params, res.errors)


class TestTierDepth:
    def test_deeper_tier_searches_more(self) -> None:
        for fam in FAMILIES:
            smoke = _grid_count(*_family_grids(fam, _defaults(fam), "smoke"))
            normal = _grid_count(*_family_grids(fam, _defaults(fam), "normal"))
            deep = _grid_count(*_family_grids(fam, _defaults(fam), "deep"))
            assert smoke <= normal <= deep
            assert deep > smoke  # depth actually increases

    def test_normal_varies_stop_and_take(self) -> None:
        # The whole point: stop/take are no longer frozen at one value.
        setup, exit_grid = _family_grids(
            "momentum_breakout", _defaults("momentum_breakout"), "normal"
        )
        assert len(exit_grid.get("stop_pct") or []) >= 2
        assert len(exit_grid.get("take_pct") or []) >= 2


class TestBuildSpec:
    def test_tier_recorded_on_spec(self) -> None:
        spec = build_sweep_spec("BTC-USDT-SWAP", "1d", "momentum_breakout",
                                fingerprint="abc123", tier="deep")
        assert spec.variant_tier == "deep"
        assert "deep" in spec.sweep_id

    def test_default_tier(self) -> None:
        spec = build_sweep_spec("BTC-USDT-SWAP", "1d", "momentum_breakout", fingerprint="x")
        assert spec.variant_tier == DEFAULT_TIER

    def test_compile_depth_increases_with_tier(self) -> None:
        glob = "data/{symbol}_*_1d.json"
        counts = {}
        for tier in ("smoke", "normal", "deep"):
            spec = build_sweep_spec("BTC-USDT-SWAP", "1d", "momentum_breakout",
                                    fingerprint="fp", backend="cpu", tier=tier)
            exp = compile_sweep(spec, data_glob=glob, timeframe_profiles=PROFILES,
                                resource_policy=POLICY)
            counts[tier] = len(exp.parameter_grid["momentum_breakout"])
        assert counts["smoke"] < counts["normal"] <= counts["deep"]
