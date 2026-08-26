from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.research_lab import paper_telegram_outbox_disposition as disposition
from src.research_lab import paper_telegram_sender as sender


def _record(key: str, *, status: str, problem: str = "") -> dict:
    return {
        "schema": "paper_telegram_delivery_outbox_item.v1",
        "delivery_key": key,
        "preview_id": f"preview_{key}",
        "instruction_id": f"instruction_{key}",
        "source_signal_id": f"signal_{key}",
        "telegram_card_id": f"card_{key}",
        "recipient_hash": "recipient_ref",
        "status": status,
        "transport_kind": "telegram_text",
        "message_id": None,
        "photo_message_id": None,
        "photo_status": "not_applicable",
        "text_status": "unknown",
        "problem": problem,
        "paper_only": True,
        "execution_allowed": False,
    }


def _write_outbox(root: Path, rows: list[dict]) -> Path:
    path = root / "state" / "derived" / "paper_telegram_delivery_outbox.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema": "paper_telegram_delivery_outbox.v1", "items": rows},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def test_exact_plan_suppresses_ambiguous_and_pending_without_claiming_delivery(tmp_path):
    outbox_path = _write_outbox(
        tmp_path,
        [
            _record("ambiguous", status="external_ack_ambiguous", problem="timeout"),
            _record("pending", status="pending"),
            _record("completed", status="completed"),
        ],
    )
    plan = disposition.build_disposition_plan(tmp_path, now=1_700_000_000.0)
    backup_path = tmp_path / "evidence" / "outbox.before.json"

    assert plan["target_count"] == 2
    assert {item["prior_status"] for item in plan["items"]} == {
        "external_ack_ambiguous",
        "pending",
    }

    result = disposition.apply_disposition_plan(
        tmp_path,
        plan,
        expected_plan_digest=plan["plan_digest"],
        backup_path=backup_path,
        confirm_permanent_no_replay=True,
        now=1_700_000_100.0,
    )

    assert result["applied"] == 2
    assert result["already_applied"] == 0
    assert backup_path.read_bytes() != outbox_path.read_bytes()
    rows = {
        row["delivery_key"]: row
        for row in json.loads(outbox_path.read_text(encoding="utf-8"))["items"]
    }
    assert rows["completed"]["status"] == "completed"
    for key in ("ambiguous", "pending"):
        assert rows[key]["status"] == "operator_suppressed_no_replay"
        assert rows[key]["problem"] == "operator_suppressed_no_replay"
        assert rows[key]["operator_disposition"]["prior_status"] in {
            "external_ack_ambiguous",
            "pending",
        }
        assert rows[key]["operator_disposition"]["plan_digest"] == plan["plan_digest"]
    assert not (tmp_path / "state" / "derived" / "paper_telegram_sent_keys.json").exists()

    replay = disposition.apply_disposition_plan(
        tmp_path,
        plan,
        expected_plan_digest=plan["plan_digest"],
        backup_path=backup_path,
        confirm_permanent_no_replay=True,
        now=1_700_000_200.0,
    )
    assert replay["applied"] == 0
    assert replay["already_applied"] == 2


def test_apply_rejects_outbox_drift_before_any_mutation(tmp_path):
    outbox_path = _write_outbox(
        tmp_path,
        [_record("ambiguous", status="external_ack_ambiguous", problem="timeout")],
    )
    plan = disposition.build_disposition_plan(tmp_path, now=1_700_000_000.0)
    before = outbox_path.read_bytes()
    data = json.loads(before)
    data["items"][0]["problem"] = "changed-after-plan"
    outbox_path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    drifted = outbox_path.read_bytes()

    with pytest.raises(disposition.OutboxDispositionError, match="outbox drift"):
        disposition.apply_disposition_plan(
            tmp_path,
            plan,
            expected_plan_digest=plan["plan_digest"],
            backup_path=tmp_path / "evidence" / "outbox.before.json",
            confirm_permanent_no_replay=True,
            now=1_700_000_100.0,
        )

    assert outbox_path.read_bytes() == drifted
    assert not (tmp_path / "evidence" / "outbox.before.json").exists()


def test_apply_requires_explicit_permanent_no_replay_confirmation(tmp_path):
    outbox_path = _write_outbox(
        tmp_path, [_record("pending", status="pending", problem="crash-left claim")]
    )
    plan = disposition.build_disposition_plan(tmp_path, now=1_700_000_000.0)
    before = outbox_path.read_bytes()

    with pytest.raises(disposition.OutboxDispositionError, match="confirmation"):
        disposition.apply_disposition_plan(
            tmp_path,
            plan,
            expected_plan_digest=plan["plan_digest"],
            backup_path=tmp_path / "evidence" / "outbox.before.json",
            confirm_permanent_no_replay=False,
        )

    assert outbox_path.read_bytes() == before


def test_apply_rejects_sent_key_index_drift(tmp_path):
    outbox_path = _write_outbox(
        tmp_path, [_record("ambiguous", status="external_ack_ambiguous")]
    )
    sent_path = tmp_path / "state" / "derived" / "paper_telegram_sent_keys.json"
    sent_path.write_text(
        json.dumps({"schema": "paper_telegram_sent_keys.v1", "sent_keys": []}),
        encoding="utf-8",
    )
    plan = disposition.build_disposition_plan(tmp_path, now=1_700_000_000.0)
    before = outbox_path.read_bytes()
    sent_path.write_text(
        json.dumps({"schema": "paper_telegram_sent_keys.v1", "sent_keys": ["new"]}),
        encoding="utf-8",
    )

    with pytest.raises(disposition.OutboxDispositionError, match="sent-key index"):
        disposition.apply_disposition_plan(
            tmp_path,
            plan,
            expected_plan_digest=plan["plan_digest"],
            backup_path=tmp_path / "evidence" / "outbox.before.json",
            confirm_permanent_no_replay=True,
        )

    assert outbox_path.read_bytes() == before
    assert not (tmp_path / "evidence" / "outbox.before.json").exists()


def test_sender_never_replays_operator_suppressed_record(tmp_path):
    preview = {
        "schema": "PaperTelegramPreview.v1",
        "preview_id": "preview_1",
        "instruction_id": "instruction_1",
        "source_signal_id": "signal_1",
        "telegram_card_id": "card_1",
        "text": "Research only. Not financial advice. Бумажный режим: это не ордер.",
        "chart_path": "",
        "problems": [],
        "paper_only": True,
        "execution_allowed": False,
    }
    preview_path = tmp_path / "state" / "derived" / "paper_telegram_preview.json"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(
        json.dumps({"schema": "PaperTelegramPreviewSnapshot.v1", "items": [preview]}),
        encoding="utf-8",
    )
    key = sender._delivery_key(preview, "111")
    suppressed = _record(key, status="operator_suppressed_no_replay")
    suppressed.update(
        {
            "preview_id": preview["preview_id"],
            "instruction_id": preview["instruction_id"],
            "source_signal_id": preview["source_signal_id"],
            "telegram_card_id": preview["telegram_card_id"],
            "recipient_hash": sender._recipient_hash("111"),
            "problem": "operator_suppressed_no_replay",
        }
    )
    _write_outbox(tmp_path, [suppressed])
    calls: list[tuple[str, str]] = []

    async def fake_send(chat_id: str, text: str) -> int:
        calls.append((chat_id, text))
        return 101

    summary = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
    )

    assert calls == []
    assert summary["sent"] == 0
    assert summary["operator_suppressed_no_replay_messages"] == 1
    delivery = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))["items"][0]
    assert delivery["status"] == "skipped_operator_suppressed"
    assert delivery["problem"] == "operator_suppressed_no_replay"
