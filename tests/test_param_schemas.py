# -*- coding: utf-8 -*-
"""Parameter authority: registry remains source of truth; schema gates execution."""

from src.research_lab.param_schemas import (
    PARAMETER_SEARCH_CONTRACT_VERSION,
    executable_exit_params,
    parameter_search_contract,
    validate_horizon,
    validate_parameter_grid,
    validate_params,
)


def test_parameter_search_contract_is_typed_versioned_and_family_derived():
    contract = parameter_search_contract("rsi_reversal")
    assert contract.version == PARAMETER_SEARCH_CONTRACT_VERSION
    assert contract.strategy_id == "rsi_reversal"
    assert contract.primary_axis.name == "period"
    assert contract.primary_axis.value_type == "int"
    assert contract.primary_axis.minimum <= contract.primary_axis.default <= contract.primary_axis.maximum
    assert contract.primary_axis.unit == "bars"
    assert contract.followup.max_default_multiplier == 2.0


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


def test_exit_mode_meta_key_allowed_for_research_variants():
    result = validate_params(
        "momentum_breakout",
        {
            "lookback": 20,
            "hold_bars": 5,
            "stop_pct": 8,
            "take_pct": 16,
            "exit_mode": "trailing",
        },
        require_executable=True,
    )
    assert result.ok, result.errors

    bad = validate_params(
        "momentum_breakout",
        {
            "lookback": 20,
            "hold_bars": 5,
            "stop_pct": 8,
            "take_pct": 16,
            "exit_mode": "magic_exit",
        },
    )
    assert not bad.ok
    assert "exit_mode:invalid_value" in bad.errors


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


def test_horizon_band_per_timeframe():
    # 15m allows up to 192 bars (~48h); 193 is too long for the scale.
    assert validate_horizon("15m", {"hold_bars": 192}) == []
    assert validate_horizon("15m", {"hold_bars": 193}) == ["hold_bars:horizon_above_192_for_15m"]
    # 1d allows up to 30 bars (~30 days); 60 is weeks-too-long.
    assert validate_horizon("1d", {"hold_bars": 30}) == []
    assert validate_horizon("1d", {"hold_bars": 60}) == ["hold_bars:horizon_above_30_for_1d"]
    # no band for a timeframe => no horizon constraint
    assert validate_horizon("3h", {"hold_bars": 999}) == []
