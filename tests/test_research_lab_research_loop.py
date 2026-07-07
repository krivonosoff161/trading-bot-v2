# -*- coding: utf-8 -*-

from argparse import Namespace
from types import SimpleNamespace

import scripts.strategy_lab.research_loop as loop_mod
from scripts.strategy_lab.research_loop import (
    _llm_step,
    _provider_error_payload,
    _sanitize_error_message,
    _synthetic_candidates,
    run_loop,
)
from src.research_lab.llm_provider import LLMProviderError, LLMUsage, record_usage
from src.research_lab.research_loop import (
    heartbeat_path,
    loop_report_path,
    loop_summary,
    read_loop_report,
    write_loop_report,
)
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.state_db import connect, default_db_path, enqueue_experiment, init_db
from src.research_lab.timeframes import load_timeframe_profiles
from src.research_lab.universe import load_universe


def _loop_args(tmp_path, **over):
    base = dict(dry_run=True, apply=False, duration_minutes=5.0, max_iterations=2,
                sleep_seconds=0, llm_propose=False, prepare_missing_data=False,
                provider="null", max_candidates=3, max_queued=5,
                max_worker_jobs_per_iteration=1, private_root=str(tmp_path),
                allow_public_output=False, load_env="", night_mode=False,
                max_llm_contract_failures=3)
    base.update(over)
    return Namespace(**base)


def _llm_args(**over):
    base = dict(llm_propose=True, apply=True, max_candidates=3, max_queued=5, allow_public_output=False)
    base.update(over)
    return Namespace(**base)


def _ctx():
    return load_universe(), load_timeframe_profiles(), load_resource_policy()


# ── report IO ───────────────────────────────────────────────────────────────

def test_loop_summary_unavailable_when_empty():
    assert loop_summary({}) == {"available": False}


def test_loop_report_roundtrip(tmp_path):
    report = {"mode": "dry_run", "iterations": 2, "duration_minutes": 0.1,
              "totals": {"proposals_queued": 0, "llm_validated": 0, "worker_deferred": 1},
              "llm_cost_rub": 0.0, "iteration_log": [{"iteration": 2, "llm": {"status": "disabled"},
              "worker": {"completed": 0, "deferred": 1, "failed": 0}}],
              "next_command": "python -m scripts.strategy_lab.status"}
    write_loop_report(tmp_path, report)
    back = read_loop_report(tmp_path)
    assert back["schema"] == "strategy_lab_research_loop.v1"
    s = loop_summary(back)
    assert s["available"] and s["mode"] == "dry_run" and s["iterations"] == 2
    assert s["last_worker"] == {"completed": 0, "deferred": 1, "failed": 0}


# ── run_loop (bounded; no daemon) ─────────────────────────────────────────────

def test_run_loop_dry_run_caps_iterations_and_writes_only_status(tmp_path):
    report = run_loop(_loop_args(tmp_path, dry_run=True, max_iterations=2))
    assert report["mode"] == "dry_run" and report["iterations"] == 2
    assert loop_report_path(tmp_path).exists() and heartbeat_path(tmp_path).exists()
    # dry-run queues nothing and runs no worker -> no queue specs, no cycle report
    assert not (tmp_path / "proposals" / "queued_specs").exists()
    assert not (tmp_path / "state" / "research_cycle").exists()


def test_run_loop_apply_without_llm_is_safe(tmp_path):
    report = run_loop(_loop_args(tmp_path, dry_run=False, apply=True, max_iterations=1, provider="null"))
    assert report["mode"] == "apply" and report["iterations"] == 1
    assert report["totals"]["llm_validated"] == 0 and report["llm_cost_rub"] == 0.0
    assert report["safety"]["no_paid_llm_default"] is True and report["safety"]["no_live_trading"] is True


def test_run_loop_writes_no_absolute_paths(tmp_path):
    run_loop(_loop_args(tmp_path, max_iterations=1))
    assert str(tmp_path) not in loop_report_path(tmp_path).read_text(encoding="utf-8")
    assert str(tmp_path) not in heartbeat_path(tmp_path).read_text(encoding="utf-8")


# ── LLM step (gated; dry-run never calls; cap blocks) ─────────────────────────

def test_llm_step_disabled_when_flag_off(tmp_path):
    universe, profiles, policy = _ctx()
    out = _llm_step(_llm_args(llm_propose=False), tmp_path, universe, profiles, policy)
    assert out["status"] == "disabled"


def test_llm_step_dry_run_never_calls(tmp_path):
    universe, profiles, policy = _ctx()
    out = _llm_step(_llm_args(apply=False), tmp_path, universe, profiles, policy)
    assert out["status"] == "dry_run_no_call"


def test_llm_step_provider_not_configured_when_env_unset(tmp_path, monkeypatch):
    for k in ("STRATEGY_LAB_LLM_ENABLED", "STRATEGY_LAB_LLM_PROVIDER", "STRATEGY_LAB_ALLOW_SYNTHETIC"):
        monkeypatch.delenv(k, raising=False)
    universe, profiles, policy = _ctx()
    out = _llm_step(_llm_args(), tmp_path, universe, profiles, policy)
    assert out["status"] == "provider_not_configured"
    assert out["retryable"] is False
    assert out["error_message"] == "provider_not_configured"
    assert out["next_action"]


def test_llm_step_skips_when_active_queue_at_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_LLM_ENABLED", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_PROVIDER", "synthetic")
    monkeypatch.setenv("STRATEGY_LAB_ALLOW_SYNTHETIC", "1")
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    enqueue_experiment(conn, tmp_path / "spec.json")
    conn.close()
    universe, profiles, policy = _ctx()

    out = _llm_step(_llm_args(max_candidates=3, max_queued=1), tmp_path, universe, profiles, policy)

    assert out["status"] == "queue_at_cap"
    assert out["active_queue"] == 1


def test_llm_step_synthetic_validates_and_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_LLM_ENABLED", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_PROVIDER", "synthetic")
    monkeypatch.setenv("STRATEGY_LAB_ALLOW_SYNTHETIC", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_DAILY_CAP", "100")
    universe, profiles, policy = _ctx()
    out = _llm_step(_llm_args(), tmp_path, universe, profiles, policy)
    assert out["status"] == "ok" and out["provider"] == "synthetic"
    assert out["validated"] >= 1 and out["cost_rub"] == 0.0
    from src.research_lab.proposal_store import load_proposals, proposals_path
    assert len(load_proposals(proposals_path(tmp_path))) >= 1  # stored


def test_llm_step_ollama_does_not_require_daily_cap(tmp_path, monkeypatch):
    class FakeOllama:
        name = "ollama"
        configured = True

        def generate(self, system, user):
            text = (
                '{"proposals":[{"setup_family":"momentum_breakout",'
                '"requested_timeframe":"1d","symbols":["BTC_USDT_SWAP"],'
                    '"parameter_grid":{"momentum_breakout":[{"lookback":20,"hold_bars":5,'
                    '"stop_pct":8,"take_pct":16}]},'
                '"hypothesis":"bounded local calculator test",'
                '"expected_validation":"reject if fragile","risk_flags":[],"max_variants":1}]}'
            )
            return text, LLMUsage(provider="ollama", model="calculator", total_tokens=100, cost_rub=0.0)

    monkeypatch.setenv("STRATEGY_LAB_LLM_ENABLED", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("STRATEGY_LAB_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_MODEL_CHEAP", "calculator")
    monkeypatch.delenv("STRATEGY_LAB_LLM_DAILY_CAP", raising=False)
    monkeypatch.setattr(loop_mod, "load_provider", lambda **_kwargs: FakeOllama())
    universe, profiles, policy = _ctx()

    out = _llm_step(_llm_args(max_candidates=1), tmp_path, universe, profiles, policy)

    assert out["status"] == "ok" and out["provider"] == "ollama"
    assert out["validated"] == 1 and out["cost_rub"] == 0.0


def test_llm_step_daily_cap_blocks_call(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_LLM_ENABLED", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_PROVIDER", "synthetic")
    monkeypatch.setenv("STRATEGY_LAB_ALLOW_SYNTHETIC", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_DAILY_CAP", "1")
    record_usage(tmp_path, LLMUsage(provider="synthetic", model="x", total_tokens=10, cost_rub=5.0))
    universe, profiles, policy = _ctx()
    out = _llm_step(_llm_args(), tmp_path, universe, profiles, policy)
    assert out["status"] == "daily_cap_exhausted"
    assert out["cost_rub"] == 0.0 and out["spent_today_rub"] == 5.0


def test_run_loop_does_not_add_prior_daily_spend_to_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_LLM_ENABLED", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_PROVIDER", "synthetic")
    monkeypatch.setenv("STRATEGY_LAB_ALLOW_SYNTHETIC", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_DAILY_CAP", "1")
    record_usage(tmp_path, LLMUsage(provider="synthetic", model="x", total_tokens=10, cost_rub=5.0))

    report = run_loop(_loop_args(tmp_path, dry_run=False, apply=True, llm_propose=True, max_iterations=1))

    assert report["llm_cost_rub"] == 0.0
    assert report["iteration_log"][-1]["llm"]["status"] == "daily_cap_exhausted"


def test_run_loop_contract_breaker_stops_repeated_bad_llm_calls(tmp_path, monkeypatch):
    calls = {"llm": 0}

    def bad_llm(*args, **kwargs):
        calls["llm"] += 1
        return {"status": "ok", "validated": 0, "rejected": 1,
                "reject_reasons": {"json_parse_error": 1}, "cost_rub": 0.5}

    def fake_cycle(*args, **kwargs):
        return SimpleNamespace(counts={
            "proposals_queued": 0, "ready_jobs": 1, "skipped_missing_data": 0,
            "worker_completed": 0, "worker_deferred": 0, "worker_failed": 0,
        })

    monkeypatch.setattr(loop_mod, "_llm_step", bad_llm)
    monkeypatch.setattr(loop_mod, "run_research_cycle", fake_cycle)

    report = run_loop(_loop_args(tmp_path, dry_run=False, apply=True, llm_propose=True,
                                 max_iterations=3, max_llm_contract_failures=1))

    assert calls["llm"] == 1
    assert report["totals"]["llm_rejected"] == 1
    assert report["llm_cost_rub"] == 0.5
    assert report["iteration_log"][-1]["llm"]["status"] == "contract_breaker"


def test_synthetic_candidates_are_bounded():
    cands = _synthetic_candidates(load_universe())
    assert 1 <= len(cands) <= 2
    assert all(c["requested_timeframe"] == "1d" and c["setup_family"] for c in cands)


# ── provider-error recovery path (the overnight failure mode) ─────────────────

class _BoomProvider:
    """Provider whose call fails like a dead Ollama (connection refused / timeout)."""

    name = "ollama"
    configured = True
    _model = "calculator"

    def __init__(self, message="ollama request failed: URLError"):
        self._message = message

    def generate(self, system, user):
        raise LLMProviderError(self._message)


def test_llm_step_provider_error_keeps_safe_actionable_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_LLM_ENABLED", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_PROVIDER", "ollama")
    monkeypatch.setattr(loop_mod, "load_provider", lambda **_kw: _BoomProvider())
    universe, profiles, policy = _ctx()

    out = _llm_step(_llm_args(max_candidates=1), tmp_path, universe, profiles, policy)

    assert out["status"] == "error:LLMProviderError"
    assert out["error_type"] == "LLMProviderError"
    assert out["provider"] == "ollama" and out["model"] == "calculator"
    assert out["retryable"] is True  # "request failed" -> transient
    assert out["next_action"] and out["cost_rub"] == 0.0
    # the actual reason is preserved, not collapsed to a bare label
    assert "request failed" in out["error_message"].lower()


def test_provider_error_message_is_sanitized_of_url_and_key():
    leaky = "POST https://host.internal/v1/chat failed bearer sk-SECRET-123 token"
    cleaned = _sanitize_error_message(leaky)
    assert "https://" not in cleaned
    assert "sk-SECRET-123" not in cleaned and "SECRET" not in cleaned
    assert "<url>" in cleaned and "<redacted>" in cleaned


def test_provider_error_payload_marks_non_retryable_config_problem():
    out = _provider_error_payload(LLMProviderError("provider_not_configured"), _BoomProvider())
    assert out["retryable"] is False
    assert "non-retryable" in out["next_action"].lower()


def test_run_loop_provider_breaker_stops_repeated_provider_errors(tmp_path, monkeypatch):
    calls = {"llm": 0}

    def boom_llm(*args, **kwargs):
        calls["llm"] += 1
        return _provider_error_payload(LLMProviderError("ollama request failed: URLError"),
                                       _BoomProvider())

    def fake_cycle(*args, **kwargs):
        return SimpleNamespace(counts={
            "proposals_queued": 0, "ready_jobs": 1, "skipped_missing_data": 0,
            "worker_completed": 0, "worker_deferred": 0, "worker_failed": 0,
        })

    monkeypatch.setattr(loop_mod, "_llm_step", boom_llm)
    monkeypatch.setattr(loop_mod, "run_research_cycle", fake_cycle)

    report = run_loop(_loop_args(tmp_path, dry_run=False, apply=True, llm_propose=True,
                                 max_iterations=5, max_llm_contract_failures=2))

    # 2 consecutive provider errors trip the breaker; the LLM is not called again
    assert calls["llm"] == 2
    assert report["iterations"] == 5  # loop keeps running (draining queue), LLM latched off
    assert report["iteration_log"][-1]["llm"]["status"] == "provider_breaker"
    assert report["iteration_log"][-1]["llm"]["next_action"]


def test_loop_summary_exposes_provider_error_recovery(tmp_path):
    report = {
        "mode": "apply", "iterations": 1, "duration_minutes": 0.1,
        "totals": {"worker_completed": 0},
        "llm_cost_rub": 0.0,
        "iteration_log": [{
            "iteration": 1,
            "llm": {"status": "error:LLMProviderError",
                    "error_message": "ollama request failed: URLError",
                    "retryable": True,
                    "next_action": "transient provider error; loop retries"},
            "worker": {"completed": 0, "deferred": 0, "failed": 0},
        }],
    }
    write_loop_report(tmp_path, report)
    s = loop_summary(read_loop_report(tmp_path))
    assert s["last_llm_status"] == "error:LLMProviderError"
    assert "request failed" in s["last_llm_reason"].lower()
    assert s["last_llm_retryable"] is True
    assert s["last_llm_next_action"]
