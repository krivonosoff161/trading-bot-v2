import asyncio
import json
from datetime import date, timedelta

from scripts.archive_runtime_logs import archive_runtime_logs
from src.utils.notification_policy import decide_notification
from src.utils.telegram_audit import record_message_audit
from src.utils.telegram_delivery_router import deliver_notification


def test_paper_setup_requires_subscription():
    blocked = decide_notification("PAPER_SETUP", is_subscribed=False)
    allowed = decide_notification("PAPER_SETUP", is_subscribed=True)

    assert blocked.allowed is False
    assert blocked.reason == "subscription_required"
    assert allowed.allowed is True
    assert allowed.destination == "personal_bot"
    assert allowed.public_signal_levels_allowed is False


def test_public_scanner_watch_goes_to_notification_channel_without_signal_levels():
    decision = decide_notification("SCANNER_WATCH")

    assert decision.allowed is True
    assert decision.destination == "notification_channel"
    assert decision.public_signal_levels_allowed is False


def test_message_audit_writes_sanitized_jsonl(tmp_path):
    path = tmp_path / "message_audit.jsonl"
    record_message_audit(
        chat_id="123",
        direction="incoming",
        mode="education",
        event="message",
        text="How do I calculate leverage?",
        provider="alibaba",
        path=path,
    )

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["schema"] == "telegram_message_audit.v1"
    assert row["chat_id"] == "123"
    assert row["text_hash"]
    assert row["text_preview"] == "How do I calculate leverage?"
    assert row["provider"] == "alibaba"


def test_delivery_router_dry_run_subscriber_only(monkeypatch, tmp_path):
    from src.utils import telegram_audit

    monkeypatch.setattr(telegram_audit, "DEFAULT_AUDIT_PATH", tmp_path / "audit.jsonl")

    async def fail_sender(chat_id: str, text: str):
        raise AssertionError("dry-run must not call sender")

    report = asyncio.run(
        deliver_notification(
            event_type="PAPER_SETUP",
            text="paper setup",
            users=[
                {"chat_id": "1", "status": "active"},
                {"chat_id": "2", "status": "expired"},
                {"chat_id": "3", "status": "superadmin"},
            ],
            sender=fail_sender,
            dry_run=True,
        )
    )

    assert report["targets"] == 2
    assert {r["chat_id"] for r in report["rows"]} == {"1", "3"}
    assert all(r["status"] == "dry_run" for r in report["rows"])


def test_subscription_delivery_users_are_normalized(monkeypatch, tmp_path):
    from scripts import subscriptions

    path = tmp_path / "subscriptions.json"
    path.write_text(
        json.dumps({
            "1": {"expires": None, "plan": "superadmin"},
            "2": {"expires": (date.today() + timedelta(days=7)).isoformat(), "plan": "monthly"},
            "3": {"expires": (date.today() - timedelta(days=1)).isoformat(), "plan": "monthly"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(subscriptions, "SUBS_FILE", path)

    rows = subscriptions.list_delivery_users()

    assert {row["chat_id"]: row["status"] for row in rows} == {
        "1": "superadmin",
        "2": "active",
        "3": "expired",
    }


def test_archive_runtime_logs_dry_run_and_apply(tmp_path):
    logs = tmp_path / "logs"
    archive = tmp_path / "logs_archive"
    (logs / "users" / "1").mkdir(parents=True)
    (logs / "users" / "1" / "feedback.jsonl").write_text("x\n", encoding="utf-8")
    (logs / "telegram_bot.log").write_text("old\n", encoding="utf-8")

    dry = archive_runtime_logs(logs_root=logs, archive_root=archive, label="revival_test", apply=False)
    assert dry["apply"] is False
    assert (logs / "telegram_bot.log").exists()

    applied = archive_runtime_logs(logs_root=logs, archive_root=archive, label="revival_test", apply=True)
    assert applied["apply"] is True
    assert (archive / "revival_test" / "manifest.json").exists()
    assert (archive / "revival_test" / "telegram_bot.log").exists()
    assert not (logs / "telegram_bot.log").exists()
    assert logs.exists()
