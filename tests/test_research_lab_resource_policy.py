# -*- coding: utf-8 -*-

import pytest

from src.research_lab.resource_policy import DEFAULT_PATH, load_resource_policy

VALID = """
schema: strategy_lab_resource_policy.v1
mode: quiet_desktop
max_workers: 1
max_queue_size: 10
autopilot_refill_when_queue_below: 2
autopilot_generate_max: 3
min_seconds_between_jobs: 900
max_jobs_per_hour: 2
allow_heavy_jobs: false
allow_1m_jobs: trigger_only
process_priority: idle_or_below_normal
llm_auto_execute: false
night_mode:
  max_queue_size: 30
  autopilot_generate_max: 8
  min_seconds_between_jobs: 60
  allow_heavy_jobs: true
  allow_1m_jobs: trigger_only
"""


def _write(tmp_path, text):
    path = tmp_path / "rp.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_config_is_quiet():
    policy = load_resource_policy(DEFAULT_PATH)
    assert policy.mode == "quiet_desktop"
    assert policy.max_workers == 1
    assert policy.allow_heavy_jobs is False
    assert policy.llm_auto_execute is False


def test_quiet_mode_gates(tmp_path):
    policy = load_resource_policy(_write(tmp_path, VALID))
    assert policy.allow_heavy_jobs is False
    # trigger_only: only the bounded event microscope is allowed
    assert policy.allows_1m("event_1m") is True
    assert policy.allows_1m("normal") is False


def test_night_mode_overrides_only_listed_keys(tmp_path):
    policy = load_resource_policy(_write(tmp_path, VALID), night_mode=True)
    assert policy.allow_heavy_jobs is True
    assert policy.max_queue_size == 30
    assert policy.min_seconds_between_jobs == 60
    # not listed in night_mode -> unchanged from quiet defaults
    assert policy.max_workers == 1
    assert policy.llm_auto_execute is False
    assert policy.mode == "quiet_desktop"


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_resource_policy(tmp_path / "missing.yaml")


def test_missing_required_key_raises(tmp_path):
    bad = _write(tmp_path, "mode: quiet_desktop\nmax_workers: 1\n")
    with pytest.raises(ValueError):
        load_resource_policy(bad)
