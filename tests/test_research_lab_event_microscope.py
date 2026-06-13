# -*- coding: utf-8 -*-

import json

from src.research_lab.event_microscope import (
    MIN_BARS_FOR_MICROSCOPE,
    derive_limits,
    locate_microscope_data,
    plan_microscope,
)
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.sweep_spec import SweepSpec, validate_sweep_spec
from src.research_lab.timeframes import load_timeframe_profiles

MINUTE_MS = 60_000
DAY_MS = 86_400_000


def _write_candles(path, n, step_ms, start_ts=1_700_000_000_000):
    rows = [
        {
            "ts": start_ts + i * step_ms,
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "vol": 1.0,
        }
        for i in range(n)
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")


def _glob(tmp_path):
    return str(tmp_path / "{symbol}_*.json")


def test_locate_1m_present(tmp_path):
    _write_candles(tmp_path / "BTC_USDT_SWAP_1m.json", n=150, step_ms=MINUTE_MS)
    found = locate_microscope_data(_glob(tmp_path), "BTC_USDT_SWAP")
    assert found.status == "available"
    assert found.timeframe == "1m"
    assert found.rows == 150
    assert found.file_label == "BTC_USDT_SWAP_1m.json"  # name only, no abs path


def test_locate_1m_missing(tmp_path):
    found = locate_microscope_data(_glob(tmp_path), "GHOST_USDT_SWAP")
    assert found.status == "missing"
    assert found.reason == "no_1m_file"


def test_locate_1m_too_short(tmp_path):
    _write_candles(tmp_path / "ETH_USDT_SWAP_1m.json", n=80, step_ms=MINUTE_MS)
    found = locate_microscope_data(_glob(tmp_path), "ETH_USDT_SWAP")
    assert found.status == "too_short"
    assert str(MIN_BARS_FOR_MICROSCOPE) in found.reason


def test_locate_rejects_daily_file_as_not_1m(tmp_path):
    _write_candles(tmp_path / "SOL_USDT_SWAP_1Dutc.json", n=200, step_ms=DAY_MS)
    found = locate_microscope_data(_glob(tmp_path), "SOL_USDT_SWAP")
    assert found.status == "not_1m"
    assert found.timeframe == "1D"


def test_derive_limits_from_profile_and_policy():
    limits = derive_limits(load_timeframe_profiles(), load_resource_policy())
    assert limits.timeframe == "1m"
    assert limits.enabled is True  # quiet_desktop allows trigger_only 1m
    assert limits.max_symbols == 2
    assert limits.max_event_windows == 3
    assert limits.max_bars_per_window == 6 * 60
    assert limits.max_variants == 8


def test_plan_caps_symbols_and_reports_missing(tmp_path):
    symbols = ["A_USDT_SWAP", "B_USDT_SWAP", "C_USDT_SWAP", "D_USDT_SWAP"]
    plan = plan_microscope(
        symbols, data_glob=_glob(tmp_path),
        profiles=load_timeframe_profiles(), policy=load_resource_policy(),
    )
    assert len(plan.data) == 2  # max_symbols cap
    assert [s for s, _ in plan.skipped_symbols] == ["C_USDT_SWAP", "D_USDT_SWAP"]
    assert plan.availability_counts().get("missing") == 2
    summary = plan.to_summary()
    assert summary["enabled"] is True
    assert "C_USDT_SWAP" in summary["over_symbol_cap"]
    # public-safe: no absolute path leaks
    assert str(tmp_path) not in json.dumps(summary)


def test_full_universe_1m_sweep_is_blocked():
    profiles, policy = load_timeframe_profiles(), load_resource_policy()
    too_wide = SweepSpec(
        sweep_id="bad_full_1m",
        anchor_symbol="BTC_USDT_SWAP",
        related_symbols=tuple(f"X{i}_USDT_SWAP" for i in range(10)),  # scope 11 >> cap 2
        timeframe="1m",
        setup_family="momentum_breakout",
        resource_class="event_1m",
    )
    result = validate_sweep_spec(too_wide, timeframe_profiles=profiles, resource_policy=policy)
    assert not result.ok
    assert any("max_symbols_per_cycle" in e for e in result.errors)


def test_bounded_event_microscope_sweep_is_allowed():
    profiles, policy = load_timeframe_profiles(), load_resource_policy()
    ok_spec = SweepSpec(
        sweep_id="ok_micro",
        anchor_symbol="BTC_USDT_SWAP",
        related_symbols=(),  # scope 1 <= cap 2
        timeframe="1m",
        setup_family="momentum_breakout",
        setup_grid={"lookback": [10, 20]},
        max_variants=4,
        resource_class="event_1m",
    )
    result = validate_sweep_spec(ok_spec, timeframe_profiles=profiles, resource_policy=policy)
    assert result.ok, result.errors
