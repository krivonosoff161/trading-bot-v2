# -*- coding: utf-8 -*-

from argparse import Namespace

from scripts.strategy_lab.research_loop import _llm_step, _synthetic_candidates, run_loop
from src.research_lab.llm_provider import LLMUsage, record_usage
from src.research_lab.research_loop import (
    heartbeat_path,
    loop_report_path,
    loop_summary,
    read_loop_report,
    write_loop_report,
)
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.timeframes import load_timeframe_profiles
from src.research_lab.universe import load_universe


def _loop_args(tmp_path, **over):
    base = dict(dry_run=True, apply=False, duration_minutes=5.0, max_iterations=2,
                sleep_seconds=0, llm_propose=False, prepare_missing_data=False,
                provider="null", max_candidates=3, max_queued=5,
                max_worker_jobs_per_iteration=1, private_root=str(tmp_path),
                allow_public_output=False)
    base.update(over)
    return Namespace(**base)


def _llm_args(**over):
    base = dict(llm_propose=True, apply=True, max_candidates=3, allow_public_output=False)
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


def test_llm_step_daily_cap_blocks_call(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_LLM_ENABLED", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_PROVIDER", "synthetic")
    monkeypatch.setenv("STRATEGY_LAB_ALLOW_SYNTHETIC", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_DAILY_CAP", "1")
    record_usage(tmp_path, LLMUsage(provider="synthetic", model="x", total_tokens=10, cost_rub=5.0))
    universe, profiles, policy = _ctx()
    out = _llm_step(_llm_args(), tmp_path, universe, profiles, policy)
    assert out["status"] == "daily_cap_exhausted"


def test_synthetic_candidates_are_bounded():
    cands = _synthetic_candidates(load_universe())
    assert 1 <= len(cands) <= 2
    assert all(c["requested_timeframe"] == "1d" and c["setup_family"] for c in cands)
