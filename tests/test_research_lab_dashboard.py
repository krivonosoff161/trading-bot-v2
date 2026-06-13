# -*- coding: utf-8 -*-

import csv
import json
from pathlib import Path

import pytest

from src.research_lab.dashboard_server import default_private_root, render_html
from src.research_lab.dashboard_state import (
    aggregate_runs,
    load_completed_runs,
    load_dashboard_state,
    resolve_allowed_path,
)


def _write_run(root: Path, name: str) -> Path:
    run = root / "experiments" / "completed" / name
    run.mkdir(parents=True)
    (run / "metrics.json").write_text(
        json.dumps({"experiment_id": "demo", "created_at": "2026-06-12T00:00:00Z"}),
        encoding="utf-8",
    )
    with (run / "candidates.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "run_id",
                "symbol",
                "family",
                "decision",
                "reasons",
                "n_trades",
                "win_rate",
                "avg_net_pct",
                "total_net_pct",
                "profit_factor",
                "max_drawdown_pct",
                "test_avg_net_pct",
                "best_trade_share",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "run_id": "abc",
                "symbol": "BTC_USDT_SWAP",
                "family": "momentum_breakout",
                "decision": "PROMOTE_FOR_PRESSURE_TEST",
                "reasons": "passed_basic_gates",
                "n_trades": "22",
                "win_rate": "0.5",
                "avg_net_pct": "1.2",
                "total_net_pct": "26.4",
                "profit_factor": "1.3",
                "max_drawdown_pct": "8",
                "test_avg_net_pct": "0.7",
                "best_trade_share": "0.2",
            }
        )
    (run / "summary.md").write_text("# Summary\n", encoding="utf-8")
    (run / "llm_review_prompt.md").write_text("# Prompt\n", encoding="utf-8")
    return run


def test_resolve_allowed_path_rejects_traversal(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "ok.txt"
    inside.write_text("ok", encoding="utf-8")

    assert resolve_allowed_path(inside, [root]) == inside.resolve()
    with pytest.raises(ValueError):
        resolve_allowed_path(tmp_path / "outside.txt", [root])


def test_dashboard_state_loads_completed_runs(tmp_path, monkeypatch):
    _write_run(tmp_path, "20260612_010000_demo")
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    monkeypatch.setattr("src.research_lab.dashboard_state.DEFAULT_PRIVATE_ROOT", tmp_path)

    state = load_dashboard_state(tmp_path)

    assert state["totals"]["run_count"] == 1
    assert state["totals"]["decision_counts"]["PROMOTE_FOR_PRESSURE_TEST"] == 1
    assert state["latest_run"]["top_candidates"][0]["symbol"] == "BTC_USDT_SWAP"
    assert state["candidate_registry"]["exists"] is False
    assert state["candidate_registry"]["registry_label"].startswith("strategy-lab/")
    assert str(tmp_path) not in json.dumps(state)


def test_dashboard_default_private_root_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_BOT_RESEARCH_ROOT", str(tmp_path))

    assert default_private_root() == tmp_path


def test_completed_runs_sort_newest_first(tmp_path):
    _write_run(tmp_path, "20260612_010000_old")
    _write_run(tmp_path, "20260612_020000_new")

    runs = load_completed_runs(tmp_path / "experiments" / "completed", tmp_path)

    assert runs[0]["run_id"] == "20260612_020000_new"


def test_render_html_escapes_candidate_fields():
    state = {
        "private_root_label": "strategy-lab",
        "obsidian_vault_label": "strategy-lab/obsidian-vault",
        "totals": {"run_count": 1, "candidate_count": 1, "decision_counts": {"PROMOTE_FOR_PRESSURE_TEST": 1}},
        "llm_cost": {"today_rub": 0},
        "latest_run": {
            "run_id": "<bad>",
            "experiment_id": "demo",
            "candidate_count": 1,
            "counts": {"PROMOTE_FOR_PRESSURE_TEST": 1},
            "top_candidates": [
                {
                    "run_id": "x",
                    "symbol": "<script>",
                    "family": "f",
                    "decision": "PROMOTE_FOR_PRESSURE_TEST",
                    "avg_net_pct": "1",
                    "test_avg_net_pct": "1",
                    "profit_factor": "1",
                    "reasons": "ok",
                }
            ],
        },
        "runs": [],
    }

    page = render_html(state)

    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_render_html_shows_validation_and_registry():
    state = {
        "private_root_label": "strategy-lab",
        "obsidian_vault_label": "strategy-lab/obsidian-vault",
        "totals": {"run_count": 1, "candidate_count": 1, "decision_counts": {}},
        "state_db": {"validation_counts": {"FORWARD_PAPER": 2, "REJECT": 5}},
        "candidate_registry": {
            "exists": True,
            "entries": 7,
            "by_validation_status": {"FORWARD_PAPER": 2, "REJECT": 5},
            "registry_label": "strategy-lab/candidate-registry/candidates.jsonl",
        },
        "llm_cost": {},
        "latest_run": {},
        "runs": [],
    }

    page = render_html(state)

    assert "Forward paper" in page
    assert "Candidate Registry" in page
    assert "candidates.jsonl" in page


def test_dashboard_state_includes_worker_and_llm_sections(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    monkeypatch.delenv("STRATEGY_LAB_LLM_ENABLED", raising=False)
    # persist a worker status as the worker would
    from src.research_lab.runtime_policy import worker_status_path, write_worker_status
    write_worker_status(worker_status_path(tmp_path), status="deferred", reason="max_jobs_per_hour", mode="quiet_desktop")

    state = load_dashboard_state(tmp_path)

    assert state["worker_status"]["status"] == "deferred"
    assert state["llm_review"]["enabled"] is False
    assert state["llm_review"]["auto_execute"] is False
    assert state["lab_config"]["resource_mode"] == "quiet_desktop"
    assert str(tmp_path) not in json.dumps(state)


def test_dashboard_state_handles_missing_private_root(tmp_path):
    missing = tmp_path / "does_not_exist"
    state = load_dashboard_state(missing)
    assert state["worker_status"] == {}
    assert state["totals"]["run_count"] == 0
    assert "llm_review" in state
    assert state["obsidian_notes"] == 0
    assert "next_run" in state


def test_dashboard_state_has_research_summary_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    state = load_dashboard_state(tmp_path)
    assert "obsidian_notes" in state
    assert "next_run" in state
    assert state["next_run"].get("allowed") is True  # no prior runs -> allowed


def test_render_html_shows_research_summary():
    state = {
        "private_root_label": "strategy-lab",
        "obsidian_vault_label": "strategy-lab/obsidian-vault",
        "totals": {"run_count": 1, "candidate_count": 1, "decision_counts": {}},
        "state_db": {"queue_counts": {}},
        "obsidian_notes": 4,
        "next_run": {"allowed": False, "reason": "min_seconds_between_jobs", "wait_seconds": 600},
        "latest_run": {
            "reducer_verdicts": {"FORWARD_PAPER": 2, "REJECT": 3},
            "entry_timing": {"avg_capture_ratio": 0.4, "avg_mfe_pct": 5.0, "avg_mae_pct": 3.0, "late_entry_rate": 0.2},
        },
        "llm_cost": {},
        "runs": [],
    }
    page = render_html(state)
    assert "Research Summary" in page
    assert "FORWARD_PAPER" in page
    assert "Obsidian candidate notes" in page
    assert "deferred" in page


def test_render_html_shows_worker_health_and_llm():
    state = {
        "private_root_label": "strategy-lab",
        "obsidian_vault_label": "strategy-lab/obsidian-vault",
        "totals": {"run_count": 0, "candidate_count": 0, "decision_counts": {}},
        "state_db": {"queue_counts": {"queued": 3, "running": 1, "completed": 5, "failed": 2}},
        "worker_status": {"status": "deferred", "reason": "min_seconds_between_jobs", "updated_at": "2026-06-13T00:00:00Z"},
        "llm_review": {"enabled": False, "note": "export-only; no automatic API call or spend"},
        "llm_cost": {},
        "latest_run": {},
        "runs": [],
    }

    page = render_html(state)

    assert "Worker &amp; Queue Health" in page
    assert "deferred" in page
    assert "LLM review" in page
    assert "disabled" in page


def test_aggregate_runs_counts_decisions():
    totals = aggregate_runs([
        {"candidate_count": 2, "counts": {"REJECT": 1, "OBSERVE": 1}},
        {"candidate_count": 1, "counts": {"REJECT": 1}},
    ])

    assert totals["candidate_count"] == 3
    assert totals["decision_counts"]["REJECT"] == 2
