# -*- coding: utf-8 -*-

import csv
import json
from pathlib import Path

import pytest

from src.research_lab.dashboard_server import default_private_root, render_html
from src.research_lab.dashboard_state import (
    aggregate_runs,
    load_lineage_summary,
    load_completed_runs,
    load_dashboard_state,
    resolve_allowed_path,
)
from src.research_lab.paper_research_status import build_status


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


def test_completed_runs_can_be_limited_for_dashboard_latency(tmp_path):
    _write_run(tmp_path, "20260612_010000_old")
    _write_run(tmp_path, "20260612_020000_mid")
    _write_run(tmp_path, "20260612_030000_new")

    runs = load_completed_runs(tmp_path / "experiments" / "completed", tmp_path, limit=2)

    assert [run["run_id"] for run in runs] == [
        "20260612_030000_new",
        "20260612_020000_mid",
    ]


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
    assert state["product_signal_training"]["schema"] == "product_signal_training_export.v1"
    assert state["product_signal_training"]["exists"] is False
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
    assert "lineage" in state
    assert "pipeline_policy" in state
    assert "provider_routes" in state
    assert "prompt_registry" in state
    assert "validator_taxonomy" in state
    assert "human_feedback" in state
    assert state["prompt_registry"]["schema"] == "PromptRegistry.v1"
    assert state["next_run"].get("allowed") is True  # no prior runs -> allowed


def test_lineage_summary_counts_private_indexes(tmp_path):
    lineage = tmp_path / "state" / "lineage"
    lineage.mkdir(parents=True)
    (lineage / "scanner_events.jsonl").write_text(
        json.dumps({"source": "farm"}) + "\n",
        encoding="utf-8",
    )
    (lineage / "data_packets.jsonl").write_text(
        json.dumps({"timeframe": "15m"}) + "\n",
        encoding="utf-8",
    )
    (lineage / "feature_packets.jsonl").write_text(
        json.dumps({"timeframe": "15m"}) + "\n",
        encoding="utf-8",
    )
    (lineage / "cycle_links.jsonl").write_text(
        json.dumps({"source": "farm"}) + "\n",
        encoding="utf-8",
    )

    summary = load_lineage_summary(tmp_path)

    assert summary["scanner_events"]["rows"] == 1
    assert summary["data_packets"]["by_key"] == {"15m": 1}
    assert summary["feature_packets"]["rows"] == 1
    assert summary["cycle_links"]["by_key"] == {"farm": 1}
    assert summary["execution_allowed"] is False


def test_paper_research_status_is_sanitized(tmp_path):
    lineage = tmp_path / "state" / "lineage"
    lineage.mkdir(parents=True)
    (lineage / "scanner_events.jsonl").write_text(json.dumps({"source": "farm"}) + "\n", encoding="utf-8")
    (lineage / "data_packets.jsonl").write_text(json.dumps({"timeframe": "15m"}) + "\n", encoding="utf-8")
    (lineage / "feature_packets.jsonl").write_text(json.dumps({"timeframe": "15m"}) + "\n", encoding="utf-8")
    (lineage / "cycle_links.jsonl").write_text(json.dumps({"source": "farm"}) + "\n", encoding="utf-8")
    (lineage / "backfill_summary.json").write_text(
        json.dumps({"schema": "LineageBackfillSummary.v1", "rows": 1, "paper_only": True}),
        encoding="utf-8",
    )

    status = build_status(tmp_path)

    assert status["schema"] == "PaperResearchStatus.v1"
    assert status["ready_flags"]["has_data_packets"] is True
    assert status["execution_allowed"] is False
    assert status["secrets_exposed"] is False
    assert status["prompt_registry"]["schema"] == "PromptRegistry.v1"
    assert status["validator_taxonomy"]["schema"] == "ValidatorTaxonomy.v1"
    assert str(tmp_path) not in json.dumps(status)


def test_validator_taxonomy_maps_memory_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    derived.joinpath("setup_outcome_memory.json").write_text(
        json.dumps(
            {
                "schema": "setup_outcome_memory.v1",
                "items": [
                    {"outcome_class": "CONFIRMED_BAD"},
                    {"outcome_class": "WRONG_EXIT"},
                    {"outcome_class": "TACTICAL_1_2_TRADE"},
                    {"lite_status": "REGIME_SPECIFIC"},
                ],
            }
        ),
        encoding="utf-8",
    )

    state = load_dashboard_state(tmp_path)
    by_class = state["validator_taxonomy"]["by_class"]

    assert by_class["confirmed_bad"] == 1
    assert by_class["wrong_exit"] == 1
    assert by_class["tactical_candidate"] == 1
    assert by_class["regime_only"] == 1


def test_dashboard_state_shows_proposal_counts(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    from src.research_lab.proposal_schema import VALIDATED, coerce_proposal
    from src.research_lab.proposal_store import proposals_path, upsert_proposals
    p = coerce_proposal({
        "proposal_id": "p1", "created_by": "rule_based", "hypothesis": "h",
        "requested_timeframe": "15m", "setup_family": "momentum_breakout",
        "symbols": ["SOL_USDT_SWAP"], "parameter_grid": {"momentum_breakout": [{"lookback": 20}]},
        "status": VALIDATED, "created_at": "2026-06-13T00:00:00+00:00", "reason_codes": ["unstable_parameters"],
    })
    upsert_proposals(proposals_path(tmp_path), [p])

    state = load_dashboard_state(tmp_path)
    assert state["proposals"]["total"] == 1
    assert state["proposals"]["validated_waiting"] == 1
    assert state["llm_review"]["auto_send"] is False
    assert state["llm_review"]["queue_requires_apply"] is True
    assert str(tmp_path) not in json.dumps(state)


def test_render_html_shows_proposals_card():
    state = {
        "private_root_label": "strategy-lab",
        "totals": {"run_count": 0, "candidate_count": 0, "decision_counts": {}},
        "state_db": {"queue_counts": {}},
        "proposals": {
            "total": 3, "by_status": {"VALIDATED": 2, "REJECTED": 1}, "validated_waiting": 2,
            "latest_reasons": [{"id": "p1", "status": "VALIDATED", "reasons": ["entry_late"]}],
        },
        "llm_review": {"auto_send": False},
        "llm_cost": {}, "latest_run": {}, "runs": [],
    }
    page = render_html(state)
    assert "Proposals (closed loop)" in page
    assert "validated waiting for queue: 2" in page
    assert "LLM auto-send" in page


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


def test_render_html_shows_farm_cockpit():
    state = {
        "private_root_label": "strategy-lab",
        "obsidian_vault_label": "strategy-lab/obsidian-vault",
        "totals": {"run_count": 0, "candidate_count": 0, "decision_counts": {}},
        "state_db": {"queue_counts": {}},
        "farm_cockpit": {
            "loop_state": {"refill_cursor": 7, "refill_backoff_symbols": 1},
            "data_readiness": {
                "prepared_files_by_timeframe": {"1h": 3, "15m": 1},
                "funding_enrich_status": {"enriched": 2},
                "oi_slot_files": 1,
            },
            "gpu_cpu": {
                "gpu_signal_rows": 4,
                "backends": [{
                    "effective_backend": "gpu", "signal_backend": "gpu",
                    "simulation_backend": "gpu", "gpu_runs": 1, "runs": 1,
                }],
            },
            "results": {
                "unique_symbols": 2, "exported": 1,
                "hard_status": {"VALIDATION_EXPORTED": 1},
                "needs_data": {"NEEDS_OI_DATA": 1},
                "by_group": {"core_market": 2},
            },
            "universe_coverage": {
                "manual": {"groups": 2, "symbols": 5},
                "discovered": {
                    "count": 10, "generated_at": "2026-06-18T00:00:00+00:00",
                    "group_sizes": {"crypto_major": 3},
                },
                "symbols_processed": 2,
                "discovered_not_yet_processed": 8,
            },
        },
        "llm_cost": {},
        "latest_run": {},
        "runs": [],
    }
    page = render_html(state)
    assert "Calculation Farm Cockpit" in page
    assert "funding enrich: enriched: 2" in page
    assert "GPU signal-supported result rows: 4" in page
    assert "core_market: 2" in page


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


def test_dashboard_state_includes_event_microscope_and_llm_gates(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    monkeypatch.delenv("STRATEGY_LAB_LLM_ENABLED", raising=False)
    monkeypatch.delenv("STRATEGY_LAB_LLM_DAILY_CAP", raising=False)

    state = load_dashboard_state(tmp_path)

    em = state["event_microscope"]
    assert em["enabled"] is True  # quiet_desktop allows trigger-only 1m
    assert em["timeframe"] == "1m"
    assert em["limits"]["max_symbols"] == 2
    assert "availability_counts" in em
    llm = state["llm_review"]
    assert llm["provider_configured"] is False
    assert llm["daily_cap_present"] is False
    assert llm["would_send"] == "export_only_env_disabled"
    assert "queue_capacity" in state
    assert str(tmp_path) not in json.dumps(state)


def test_render_html_shows_event_microscope_card():
    state = {
        "private_root_label": "strategy-lab",
        "totals": {"run_count": 0, "candidate_count": 0, "decision_counts": {}},
        "state_db": {"queue_counts": {}},
        "event_microscope": {
            "timeframe": "1m", "enabled": True, "disabled_reason": "",
            "limits": {"max_symbols": 2, "max_event_windows": 3, "max_bars_per_window": 360, "max_variants": 8},
            "availability_counts": {"missing": 2}, "scanned_group": "l2_high_beta",
            "skipped_reasons": [{"symbol": "ZEC_USDT_SWAP", "reason": "no_1m_file"}],
        },
        "queue_capacity": {"max_queue_size": 10, "queued": 0, "full": False},
        "llm_cost": {}, "latest_run": {}, "runs": [],
    }
    page = render_html(state)
    assert "Event Microscope (1m)" in page
    assert "trigger-only" in page
    assert "no_1m_file" in page


def test_dashboard_state_includes_data_prep(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    state = load_dashboard_state(tmp_path)
    assert state["last_prepare_1m"]["available"] is False  # nothing prepared yet
    assert str(tmp_path) not in json.dumps(state)


def test_dashboard_state_includes_prepare_workflow_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr("src.research_lab.dashboard_state.SCOUT_BUDGET_LOG", tmp_path / "missing.jsonl")
    for key in ("STRATEGY_LAB_PREPARE_1M", "STRATEGY_LAB_PREPARE_1M_APPLY", "STRATEGY_LAB_MARKET_DATA_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    state = load_dashboard_state(tmp_path)
    pw = state["prepare_workflow"]
    assert pw["enabled"] is False and pw["mode"] == "disabled"
    assert pw["provider"] == "null" and pw["will_fetch_network"] is False
    assert str(tmp_path) not in json.dumps(state)


def test_render_html_shows_auto_prepare_line():
    state = {
        "private_root_label": "strategy-lab",
        "totals": {"run_count": 0, "candidate_count": 0, "decision_counts": {}},
        "state_db": {"queue_counts": {}},
        "event_microscope": {
            "timeframe": "1m", "enabled": True, "disabled_reason": "",
            "limits": {"max_symbols": 2, "max_event_windows": 3, "max_bars_per_window": 360, "max_variants": 8},
            "availability_counts": {"missing": 2}, "scanned_group": "l2_high_beta", "skipped_reasons": [],
        },
        "prepare_workflow": {"enabled": False, "mode": "disabled", "provider": "null", "will_fetch_network": False},
        "llm_cost": {}, "latest_run": {}, "runs": [],
    }
    page = render_html(state)
    assert "auto-prepare on start" in page
    assert "disabled" in page


def test_render_html_shows_data_prep_line():
    state = {
        "private_root_label": "strategy-lab",
        "totals": {"run_count": 0, "candidate_count": 0, "decision_counts": {}},
        "state_db": {"queue_counts": {}},
        "event_microscope": {
            "timeframe": "1m", "enabled": True, "disabled_reason": "",
            "limits": {"max_symbols": 2, "max_event_windows": 3, "max_bars_per_window": 360, "max_variants": 8},
            "availability_counts": {"missing": 2}, "scanned_group": "l2_high_beta", "skipped_reasons": [],
        },
        "last_prepare_1m": {
            "available": True, "provider": "null", "mode": "dry_run",
            "missing": 1, "would_download": 1, "downloaded": 0, "files_written": 0,
        },
        "llm_cost": {}, "latest_run": {}, "runs": [],
    }
    page = render_html(state)
    assert "1m data prep" in page
    assert "no full-market download" in page


def test_aggregate_runs_counts_decisions():
    totals = aggregate_runs([
        {"candidate_count": 2, "counts": {"REJECT": 1, "OBSERVE": 1}},
        {"candidate_count": 1, "counts": {"REJECT": 1}},
    ])

    assert totals["candidate_count"] == 3
    assert totals["decision_counts"]["REJECT"] == 2
