# -*- coding: utf-8 -*-

from src.research_lab.prepare_workflow import load_prepare_workflow_config


def _cfg(**env):
    return load_prepare_workflow_config(env)


def test_defaults_are_safe():
    c = _cfg()
    assert c.enabled is False and c.apply is False and c.provider == "null"
    assert c.mode == "disabled" and c.will_fetch_network is False
    assert c.max_symbols is None and c.max_windows is None and c.warnings == ()


def test_enabled_default_is_dry_run_no_network():
    c = _cfg(STRATEGY_LAB_PREPARE_1M="1")
    assert c.enabled is True and c.apply is False
    assert c.mode == "dry_run" and c.will_fetch_network is False


def test_apply_with_okx_public_fetches_network():
    c = _cfg(STRATEGY_LAB_PREPARE_1M="1", STRATEGY_LAB_PREPARE_1M_APPLY="1",
             STRATEGY_LAB_MARKET_DATA_PROVIDER="okx-public")
    assert c.mode == "apply" and c.will_fetch_network is True


def test_apply_with_synthetic_is_offline():
    c = _cfg(STRATEGY_LAB_PREPARE_1M="1", STRATEGY_LAB_PREPARE_1M_APPLY="1",
             STRATEGY_LAB_MARKET_DATA_PROVIDER="synthetic")
    assert c.mode == "apply" and c.will_fetch_network is False  # synthetic never networks


def test_apply_without_enabled_does_not_fetch():
    c = _cfg(STRATEGY_LAB_PREPARE_1M_APPLY="1", STRATEGY_LAB_MARKET_DATA_PROVIDER="okx-public")
    assert c.enabled is False and c.mode == "disabled" and c.will_fetch_network is False


def test_invalid_provider_falls_back_to_null_with_warning():
    c = _cfg(STRATEGY_LAB_PREPARE_1M="1", STRATEGY_LAB_MARKET_DATA_PROVIDER="binance")
    assert c.provider == "null"
    assert any("MARKET_DATA_PROVIDER" in w for w in c.warnings)


def test_invalid_bool_falls_back_safe_with_warning():
    c = _cfg(STRATEGY_LAB_PREPARE_1M="maybe")
    assert c.enabled is False
    assert any("PREPARE_1M" in w for w in c.warnings)


def test_invalid_max_ints_ignored_with_warning():
    c = _cfg(STRATEGY_LAB_PREPARE_1M="1", STRATEGY_LAB_PREPARE_1M_MAX_SYMBOLS="abc",
             STRATEGY_LAB_PREPARE_1M_MAX_WINDOWS="-3")
    assert c.max_symbols is None and c.max_windows is None
    assert len(c.warnings) >= 2


def test_valid_max_ints_parsed():
    c = _cfg(STRATEGY_LAB_PREPARE_1M="1", STRATEGY_LAB_PREPARE_1M_MAX_SYMBOLS="1",
             STRATEGY_LAB_PREPARE_1M_MAX_WINDOWS="2")
    assert c.max_symbols == 1 and c.max_windows == 2


def test_summary_advertises_safe_default():
    s = _cfg().to_summary()
    assert s["enabled"] is False and s["will_fetch_network"] is False
    assert "auto network fetch disabled by default" in s["note"]
