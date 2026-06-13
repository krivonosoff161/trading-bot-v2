# -*- coding: utf-8 -*-

import json

import pytest

from src.research_lab.data_requirements import (
    MINUTE_MS,
    OUTSIDE_POLICY,
    DataRequirement,
    apply_policy_caps,
    collect_requirements,
    manual_requirement,
    parse_utc_to_ms,
    requirement_from_event,
)
from src.research_lab.event_microscope import MicroscopeLimits

LIMITS = MicroscopeLimits(
    timeframe="1m", max_symbols=2, max_event_windows=3, max_bars_per_window=360,
    max_variants=8, enabled=True,
)


def _req(symbol, start, end, status="candidate"):
    return DataRequirement(symbol, "1m", start, end, "r", "manual", 360, status)


def test_parse_utc_assumes_utc():
    a = parse_utc_to_ms("2025-01-30T00:00")
    assert a == parse_utc_to_ms("2025-01-30T00:00:00+00:00") == parse_utc_to_ms("2025-01-30T00:00:00Z")
    assert parse_utc_to_ms("2025-01-30T01:00") - a == 3_600_000


def test_requirement_from_event_is_minute_aligned_and_capped():
    ctx = {
        "anchor_symbol": "BTC_USDT_SWAP",
        "move_start_ts": 1_700_000_000_000,
        "move_end_ts": 1_700_000_000_000 + 1000 * MINUTE_MS,  # huge -> must clip
    }
    r = requirement_from_event(ctx, limits=LIMITS, reason="x", source_type="event_sweep")
    assert r is not None
    assert r.symbol == "BTC_USDT_SWAP" and r.timeframe == "1m"
    assert r.start_ts % MINUTE_MS == 0 and r.end_ts % MINUTE_MS == 0
    assert r.bar_count() <= LIMITS.max_bars_per_window


def test_requirement_from_event_bad_context_returns_none():
    assert requirement_from_event({}, limits=LIMITS, reason="x", source_type="e") is None
    assert requirement_from_event({"move_start_ts": 5, "move_end_ts": 1, "anchor_symbol": "X"},
                                  limits=LIMITS, reason="x", source_type="e") is None


def test_manual_requirement_caps_bars():
    r = manual_requirement("BTC_USDT_SWAP", "2025-01-30T00:00", "2025-01-31T00:00", limits=LIMITS)  # 24h
    assert r.bar_count() <= LIMITS.max_bars_per_window
    assert r.source_type == "manual"


def test_manual_requirement_end_before_start_raises():
    with pytest.raises(ValueError):
        manual_requirement("X_USDT_SWAP", "2025-01-30T06:00", "2025-01-30T00:00", limits=LIMITS)


def test_policy_caps_symbol_limit_marks_outside():
    reqs = [_req(f"S{i}_USDT_SWAP", 0, 60_000) for i in range(5)]  # same window, 5 symbols
    out = apply_policy_caps(reqs, LIMITS)
    kept = [r for r in out if r.status != OUTSIDE_POLICY]
    outside = [r for r in out if r.status == OUTSIDE_POLICY]
    assert len(kept) == 2  # max_symbols
    assert len(outside) == 3


def test_policy_caps_window_limit_marks_outside():
    reqs = [_req("S_USDT_SWAP", i * 100_000, i * 100_000 + 60_000) for i in range(4)]  # 4 windows
    out = apply_policy_caps(reqs, LIMITS)
    assert sum(1 for r in out if r.status == OUTSIDE_POLICY) == 1  # max_event_windows = 3


def test_policy_caps_dedup_identical():
    reqs = [_req("S_USDT_SWAP", 0, 60_000), _req("S_USDT_SWAP", 0, 60_000)]
    assert len(apply_policy_caps(reqs, LIMITS)) == 1


def test_collect_from_event_specs(tmp_path):
    d = tmp_path / "plans" / "event_specs"
    d.mkdir(parents=True)
    (d / "spec1.json").write_text(json.dumps({
        "experiment_id": "e1", "symbols": ["BTC_USDT_SWAP"],
        "event_context": {"anchor_symbol": "BTC_USDT_SWAP",
                          "move_start_ts": 1_700_000_000_000, "move_end_ts": 1_700_000_600_000, "direction": "up"},
    }), encoding="utf-8")
    reqs = collect_requirements(tmp_path, limits=LIMITS)
    assert any(r.symbol == "BTC_USDT_SWAP" and r.source_type == "event_sweep" for r in reqs)


def test_collect_with_manual_and_no_specs(tmp_path):
    manual = manual_requirement("ETH_USDT_SWAP", "2025-01-30T00:00", "2025-01-30T01:00", limits=LIMITS)
    reqs = collect_requirements(tmp_path, limits=LIMITS, manual=manual)
    assert len(reqs) == 1 and reqs[0].source_type == "manual"
