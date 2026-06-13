# -*- coding: utf-8 -*-

from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.runtime_policy import (
    cadence_path,
    effective_variant_cap,
    evaluate_cadence,
    load_recent_starts,
    record_start,
)

QUIET = load_resource_policy()  # quiet_desktop: min 900s, 2 jobs/hour, cap 24
NIGHT = load_resource_policy(night_mode=True)  # 60s, cap 80


def test_first_job_is_allowed():
    decision = evaluate_cadence(QUIET, [], now=1000.0)
    assert decision.allowed is True
    assert decision.reason == "ok"


def test_min_seconds_between_jobs_defers():
    decision = evaluate_cadence(QUIET, [1000.0], now=1100.0)
    assert decision.allowed is False
    assert decision.reason == "min_seconds_between_jobs"
    assert decision.wait_seconds == 800  # 900 - 100


def test_max_jobs_per_hour_defers():
    # two starts within the last hour, spaced beyond min gap, hits the 2/hour cap
    decision = evaluate_cadence(QUIET, [100.0, 1100.0], now=2200.0)
    assert decision.allowed is False
    assert decision.reason == "max_jobs_per_hour"


def test_hourly_window_slides_and_allows():
    # both prior starts are older than 1h and the min gap has passed -> allowed
    decision = evaluate_cadence(QUIET, [100.0, 1100.0], now=5000.0)
    assert decision.allowed is True


def test_quiet_caps_variants():
    assert effective_variant_cap(QUIET, 100) == (24, True)
    assert effective_variant_cap(QUIET, 10) == (10, False)
    assert effective_variant_cap(QUIET, 0) == (24, True)  # 0 = unlimited -> clamped


def test_night_mode_raises_cap_but_only_when_selected():
    assert effective_variant_cap(NIGHT, 100) == (80, True)
    # default load is quiet, not night
    assert load_resource_policy().max_variants_per_job == 24


def test_cadence_file_roundtrip_and_trim(tmp_path):
    path = cadence_path(tmp_path)
    record_start(path, now=10_000.0)
    record_start(path, now=10_050.0)
    starts = load_recent_starts(path)
    assert starts == [10_000.0, 10_050.0]
    # a much later start trims entries older than the retain window
    record_start(path, now=10_000.0 + 8000)
    trimmed = load_recent_starts(path)
    assert 10_000.0 not in trimmed
    assert (10_000.0 + 8000) in trimmed


def test_load_recent_starts_missing_file(tmp_path):
    assert load_recent_starts(tmp_path / "nope.json") == []
