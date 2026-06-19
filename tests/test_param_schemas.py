# -*- coding: utf-8 -*-
"""Parameter authority: registry remains source of truth; schema gates execution."""

from src.research_lab.param_schemas import (
    executable_exit_params,
    validate_parameter_grid,
    validate_params,
)


def test_stop_take_units_are_percent_points_and_rr_gated():
    ok = validate_params(
        "momentum_breakout",
        {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        require_executable=True,
    )
    assert ok.ok, ok.errors

    bad = validate_params(
        "momentum_breakout",
        {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 8},
        require_executable=True,
    )
    assert not bad.ok
    assert "take_pct:reward_risk_below_2r" in bad.errors


def test_unknown_parameter_rejected_but_direction_meta_key_allowed():
    result = validate_params(
        "momentum_breakout",
        {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16, "direction": "long"},
        require_executable=True,
    )
    assert result.ok, result.errors

    bad = validate_params(
        "momentum_breakout",
        {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16, "mystery": 1},
    )
    assert not bad.ok
    assert "mystery:unknown_param" in bad.errors


def test_parameter_grid_reports_variant_errors():
    result = validate_parameter_grid(
        "momentum_breakout",
        [
            {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
            {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 4},
        ],
        require_executable=True,
    )
    assert not result.ok
    assert "variant_1:take_pct:reward_risk_below_2r" in result.errors


def test_internal_farm_defaults_can_be_made_executable_without_changing_registry():
    params = executable_exit_params("mean_reversion_fade")
    assert params["stop_pct"] == 10
    assert params["take_pct"] == 20
    assert validate_params("mean_reversion_fade", params, require_executable=True).ok
