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
    state = tmp_path / "state"
    state.mkdir()
    (state / "farm_loop_status.json").write_text(
        json.dumps({
            "pid": 12345,
            "stage": "sleep",
            "updated_at": time.time(),
            "paper_only": True,
            "execution_allowed": False,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRADING_BOT_RESEARCH_ROOT", str(tmp_path))
    monkeypatch.setattr(project_snapshot, "python_processes", lambda: [])
    monkeypatch.setattr(project_snapshot, "_pid_exists", lambda pid: pid == 12345)

    report = project_snapshot.bot_status()

    assert report["by_kind"] == {"canonical_farm_paper_loop": 1}
    assert report["relevant"][0]["pid"] == 12345


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
            "by_status": {"armed": 1, "reviewed": 2},
            "by_live_block": {"missing_ready_strategy_id": 1},
            "execution_allowed": False,
        }),
        encoding="utf-8",
    )
    (derived / "paper_telegram_preview.json").write_text(
        json.dumps({"rendered": 1, "execution_allowed": False}),
        encoding="utf-8",
    )
    (derived / "paper_telegram_delivery.json").write_text(
        json.dumps({
            "eligible": 1,
            "sent": 0,
            "duplicates": 1,
            "errors": 0,
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
    assert status["delivery_dry_run"] is True
    assert status["sends_network"] is False
    assert status["delivery_duplicates"] == 1
    assert status["cumulative_sent_keys"] == 3
    assert status["cumulative_sent_previews"] == 2
    assert status["cumulative_sent_recipients"] == 2
    assert status["training_rows"] == 4
    assert status["training_by_result"] == {"take": 2, "expired_no_entry": 1, "stop": 1}
    assert status["training_by_family"] == {"early_tp_tactical": 3, "continuation": 1}
    assert status["training_by_diagnosis"] == {"good_signal": 2, "wrong_direction": 1}
    assert status["execution_allowed"] is False
    assert "items" not in status
