# -*- coding: utf-8 -*-

import pytest

from src.research_lab.paths import PROJECT_ROOT
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.sweep_spec import (
    SweepSpec,
    ensure_valid,
    validate_sweep_spec,
)
from src.research_lab.timeframes import load_timeframe_profiles


@pytest.fixture
def profiles():
    return load_timeframe_profiles()


@pytest.fixture
def quiet():
    return load_resource_policy()


@pytest.fixture
def night():
    return load_resource_policy(night_mode=True)


def _light_spec(**over):
    base = dict(
        sweep_id="s1",
        anchor_symbol="SOL_USDT_SWAP",
        related_symbols=("BTC_USDT_SWAP",),
        timeframe="15m",
        setup_family="momentum_breakout",
        setup_grid={"lookback": [10, 20]},
        max_variants=8,
        backend="cpu",
        resource_class="light",
    )
    base.update(over)
    return SweepSpec(**base)


def test_valid_light_sweep_passes(profiles, quiet):
    result = validate_sweep_spec(_light_spec(), timeframe_profiles=profiles, resource_policy=quiet)
    assert result.ok
    assert result.effective_max_variants == 8
    ensure_valid(_light_spec(), timeframe_profiles=profiles, resource_policy=quiet)


def test_1m_full_universe_rejected(profiles, quiet):
    spec = _light_spec(
        timeframe="1m",
        resource_class="event_1m",
        related_symbols=tuple(f"S{i}_USDT_SWAP" for i in range(10)),
        setup_grid={"lookback": [5]},
    )
    result = validate_sweep_spec(spec, timeframe_profiles=profiles, resource_policy=quiet)
    assert not result.ok
    assert any("exceeds max_symbols_per_cycle" in e for e in result.errors)


def test_1m_requires_event_resource_class(profiles, quiet):
    spec = _light_spec(timeframe="1m", resource_class="normal", related_symbols=())
    result = validate_sweep_spec(spec, timeframe_profiles=profiles, resource_policy=quiet)
    assert not result.ok
    assert any("event_1m" in e for e in result.errors)


def test_max_variants_clipped_note(profiles, quiet):
    spec = _light_spec(max_variants=999, setup_grid={"lookback": [10, 20]})
    result = validate_sweep_spec(spec, timeframe_profiles=profiles, resource_policy=quiet)
    assert result.ok
    assert result.effective_max_variants == 16  # 15m profile cap
    assert any("clipped" in n for n in result.notes)


def test_variant_grid_exceeding_cap_rejected(profiles, quiet):
    spec = _light_spec(max_variants=16, setup_grid={"a": list(range(20))})
    result = validate_sweep_spec(spec, timeframe_profiles=profiles, resource_policy=quiet)
    assert not result.ok
    assert any("exceeds max_variants" in e for e in result.errors)


def test_heavy_job_rejected_under_quiet_but_allowed_at_night(profiles, quiet, night):
    spec = _light_spec(resource_class="heavy")
    quiet_result = validate_sweep_spec(spec, timeframe_profiles=profiles, resource_policy=quiet)
    assert not quiet_result.ok
    assert any("heavy job rejected" in e for e in quiet_result.errors)

    night_result = validate_sweep_spec(spec, timeframe_profiles=profiles, resource_policy=night)
    assert night_result.ok


def test_public_private_output_path_rejected(profiles, quiet):
    leak = str(PROJECT_ROOT / "configs" / "strategy_lab" / "leak.json")
    spec = _light_spec(private_output_policy=leak)
    result = validate_sweep_spec(spec, timeframe_profiles=profiles, resource_policy=quiet)
    assert not result.ok
    assert any("public repo" in e for e in result.errors)


def test_unknown_backend_rejected(profiles, quiet):
    spec = _light_spec(backend="quantum")
    result = validate_sweep_spec(spec, timeframe_profiles=profiles, resource_policy=quiet)
    assert not result.ok


def test_declared_gpu_backend_rejected_until_executor_exists(profiles, quiet):
    spec = _light_spec(backend="gpu")
    result = validate_sweep_spec(spec, timeframe_profiles=profiles, resource_policy=quiet)
    assert not result.ok
    assert any("not implemented yet" in e for e in result.errors)
