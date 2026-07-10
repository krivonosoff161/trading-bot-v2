from __future__ import annotations

import json
import time

from scripts import project_snapshot


def test_classify_canonical_farm_paper_loop() -> None:
    cmd = (
        "python -m scripts.strategy_lab.farm_loop --apply --loop "
        "--run-worker --run-validation --run-paper --run-paper-signals"
    )

    assert project_snapshot.classify_process(cmd) == "canonical_farm_paper_loop"


def test_classify_main_engine() -> None:
    assert project_snapshot.classify_process(r"python C:\repo\main.py") == "main_engine"


def test_bot_status_ignores_unrelated_python() -> None:
    report = project_snapshot.bot_status(
        [
            {"ProcessId": 1, "CommandLine": "python scripts/project_snapshot.py"},
            {"ProcessId": 2, "CommandLine": "python -m pytest tests/test_x.py"},
            {"ProcessId": 3, "CommandLine": "python -m pytest tests/test_paper_telegram_sender.py -q"},
        ]
    )

    assert report["relevant"] == []
    assert report["ignored_python"] == 3
    assert report["by_kind"] == {}


def test_bot_status_counts_only_relevant_processes() -> None:
    report = project_snapshot.bot_status(
        [
            {
                "ProcessId": 10,
                "CommandLine": (
                    "python -m scripts.strategy_lab.farm_loop --apply "
                    "--run-paper-signals --run-paper"
                ),
            },
            {"ProcessId": 11, "CommandLine": "python -m scripts.strategy_lab.paper_signals_run --status"},
            {"ProcessId": 12, "CommandLine": "python scripts/project_snapshot.py"},
        ]
    )

    assert report["ignored_python"] == 1
    assert report["by_kind"] == {
        "canonical_farm_paper_loop": 1,
        "paper_signals_runner": 1,
    }
    assert [row["pid"] for row in report["relevant"]] == [10, 11]


def test_bot_status_recovers_farm_loop_from_fresh_status(tmp_path, monkeypatch) -> None:
    now = time.time()
    state = tmp_path / "state"
    state.mkdir()
    (state / "farm_loop_status.json").write_text(
        json.dumps({
            "pid": 12345,
            "stage": "sleep",
            "updated_at": now,
            "cycle_started_at": now - 12,
            "paper_only": True,
            "execution_allowed": False,
            "loop": True,
            "details": {"sleep_seconds": 600},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADING_BOT_RESEARCH_ROOT", str(tmp_path))
    monkeypatch.setattr(project_snapshot, "python_processes", lambda: [])
    monkeypatch.setattr(project_snapshot, "_pid_exists", lambda pid: pid == 12345)

    report = project_snapshot.bot_status()

    assert report["by_kind"] == {"canonical_farm_paper_loop": 1}
    assert report["relevant"][0]["pid"] == 12345
    assert report["farm_status"]["stage"] == "sleep"
    assert report["farm_status"]["paper_only"] is True
    assert report["farm_status"]["execution_allowed"] is False
    assert report["farm_status"]["details"] == {"sleep_seconds": 600}


def test_farm_loop_status_snapshot_reports_stage_freshness(tmp_path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "farm_loop_status.json").write_text(
        json.dumps({
            "pid": 123,
            "stage": "paper_signals",
            "updated_at": 1_000.0,
            "cycle_started_at": 900.0,
            "loop": True,
            "paper_only": True,
            "execution_allowed": False,
            "details": {"max_pfr_scan": 30},
        }),
        encoding="utf-8",
    )

    status = project_snapshot._farm_loop_status_snapshot(tmp_path, now=1_050.0)

    assert status == {
        "pid": 123,
        "stage": "paper_signals",
        "updated_age_seconds": 50,
        "cycle_age_seconds": 150,
        "loop": True,
        "paper_only": True,
        "execution_allowed": False,
        "details": {"max_pfr_scan": 30},
    }


def test_outcome_retest_status_separates_current_from_historical_invalid(tmp_path) -> None:
    from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path

    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    fresh_spec = {
        "retest_id": "fresh_retest",
        "queueable": True,
        "sweep_spec": {"sweep_id": "fresh", "max_variants": 8},
    }
    catalog_path = derived / "outcome_retest_specs.json"
    catalog_path.write_text(
        json.dumps({
            "specs": 2,
            "queueable": 2,
            "items": [
                fresh_spec,
                {
                    "retest_id": "queued_retest",
                    "queueable": True,
                    "sweep_spec": {"sweep_id": "queued", "max_variants": 8},
                },
            ],
        }),
        encoding="utf-8",
    )
    tasks = FarmTasksDB(tasks_db_path(tmp_path))
    tasks.enqueue_task(
        task_type="schedule_retest",
        task_key="fresh",
        source_event_id="fresh_retest",
        payload={"retest_spec": fresh_spec},
        state="skipped",
        machine_reason="invalid_retest_spec:variant grid exceeds max_variants",
        now=10.0,
    )
    tasks.enqueue_task(
        task_type="schedule_retest",
        task_key="old",
        source_event_id="old_retest",
        payload={"retest_spec": {"retest_id": "old_retest", "queueable": True}},
        state="skipped",
        machine_reason="invalid_retest_spec:old invalid grid",
        now=1.0,
    )
    tasks.enqueue_task(
        task_type="run_sweep",
        task_key="run",
        state="completed",
        payload={"origin": "outcome_retest"},
        now=1.0,
    )

    status = project_snapshot._outcome_retest_status(tmp_path)

    assert status["catalog_specs"] == 2
    assert status["catalog_queueable"] == 2
    assert status["invalid_retest"] == 2
    assert status["invalid_retest_current"] == 1
    assert status["invalid_retest_historical"] == 1
    assert status["catalog_current_scheduled"] == 1
    assert status["catalog_current_run_sweep"] == 0
    assert status["catalog_current_unscheduled"] == 1
    assert status["invalid_current_reasons"] == {"variant grid exceeds max_variants": 1}
    assert status["invalid_reasons"] == {
        "old invalid grid": 1,
        "variant grid exceeds max_variants": 1,
    }
    assert status["run_sweep_outcome_retest"] == {"completed": 1}


def test_paper_product_status_reads_only_aggregate_private_snapshots(tmp_path) -> None:
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    (derived / "paper_signals.json").write_text(
        json.dumps({"total": 3, "by_status": {"armed": 2, "reviewed": 1}}),
        encoding="utf-8",
    )
    (derived / "main_paper_instructions.json").write_text(
        json.dumps({"instructions": 1, "skipped_unvalidated": 2, "execution_allowed": False}),
        encoding="utf-8",
    )
    (derived / "main_paper_consumed.json").write_text(
        json.dumps({"accepted": 1, "rejected": 0, "execution_allowed": False}),
        encoding="utf-8",
    )
    (derived / "main_paper_runtime_queue.json").write_text(
        json.dumps({"queued": 1, "execution_allowed": False, "items": [{"secret_like": "not surfaced"}]}),
        encoding="utf-8",
    )
    (derived / "main_paper_runtime_observation.json").write_text(
        json.dumps({"observed": 1, "reviewed": 0, "pending": 1, "provider_error": 0}),
        encoding="utf-8",
    )
    (derived / "main_paper_trades.json").write_text(
        json.dumps({"trades": 1, "by_status": {"armed": 1}, "execution_allowed": False}),
        encoding="utf-8",
    )
    (derived / "paper_product_trades.json").write_text(
        json.dumps({
            "trades": 3,
            "live_ready": 2,
            "live_blocked": 1,
            "active_trades": 1,
            "active_live_ready": 0,
            "active_live_blocked": 1,
            "active_by_source": {"farm": 1},
            "active_by_family": {"continuation": 1},
            "by_status": {"armed": 1, "reviewed": 2},
            "by_live_block": {"missing_ready_strategy_id": 1},
            "execution_allowed": False,
        }),
        encoding="utf-8",
    )
    (derived / "paper_telegram_preview.json").write_text(
        json.dumps({
            "rendered": 1,
            "skipped_quality_gate": 2,
            "quality_gate_reasons": {"quality_label:needs_review": 2},
            "execution_allowed": False,
        }),
        encoding="utf-8",
    )
    (derived / "paper_telegram_delivery.json").write_text(
        json.dumps({
            "eligible": 1,
            "eligible_cards": 1,
            "sent": 0,
            "sent_messages": 0,
            "sent_cards": 0,
            "duplicates": 1,
            "duplicate_messages": 1,
            "duplicate_cards": 1,
            "errors": 0,
            "error_messages": 0,
            "error_cards": 0,
            "target_recipients": 2,
            "dry_run": True,
            "configured": True,
            "sends_network": False,
            "execution_allowed": False,
        }),
        encoding="utf-8",
    )
    (derived / "paper_telegram_sent_keys.json").write_text(
        json.dumps({
            "schema": "paper_telegram_sent_keys.v1",
            "sent_keys": [
                "preview_1:recipient_a",
                "preview_1:recipient_b",
                "preview_2:recipient_a",
            ],
        }),
        encoding="utf-8",
    )
    (derived / "paper_signal_training.json").write_text(
        json.dumps({
            "schema": "paper_signal_training_export.v2",
            "rows": 4,
            "by_result": {"take": 2, "stop": 1, "expired_no_entry": 1},
            "by_family": {"early_tp_tactical": 3, "continuation": 1},
            "by_diagnosis": {"good_signal": 2, "wrong_direction": 1},
            "execution_allowed": False,
        }),
        encoding="utf-8",
    )
    (derived / "paper_signal_training.jsonl").write_text(
        json.dumps({
            "schema": "TrainingRow.v2",
            "training_row_id": "training_s1",
            "outcome_review_id": "llmr_1",
            "outcome_learning_review_kind": "loss",
            "outcome_learning_bucket": "gave_back",
            "paper_only": True,
            "execution_allowed": False,
        })
        + "\n"
        + json.dumps({
            "schema": "TrainingRow.v2",
            "training_row_id": "training_s2",
            "outcome_review_id": "",
            "paper_only": True,
            "execution_allowed": False,
        })
        + "\n",
        encoding="utf-8",
    )
    llm_advice = tmp_path / "state" / "llm_advice"
    llm_advice.mkdir(parents=True, exist_ok=True)
    (llm_advice / "outcome_reviews.jsonl").write_text(
        json.dumps({
            "schema": "OutcomeReview.v1",
            "review_id": "llmr_1",
            "role_id": "outcome_reviewer",
            "source_ref": "training_s1",
            "accepted": True,
            "payload": {"review_kind": "loss", "outcome_bucket": "gave_back"},
            "paper_only": True,
            "execution_allowed": False,
        })
        + "\n"
        + json.dumps({
            "schema": "OutcomeReview.v1",
            "review_id": "llmr_2",
            "role_id": "outcome_reviewer",
            "source_ref": "training_s2",
            "accepted": False,
            "payload": {},
            "paper_only": True,
            "execution_allowed": False,
        })
        + "\n",
        encoding="utf-8",
    )
    (derived / "paper_product_quality_report.json").write_text(
        json.dumps({
            "schema": "paper_product_quality_report.v1",
            "operator_action": "fix_promotion_gap_missing_ready_strategy_id",
            "quality_labels": {"candidate_watch": 1, "needs_review": 2},
            "geometry_profiles": [
                {
                    "profile_id": "base",
                    "rows": 12,
                    "take_rate": 0.5,
                    "avg_net_r": 0.1,
                },
                {
                    "profile_id": "faster_capture",
                    "rows": 10,
                    "take_rate": 0.4,
                    "avg_net_r": -0.2,
                },
            ],
            "active_signal_lifecycle": {
                "active": 3,
                "pending_outcomes": 2,
                "oldest_age_hours": 22.5,
                "next_expiry_hours": 0.5,
                "overdue_expiry": 0,
                "expiry_buckets": {"le_1h": 1, "le_3h": 2},
            },
            "lifecycle_integrity": {
                "schema": "paper_signal_lifecycle_integrity.v1",
                "v2_rows": 12,
                "entry_expired_contradictions": 0,
                "negative_bars_held": 0,
                "valid": True,
            },
            "pfr_funnel": {
                "catalog_ready": 43,
                "catalog_rejected_quality": 10,
                "bridge_instructions": 0,
                "bridge_skip_reasons": {"missing_ready_strategy_id": 1},
                "last_cycle_pfr_counts": {"pfr_rejected:no_breakout": 6},
                "near_trigger_counts": {"pfr_near_trigger:breakout_gap_le_1pct": 1},
                "cycle_resource_reasons": {"stale_data": 1},
            },
            "pfr_trigger_state": {
                "state": "waiting_for_live_trigger",
                "catalog_ready": 43,
                "bridge_instructions": 0,
                "bridge_validated_instructions": 0,
                "last_cycle_generated": 3,
                "last_cycle_pfr_generated": 0,
                "top_reasons": {"pfr_rejected:no_breakout": 6},
            },
            "execution_allowed": False,
        }),
        encoding="utf-8",
    )

    status = project_snapshot.paper_product_status(tmp_path)

    assert status["active"] is True
    assert status["paper_total"] == 3
    assert status["instructions"] == 1
    assert status["queued"] == 1
    assert status["trades"] == 1
    assert status["product_trades"] == 3
    assert status["product_live_ready"] == 2
    assert status["product_active_trades"] == 1
    assert status["product_active_live_ready"] == 0
    assert status["product_active_live_blocked"] == 1
    assert status["product_active_by_source"] == {"farm": 1}
    assert status["product_active_by_family"] == {"continuation": 1}
    assert status["delivery_dry_run"] is True
    assert status["sends_network"] is False
    assert status["preview_skipped_quality_gate"] == 2
    assert status["preview_quality_gate_reasons"] == {"quality_label:needs_review": 2}
    assert status["delivery_duplicates"] == 1
    assert status["delivery_duplicate_cards"] == 1
    assert status["delivery_targets"] == 2
    assert status["cumulative_sent_keys"] == 3
    assert status["cumulative_sent_previews"] == 2
    assert status["cumulative_sent_recipients"] == 2
    assert status["training_rows"] == 4
    assert status["training_by_result"] == {"take": 2, "expired_no_entry": 1, "stop": 1}
    assert status["training_by_family"] == {"early_tp_tactical": 3, "continuation": 1}
    assert status["training_by_diagnosis"] == {"good_signal": 2, "wrong_direction": 1}
    assert status["outcome_review_rows"] == 2
    assert status["outcome_review_accepted"] == 1
    assert status["outcome_review_rejected"] == 1
    assert status["training_outcome_review_linked"] == 1
    assert status["training_learning_kind"] == {"loss": 1}
    assert status["training_learning_bucket"] == {"gave_back": 1}
    assert status["outcome_gate_verdicts"] == 1
    assert status["outcome_gate_by_stage"] == {"review_only": 1}
    assert status["quality_operator_action"] == "fix_promotion_gap_missing_ready_strategy_id"
    assert status["quality_labels"] == {"needs_review": 2, "candidate_watch": 1}
    assert status["geometry_profiles"] == [
        {"profile_id": "base", "rows": 12, "take_rate": 0.5, "avg_net_r": 0.1},
        {"profile_id": "faster_capture", "rows": 10, "take_rate": 0.4, "avg_net_r": -0.2},
    ]
    assert status["active_lifecycle"] == {
        "active": 3,
        "pending_outcomes": 2,
        "oldest_age_hours": 22.5,
        "next_expiry_hours": 0.5,
        "overdue_expiry": 0,
        "expiry_buckets": {"le_1h": 1, "le_3h": 2},
    }
    assert status["lifecycle_integrity"] == {
        "schema": "paper_signal_lifecycle_integrity.v1",
        "v2_rows": 12,
        "entry_expired_contradictions": 0,
        "negative_bars_held": 0,
        "valid": True,
    }
    assert status["pfr_funnel"] == {
        "catalog_ready": 43,
        "catalog_rejected_quality": 10,
        "bridge_instructions": 0,
        "bridge_skip_reasons": {"missing_ready_strategy_id": 1},
        "last_cycle_pfr_counts": {"pfr_rejected:no_breakout": 6},
        "near_trigger_counts": {"pfr_near_trigger:breakout_gap_le_1pct": 1},
        "cycle_resource_reasons": {"stale_data": 1},
    }
    assert status["pfr_trigger_state"] == {
        "state": "waiting_for_live_trigger",
        "catalog_ready": 43,
        "bridge_instructions": 0,
        "bridge_validated_instructions": 0,
        "last_cycle_generated": 3,
        "last_cycle_pfr_generated": 0,
        "top_reasons": {"pfr_rejected:no_breakout": 6},
    }
    assert status["quality_report_exists"] is True
    assert status["execution_allowed"] is False
    assert "items" not in status
