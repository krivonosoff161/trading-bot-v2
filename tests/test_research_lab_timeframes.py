# -*- coding: utf-8 -*-

import pytest

from src.research_lab.timeframes import DEFAULT_PATH, load_timeframe_profiles

VALID = """
schema: strategy_lab_timeframe_profiles.v1
profiles:
  1d:
    role: regime
    default_use: context
    max_symbols_per_cycle: 20
    max_variants_per_setup: 6
  1m:
    role: event_microstructure
    default_use: microscope
    trigger_only: true
    max_symbols_per_cycle: 2
    max_window_hours: 6
    max_variants_per_setup: 8
"""


def _write(tmp_path, text):
    path = tmp_path / "tf.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_config_loads():
    profiles = load_timeframe_profiles(DEFAULT_PATH)
    assert set(profiles.names()) >= {"1d", "1h", "15m", "1m"}
    assert profiles.get("1m").trigger_only is True
    assert profiles.get("15m").role == "entry"


def test_case_insensitive_lookup(tmp_path):
    profiles = load_timeframe_profiles(_write(tmp_path, VALID))
    assert profiles.get("1D").role == "regime"
    assert profiles.get("1d").max_window_hours is None
    assert profiles.get("1m").max_window_hours == 6


def test_unknown_timeframe_raises(tmp_path):
    profiles = load_timeframe_profiles(_write(tmp_path, VALID))
    with pytest.raises(ValueError):
        profiles.get("4h")


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_timeframe_profiles(tmp_path / "missing.yaml")


def test_missing_required_key_raises(tmp_path):
    bad = _write(tmp_path, "profiles:\n  1d:\n    role: regime\n")
    with pytest.raises(ValueError):
        load_timeframe_profiles(bad)


def test_empty_profiles_raises(tmp_path):
    bad = _write(tmp_path, "profiles: {}\n")
    with pytest.raises(ValueError):
        load_timeframe_profiles(bad)
