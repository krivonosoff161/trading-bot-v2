import json
from pathlib import Path

from src.research_lab import paper_telegram_sender as sender


def _preview(**overrides):
    row = {
        "schema": "PaperTelegramPreview.v1",
        "preview_id": "preview_1",
        "instruction_id": "mainpaper_1",
        "source_signal_id": "sig_1",
        "pair": "BTC-USDT-SWAP",
        "timeframe": "1h",
        "side": "long",
        "setup_family": "early_tp_tactical",
        "consumer_status": "accepted_for_paper_watch",
        "text": "<b>PAPER WATCH</b>\nresearch-only, not an order\nexecution_allowed=false",
        "problems": [],
        "paper_only": True,
        "execution_allowed": False,
    }
    row.update(overrides)
    return row


def _write_preview_snapshot(root: Path, rows: list[dict]) -> None:
    path = root / "state" / "derived" / "paper_telegram_preview.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema": "paper_telegram_preview.v1",
                "rendered": len(rows),
                "invalid": 0,
                "sends_network": False,
                "items": rows,
            }
        ),
        encoding="utf-8",
    )


def test_sender_dry_run_never_calls_telegram(tmp_path):
    _write_preview_snapshot(tmp_path, [_preview()])

    async def fail_send(*_args, **_kwargs):
        raise AssertionError("dry-run must not send")

    summary = sender.send_paper_telegram_previews(tmp_path, apply=False, send_text=fail_send)

    assert summary["dry_run"] is True
    assert summary["sends_network"] is False
    assert summary["sent"] == 0
    assert summary["eligible"] == 1
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["items"][0]["status"] == "dry_run"


def test_sender_apply_skips_without_subscribers(monkeypatch, tmp_path):
    _write_preview_snapshot(tmp_path, [_preview()])

    summary = sender.send_paper_telegram_previews(tmp_path, apply=True)

    assert summary["configured"] is False
    assert summary["sends_network"] is False
    assert summary["sent"] == 0
    assert summary["skipped"] == 1
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["items"][0]["status"] == "skipped_no_subscribers"
    assert data["items"][0]["problem"] == "paper_subscribers_not_configured"


def test_sender_uses_injected_subscriber_transport(tmp_path):
    _write_preview_snapshot(tmp_path, [_preview()])
    calls = []

    async def fake_send(chat_id, text):
        calls.append((chat_id, text))
        return 100 + len(calls)

    summary = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=2,
        recipient_ids=["111", "222"],
        send_text=fake_send,
    )

    assert summary["configured"] is True
    assert summary["sends_network"] is True
    assert summary["targets"] == 2
    assert summary["sent"] == 2
    assert calls == [
        ("111", "<b>PAPER WATCH</b>\nresearch-only, not an order\nexecution_allowed=false"),
        ("222", "<b>PAPER WATCH</b>\nresearch-only, not an order\nexecution_allowed=false"),
    ]
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["chat_env"] == "SUBSCRIPTION_USERS"
    assert data["items"][0]["message_id"] == 101
    assert data["items"][0]["destination"] == "personal_bot"
    assert data["items"][0]["recipient_hash"]
    assert "111" not in json.dumps(data, ensure_ascii=False)
    assert "222" not in json.dumps(data, ensure_ascii=False)


def test_sender_deduplicates_sent_preview_per_recipient(tmp_path):
    _write_preview_snapshot(tmp_path, [_preview()])
    calls = []

    async def fake_send(chat_id, text):
        calls.append((chat_id, text))
        return 100 + len(calls)

    first = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
    )
    second = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
    )

    assert first["sent"] == 1
    assert second["sent"] == 0
    assert second["duplicates"] == 1
    assert len(calls) == 1


def test_sender_rejects_invalid_preview(tmp_path):
    _write_preview_snapshot(tmp_path, [_preview(execution_allowed=True)])

    summary = sender.send_paper_telegram_previews(tmp_path, apply=False)

    assert summary["eligible"] == 0
    assert summary["invalid_preview"] == 1
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["items"][0]["status"] == "invalid_preview"
    assert data["items"][0]["problem"] == "execution_allowed_not_false"
