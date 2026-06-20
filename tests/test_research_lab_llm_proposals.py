# -*- coding: utf-8 -*-

import json

from src.research_lab.llm_proposals import (
    build_proposal_prompt,
    chief_review_candidates,
    evaluate_llm_loop_gates,
    load_llm_loop_config,
    parse_llm_proposals,
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
         "symbols": ["BTC_USDT_SWAP"], "parameter_grid": {
             "momentum_breakout": [{"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}]}},
        {"hypothesis": "bad", "setup_family": "nope_family", "requested_timeframe": "1d",
         "symbols": ["BTC_USDT_SWAP"], "parameter_grid": {"nope_family": [{"x": 1}]}},
        {"garbage": "no required fields"},
    ]
    batch = validate_llm_candidates(items, universe=universe, timeframe_profiles=profiles,
                                    resource_policy=policy, created_at=_AT)
    assert len(batch.validated) == 1
    assert len(batch.rejected) == 2


def test_memory_index_rejects_known_bad_candidate():
    from src.research_lab.data_fingerprint import params_hash
    from src.research_lab.setup_outcome_memory import build_gate_index
    universe, profiles, policy = _ctx()
    params = {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}
    idx = build_gate_index([{"symbol": "BTC_USDT_SWAP", "timeframe": "1d", "family": "momentum_breakout",
                             "params_hash": params_hash(params), "data_fingerprint": "fp",
                             "decision": "REJECT", "validation_status": "REJECT", "hard_status": "",
                             "n_trades": 20, "avg_net_pct": -0.5}])
    items = [{"hypothesis": "re-test a known-bad setup", "setup_family": "momentum_breakout",
              "requested_timeframe": "1d", "symbols": ["BTC_USDT_SWAP"],
              "parameter_grid": {"momentum_breakout": [params]}}]
    batch = validate_llm_candidates(items, universe=universe, timeframe_profiles=profiles,
                                    resource_policy=policy, created_at=_AT, memory_index=idx)
    assert len(batch.validated) == 0
    assert batch.reject_reasons().get("known_bad_in_memory") == 1


def test_prompt_includes_memory_digest_when_present():
    universe, profiles, policy = _ctx()
    digest = "OUTCOME MEMORY DIGEST (test): confirmed_bad=99"
    _system, user = build_proposal_prompt("lab state", universe=universe, profiles=profiles,
                                          max_candidates=4, memory_digest=digest)
    assert digest in user
    assert "Do NOT re-propose a setup the outcome memory marks confirmed_bad" in user


def test_parse_accepts_common_wrappers_and_think_prefix():
    text = '<think>draft</think>\n{"candidates": [{"strategy": "momentum_breakout"}]} trailing'
    assert parse_llm_proposals(text) == [{"strategy": "momentum_breakout"}]


def test_validate_normalizes_model_aliases_into_proposal():
    universe, profiles, policy = _ctx()
    items = [{
        "strategy": "momentum_breakout",
        "timeframe": "1d",
        "symbols": "BTC_USDT_SWAP",
        "params": {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
        "rationale": "test breakout timing",
        "expected_failure_mode": "late entry",
    }]
    batch = validate_llm_candidates(items, universe=universe, timeframe_profiles=profiles,
                                    resource_policy=policy, created_at=_AT)
    assert len(batch.validated) == 1
    proposal = batch.validated[0]
    assert proposal.setup_family == "momentum_breakout"
    assert proposal.requested_timeframe == "1d"
    assert proposal.parameter_grid["momentum_breakout"][0]["lookback"] == 20


def test_parse_wrong_shape_gets_specific_error():
    try:
        parse_llm_proposals('{"note": "not a proposal batch"}')
    except ValueError as exc:
        assert str(exc) == "missing_proposals_array"
    else:
        raise AssertionError("expected ValueError")


def test_unsafe_wording_candidate_rejected_not_executed():
    universe, profiles, policy = _ctx()
    items = [{"hypothesis": "guaranteed profit, live trade now", "setup_family": "momentum_breakout",
              "requested_timeframe": "1d", "symbols": ["BTC_USDT_SWAP"],
              "parameter_grid": {"momentum_breakout": [{"lookback": 20, "hold_bars": 5,
                                                        "stop_pct": 8, "take_pct": 16}]}}]
    batch = validate_llm_candidates(items, universe=universe, timeframe_profiles=profiles,
                                    resource_policy=policy, created_at=_AT)
    assert not batch.validated and batch.rejected  # unsafe wording -> rejected


def test_candidate_cap_applied():
    universe, profiles, policy = _ctx()
    items = [
        {"hypothesis": f"re-test {i}", "setup_family": "momentum_breakout", "requested_timeframe": "1d",
         "symbols": ["BTC_USDT_SWAP"], "parameter_grid": {
             "momentum_breakout": [{"lookback": 10 + i, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}]}}
        for i in range(6)
    ]
    batch = validate_llm_candidates(items, universe=universe, timeframe_profiles=profiles,
                                    resource_policy=policy, created_at=_AT, max_candidates=2)
    assert len(batch.validated) == 2  # cheap-model cap


def test_chief_review_capped():
    universe, profiles, policy = _ctx()
    items = [
        {"hypothesis": f"re-test {i}", "setup_family": "momentum_breakout", "requested_timeframe": "1d",
         "symbols": ["BTC_USDT_SWAP"], "parameter_grid": {
             "momentum_breakout": [{"lookback": 10 + i, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}]}}
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


def test_contract_failures_disable_llm_for_current_run():
    universe, profiles, policy = _ctx()
    items = [
        {"garbage": "no required fields"},
        {"hypothesis": "bad family", "setup_family": "nope", "requested_timeframe": "1d",
         "symbols": ["BTC_USDT_SWAP"], "parameter_grid": {"nope": [{"x": 1}]}},
        {"hypothesis": "bad rr", "setup_family": "momentum_breakout", "requested_timeframe": "1d",
         "symbols": ["BTC_USDT_SWAP"], "parameter_grid": {
             "momentum_breakout": [{"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 4}]}},
    ]
    batch = validate_llm_candidates(items, universe=universe, timeframe_profiles=profiles,
                                    resource_policy=policy, created_at=_AT)
    summary = batch.to_summary()
    assert summary["contract_failures"] >= 3
    assert summary["disable_for_run"] is True


def test_llm_prompt_makes_calculator_advisory_not_controller():
    universe, profiles, _policy = _ctx()
    system, user = build_proposal_prompt(
        "latest status: hard_status=NEEDS_MORE_DATA",
        universe=universe,
        profiles=profiles,
        max_candidates=3,
    )
    low = (system + "\n" + user).lower()
    assert "not the controller of the farm" in low
    assert "deterministic code validates" in low
    assert "never start or stop processes" in low
    assert "paper_forward_ready can only be assigned by hard validation" in low
    assert "exactly one top-level key: proposals" in low
    assert "live-trading" in low
