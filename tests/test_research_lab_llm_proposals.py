# -*- coding: utf-8 -*-

import json

from src.research_lab.llm_proposals import (
    chief_review_candidates,
    evaluate_llm_loop_gates,
    load_llm_loop_config,
    validate_llm_candidates,
)
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.timeframes import load_timeframe_profiles
from src.research_lab.universe import load_universe

_AT = "2026-06-13T00:00:00+00:00"


def _ctx():
    return load_universe(), load_timeframe_profiles(), load_resource_policy()


def test_loop_config_default_is_disabled():
    c = load_llm_loop_config({})
    assert c.enabled is False and c.provider_configured is False
    assert c.to_summary()["mode"] == "disabled"


def test_loop_config_enabled_without_provider_is_export_only():
    c = load_llm_loop_config({"STRATEGY_LAB_LLM_ENABLED": "1"})
    assert c.enabled is True and c.provider_configured is False
    assert c.to_summary()["mode"] == "export_only"


def test_send_blocked_when_disabled():
    c = load_llm_loop_config({})
    assert evaluate_llm_loop_gates(c, send_requested=True, dry_run=False).allowed is False


def test_send_blocked_no_provider_even_if_enabled_and_capped():
    c = load_llm_loop_config({"STRATEGY_LAB_LLM_ENABLED": "1", "STRATEGY_LAB_LLM_DAILY_CAP": "1"})
    d = evaluate_llm_loop_gates(c, send_requested=True, dry_run=False)
    assert d.allowed is False  # provider_configured is False (no client ships)


def test_dry_run_never_sends():
    c = load_llm_loop_config({"STRATEGY_LAB_LLM_ENABLED": "1", "STRATEGY_LAB_LLM_PROVIDER": "alibaba",
                              "STRATEGY_LAB_LLM_DAILY_CAP": "1"})
    d = evaluate_llm_loop_gates(c, send_requested=True, dry_run=True)
    assert d.allowed is False and d.reason == "dry_run_active"


def test_validate_candidates_accepts_valid_rejects_invalid():
    universe, profiles, policy = _ctx()
    items = [
        {"hypothesis": "neighbor re-test", "setup_family": "momentum_breakout", "requested_timeframe": "1d",
         "symbols": ["BTC_USDT_SWAP"], "parameter_grid": {"momentum_breakout": [{"lookback": 20}]}},
        {"hypothesis": "bad", "setup_family": "nope_family", "requested_timeframe": "1d",
         "symbols": ["BTC_USDT_SWAP"], "parameter_grid": {"nope_family": [{"x": 1}]}},
        {"garbage": "no required fields"},
    ]
    batch = validate_llm_candidates(items, universe=universe, timeframe_profiles=profiles,
                                    resource_policy=policy, created_at=_AT)
    assert len(batch.validated) == 1
    assert len(batch.rejected) == 2


def test_unsafe_wording_candidate_rejected_not_executed():
    universe, profiles, policy = _ctx()
    items = [{"hypothesis": "guaranteed profit, live trade now", "setup_family": "momentum_breakout",
              "requested_timeframe": "1d", "symbols": ["BTC_USDT_SWAP"],
              "parameter_grid": {"momentum_breakout": [{"lookback": 20}]}}]
    batch = validate_llm_candidates(items, universe=universe, timeframe_profiles=profiles,
                                    resource_policy=policy, created_at=_AT)
    assert not batch.validated and batch.rejected  # unsafe wording -> rejected


def test_candidate_cap_applied():
    universe, profiles, policy = _ctx()
    items = [
        {"hypothesis": f"re-test {i}", "setup_family": "momentum_breakout", "requested_timeframe": "1d",
         "symbols": ["BTC_USDT_SWAP"], "parameter_grid": {"momentum_breakout": [{"lookback": 10 + i}]}}
        for i in range(6)
    ]
    batch = validate_llm_candidates(items, universe=universe, timeframe_profiles=profiles,
                                    resource_policy=policy, created_at=_AT, max_candidates=2)
    assert len(batch.validated) == 2  # cheap-model cap


def test_chief_review_capped():
    universe, profiles, policy = _ctx()
    items = [
        {"hypothesis": f"re-test {i}", "setup_family": "momentum_breakout", "requested_timeframe": "1d",
         "symbols": ["BTC_USDT_SWAP"], "parameter_grid": {"momentum_breakout": [{"lookback": 10 + i}]}}
        for i in range(5)
    ]
    batch = validate_llm_candidates(items, universe=universe, timeframe_profiles=profiles,
                                    resource_policy=policy, created_at=_AT, max_candidates=10)
    cfg = load_llm_loop_config({}, max_reviews=2)
    assert len(chief_review_candidates(batch, cfg)) == 2


def test_summary_stores_no_key_values():
    c = load_llm_loop_config({"STRATEGY_LAB_LLM_PROVIDER": "alibaba"})
    blob = json.dumps(c.to_summary()).lower()
    assert "alibaba" in blob  # provider NAME is fine
    for forbidden in ("secret", "api_key", "apikey", "passphrase", "token", "bearer"):
        assert forbidden not in blob
