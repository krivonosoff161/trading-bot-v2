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
        "text": "<b>Бумажный сигнал</b>\nБумажный режим: это не ордер.\nАвтоисполнение выключено.",
        "problems": [],
        "paper_only": True,
        "execution_allowed": False,
    }
    row.update(overrides)
    return row


def _write_preview_snapshot(root: Path, rows: list[dict]) -> None:
    path = root / "state" / "derived" / "paper_telegram_preview.json"
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _write_quality_report(root: Path, **overrides) -> None:
    path = root / "state" / "derived" / "paper_product_quality_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": "paper_product_quality_report.v1",
        "operator_action": "strict_main_waiting_for_active_pfr_candidates",
        "active_trades": 12,
        "active_live_ready": 0,
        "quality_labels": {"mixed": 1, "needs_review": 4},
        "training_rows": 1393,
        "training_by_result": {"take": 184, "stop": 371},
        "pfr_trigger_state": {
            "state": "waiting_for_live_trigger",
            "catalog_ready": 43,
            "bridge_instructions": 0,
            "last_cycle_generated": 0,
            "top_reasons": {"pfr_rejected:no_breakout": 6},
        },
        "pfr_funnel": {
            "near_trigger_counts": {"pfr_near_trigger:fade_gap_gt_2pct": 5},
            "cycle_resource_reasons": {"network_fetch_limit_reached": 1},
        },
        "active_signal_lifecycle": {
            "active": 12,
            "by_status": {"armed": 10, "opened_paper": 2},
            "by_outcome_result": {"pending": 12},
            "pending_outcomes": 12,
            "active_without_outcome": 0,
            "oldest_age_hours": 22.5,
            "next_expiry_hours": 0.5,
            "overdue_expiry": 0,
            "age_buckets": {"le_24h": 12},
            "expiry_buckets": {"le_1h": 1, "le_3h": 4, "le_24h": 7},
            "terminal_training_backlog": 0,
        },
        "paper_only": True,
        "execution_allowed": False,
    }
    data.update(overrides)
    path.write_text(
        json.dumps(data, ensure_ascii=False),
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
    assert summary["eligible_cards"] == 1
    assert summary["target_recipients"] == 2
    assert summary["potential_messages"] == 2
    assert summary["sent_messages"] == 2
    assert summary["sent_cards"] == 1
    assert calls == [
        ("111", "<b>Бумажный сигнал</b>\nБумажный режим: это не ордер.\nАвтоисполнение выключено."),
        ("222", "<b>Бумажный сигнал</b>\nБумажный режим: это не ордер.\nАвтоисполнение выключено."),
    ]
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["chat_env"] == "SUBSCRIPTION_USERS"
    assert data["items"][0]["message_id"] == 101
    assert data["items"][0]["destination"] == "personal_bot"
    assert data["items"][0]["recipient_hash"]
    assert "recipient_id" not in data["items"][0]
    assert all(item["recipient_hash"] not in {"111", "222"} for item in data["items"])


def test_sender_sends_private_review_chart_before_text(tmp_path):
    chart_path = tmp_path / "state" / "derived" / "paper_reviews" / "sig_1.png"
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    chart_path.write_bytes(b"fake-png")
    _write_preview_snapshot(tmp_path, [_preview(chart_path=str(chart_path))])
    text_calls = []
    photo_calls = []
    events = []

    async def fake_send(chat_id, text):
        events.append(("text", chat_id))
        text_calls.append((chat_id, text))
        return 101

    async def fake_photo(chat_id, path):
        events.append(("photo", chat_id))
        photo_calls.append((chat_id, path))
        return 201

    summary = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
        send_photo=fake_photo,
    )

    assert summary["sent"] == 1
    assert summary["chart_available_messages"] == 1
    assert summary["chart_sent_messages"] == 1
    assert events == [("photo", "111"), ("text", "111")]
    assert text_calls == [
        ("111", "<b>Бумажный сигнал</b>\nБумажный режим: это не ордер.\nАвтоисполнение выключено.")
    ]
    assert photo_calls == [("111", str(chart_path.resolve()))]
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["items"][0]["chart_available"] is True
    assert data["items"][0]["chart_sent"] is True
    assert data["items"][0]["chart_problem"] == ""
    assert "recipient_id" not in data["items"][0]


def test_sender_does_not_mark_chart_sent_without_photo_message_id(tmp_path):
    chart_path = tmp_path / "state" / "derived" / "paper_reviews" / "sig_1.png"
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    chart_path.write_bytes(b"fake-png")
    _write_preview_snapshot(tmp_path, [_preview(chart_path=str(chart_path))])

    async def fake_send(chat_id, text):
        return 101

    async def fake_photo(chat_id, path):
        return None

    summary = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
        send_photo=fake_photo,
    )

    assert summary["sent"] == 1
    assert summary["chart_available_messages"] == 1
    assert summary["chart_sent_messages"] == 0
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["items"][0]["chart_available"] is True
    assert data["items"][0]["chart_sent"] is False
    assert data["items"][0]["chart_problem"] == "photo_message_id_missing"


def test_sender_sends_private_legacy_base_chart(tmp_path):
    chart_path = tmp_path / "state" / "derived" / "paper_telegram_base_charts" / "sig_1.png"
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    chart_path.write_bytes(b"fake-png")
    _write_preview_snapshot(tmp_path, [_preview(chart_path=str(chart_path))])
    photo_calls = []

    async def fake_send(chat_id, text):
        return 101

    async def fake_photo(chat_id, path):
        photo_calls.append((chat_id, path))
        return 201

    summary = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
        send_photo=fake_photo,
    )

    assert summary["sent"] == 1
    assert summary["chart_available_messages"] == 1
    assert summary["chart_sent_messages"] == 1
    assert photo_calls == [("111", str(chart_path.resolve()))]


def test_sender_refuses_chart_outside_private_reviews(tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"fake-png")
    _write_preview_snapshot(tmp_path, [_preview(chart_path=str(outside))])
    photo_calls = []

    async def fake_send(chat_id, text):
        return 101

    async def fake_photo(chat_id, path):
        photo_calls.append((chat_id, path))
        return 201

    summary = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
        send_photo=fake_photo,
    )

    assert summary["sent"] == 1
    assert summary["chart_available_messages"] == 0
    assert summary["chart_sent_messages"] == 0
    assert photo_calls == []
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["items"][0]["chart_available"] is False
    assert data["items"][0]["chart_sent"] is False
    assert data["items"][0]["chart_problem"] == "chart_outside_private_reviews"


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


def test_sender_sends_scenario_update_as_separate_state_event(tmp_path):
    _write_preview_snapshot(
        tmp_path,
        [
            _preview(
                preview_id="preview_signal",
                source_signal_id="sig_primary",
                telegram_card_id="tgcard_sig_primary",
            ),
            _preview(
                preview_id="preview_scenario_update",
                source_signal_id="scenario_update:thesis_1:state_1",
                telegram_card_id="tgcard_scenario_update_1",
                consumer_status="scenario_update",
                text=f"<b>scenario update</b>\n{sender.REQUIRED_DISCLAIMER}\nexecution disabled.",
            ),
        ],
    )
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

    assert first["sent"] == 2
    assert first["sent_cards"] == 2
    assert second["sent"] == 0
    assert second["duplicate_cards"] == 2
    assert len(calls) == 2


def test_sender_persists_sent_key_after_each_successful_delivery(tmp_path):
    _write_preview_snapshot(tmp_path, [_preview(telegram_card_id="tgcard_sig_1_clean_v2")])
    sent_keys_path = tmp_path / "state" / "derived" / "paper_telegram_sent_keys.json"
    observed_after_first = []
    calls = []

    async def fake_send(chat_id, text):
        calls.append((chat_id, text))
        if len(calls) == 2:
            data = json.loads(sent_keys_path.read_text(encoding="utf-8"))
            observed_after_first.extend(data["sent_keys"])
        return 100 + len(calls)

    summary = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=2,
        recipient_ids=["111", "222"],
        send_text=fake_send,
    )

    assert summary["sent"] == 2
    assert len(calls) == 2
    assert "signal:sig_1:f6e0a1e2ac41945a" in observed_after_first


def test_sender_deduplicates_when_card_template_changes_same_signal(tmp_path):
    _write_preview_snapshot(
        tmp_path,
        [
            _preview(
                preview_id="preview_1",
                telegram_card_id="tgcard_sig_1_clean_v2",
            )
        ],
    )
    sent_keys_path = tmp_path / "state" / "derived" / "paper_telegram_sent_keys.json"
    sent_keys_path.write_text(
        json.dumps(
            {
                "schema": "paper_telegram_sent_keys.v1",
                "sent_keys": ["preview_1:f6e0a1e2ac41945a"],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    async def fake_send(chat_id, text):
        calls.append((chat_id, text))
        return 100 + len(calls)

    summary = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
    )

    assert summary["sent"] == 0
    assert summary["duplicates"] == 1
    assert len(calls) == 0
    data = json.loads(sent_keys_path.read_text(encoding="utf-8"))
    assert "preview_1:f6e0a1e2ac41945a" in data["sent_keys"]
    assert "signal:sig_1:f6e0a1e2ac41945a" in data["sent_keys"]
    assert "tgcard_sig_1_clean_v2:f6e0a1e2ac41945a" in data["sent_keys"]


def test_sender_status_digest_when_all_cards_are_duplicate(tmp_path):
    _write_preview_snapshot(tmp_path, [_preview()])
    _write_quality_report(tmp_path)
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
        status_digest=True,
        now=1_800_000_000.0,
    )
    second = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
        status_digest=True,
        now=1_800_000_000.0,
    )

    assert first["sent"] == 1
    assert first["status_digest_sent_messages"] == 0
    assert second["sent"] == 1
    assert second["duplicates"] == 1
    assert second["status_digest_reason"] == "all_cards_duplicate"
    assert second["status_digest_sent_messages"] == 1
    assert len(calls) == 2
    assert "Статус paper-бота" in calls[1][1]
    assert "all_cards_duplicate" in calls[1][1]
    assert "pending=<code>12</code>" in calls[1][1]
    assert "oldest_h=<code>22.5</code>" in calls[1][1]
    assert "next_expiry_h=<code>0.5</code>" in calls[1][1]
    assert "waiting_for_live_trigger" in calls[1][1]
    assert "pfr_rejected:no_breakout" in calls[1][1]
    assert "PFR рядом со входом" in calls[1][1]
    assert "pfr_near_trigger:fade_gap_gt_2pct" in calls[1][1]
    assert "Блокеры цикла" in calls[1][1]
    assert "network_fetch_limit_reached" in calls[1][1]
    assert "Бумажный режим: это не ордер." in calls[1][1]
    assert "Автоисполнение выключено." in calls[1][1]
    assert "research-only, not an order" not in calls[1][1]
    assert "execution_allowed=false" not in calls[1][1]


def test_sender_status_digest_deduplicates_unchanged_state_in_same_bucket(tmp_path):
    _write_preview_snapshot(tmp_path, [_preview()])
    _write_quality_report(tmp_path)
    calls = []

    async def fake_send(chat_id, text):
        calls.append((chat_id, text))
        return 100 + len(calls)

    sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
        status_digest=True,
        now=1_800_000_000.0,
    )
    second = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
        status_digest=True,
        now=1_800_000_000.0,
    )
    third = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
        status_digest=True,
        now=1_800_000_000.0,
    )

    assert second["status_digest_sent_messages"] == 1
    assert third["sent"] == 0
    assert third["status_digest_reason"] == "all_cards_duplicate"
    assert third["status_digest_sent_messages"] == 0
    assert third["status_digest_duplicate_messages"] == 1
    assert len(calls) == 2


def test_sender_status_digest_sends_when_material_state_changes_in_same_bucket(tmp_path):
    _write_preview_snapshot(tmp_path, [_preview()])
    _write_quality_report(tmp_path)
    calls = []

    async def fake_send(chat_id, text):
        calls.append((chat_id, text))
        return 100 + len(calls)

    sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
        status_digest=True,
        now=1_800_000_000.0,
    )
    sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
        status_digest=True,
        now=1_800_000_000.0,
    )
    _write_quality_report(
        tmp_path,
        active_trades=9,
        training_rows=1396,
        training_by_result={"take": 184, "stop": 371, "expired_no_entry": 3},
        active_signal_lifecycle={
            "active": 9,
            "by_status": {"armed": 8, "opened_paper": 1},
            "by_outcome_result": {"pending": 9},
            "pending_outcomes": 9,
            "active_without_outcome": 0,
            "oldest_age_hours": 22.5,
            "next_expiry_hours": 0.5,
            "overdue_expiry": 0,
            "age_buckets": {"le_24h": 9},
            "expiry_buckets": {"le_1h": 1, "le_3h": 3, "le_24h": 5},
            "terminal_training_backlog": 0,
        },
    )
    third = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
        status_digest=True,
        now=1_800_000_000.0,
    )

    assert third["status_digest_reason"] == "all_cards_duplicate"
    assert third["status_digest_sent_messages"] == 1
    assert len(calls) == 3
    assert "pending=<code>9</code>" in calls[2][1]
    assert "expired_no_entry" in calls[2][1]
    data = json.loads(Path(third["snapshot_path"]).read_text(encoding="utf-8"))
    digest_items = [item for item in data["items"] if item["source_signal_id"] == "paper_status_digest"]
    assert digest_items[0]["preview_id"].startswith("paper_status_digest_")
    assert "recipient_id" not in digest_items[0]
    assert digest_items[0]["recipient_hash"] != "111"


def test_sender_status_digest_when_quality_gate_leaves_no_cards(tmp_path):
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    (derived / "paper_telegram_preview.json").write_text(
        json.dumps(
            {
                "schema": "paper_telegram_preview.v1",
                "records_read": 2,
                "rendered": 0,
                "skipped_quality_gate": 2,
                "quality_gate_reasons": {"quality_label:needs_review": 2},
                "items": [],
                "sends_network": False,
            }
        ),
        encoding="utf-8",
    )
    _write_quality_report(tmp_path)
    calls = []

    async def fake_send(chat_id, text):
        calls.append((chat_id, text))
        return 200 + len(calls)

    summary = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
        status_digest=True,
        now=1_800_000_000.0,
    )

    assert summary["eligible"] == 0
    assert summary["sent"] == 1
    assert summary["status_digest_reason"] == "quality_gate_no_cards"
    assert summary["status_digest_sent_messages"] == 1
    assert "quality_gate_no_cards" in calls[0][1]
    assert "waiting_for_live_trigger" in calls[0][1]
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["items"][0]["source_signal_id"] == "paper_status_digest"
    assert "recipient_id" not in data["items"][0]
    assert data["items"][0]["recipient_hash"] != "111"


def test_sender_separates_unique_cards_from_recipient_messages(tmp_path):
    _write_preview_snapshot(
        tmp_path,
            [
                _preview(preview_id="preview_1", instruction_id="mainpaper_1", source_signal_id="sig_1"),
                _preview(preview_id="preview_2", instruction_id="mainpaper_2", source_signal_id="sig_2"),
            ],
        )
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

    assert summary["eligible"] == 2
    assert summary["eligible_cards"] == 2
    assert summary["targets"] == 2
    assert summary["target_recipients"] == 2
    assert summary["potential_messages"] == 4
    assert summary["sent"] == 4
    assert summary["sent_messages"] == 4
    assert summary["sent_cards"] == 2
    assert len(calls) == 4


def test_sender_rejects_invalid_preview(tmp_path):
    _write_preview_snapshot(tmp_path, [_preview(execution_allowed=True)])

    summary = sender.send_paper_telegram_previews(tmp_path, apply=False)

    assert summary["eligible"] == 0
    assert summary["invalid_preview"] == 1
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["items"][0]["status"] == "invalid_preview"
    assert data["items"][0]["problem"] == "execution_allowed_not_false"


def test_sender_records_ambiguous_ack_when_sent_key_write_fails(monkeypatch, tmp_path):
    _write_preview_snapshot(tmp_path, [_preview()])
    calls = []
    original_save = sender._save_sent_keys

    def fail_after_transport_ack(private_root, sent_keys):
        if calls:
            raise OSError("synthetic durable write failure")
        original_save(private_root, sent_keys)

    async def fake_send(chat_id, text):
        calls.append((chat_id, text))
        return 101

    monkeypatch.setattr(sender, "_save_sent_keys", fail_after_transport_ack)

    summary = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
    )

    assert len(calls) == 1
    assert summary["sent"] == 0
    assert summary["external_ack_ambiguous_messages"] == 1
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    item = data["items"][0]
    assert item["status"] == "external_ack_ambiguous"
    assert item["message_id"] == 101
    assert item["problem"] == "sent_key_write_failed"
    assert item["recipient_hash"] == sender._recipient_hash("111")
    assert item["delivery_key"] == sender._delivery_key(_preview(), "111")
    assert "recipient_id" not in item
    assert "111" not in json.dumps(data, ensure_ascii=False)


def test_sender_fails_closed_on_ambiguous_ack_without_resend(tmp_path):
    _write_preview_snapshot(tmp_path, [_preview()])
    calls = []

    async def fake_send(chat_id, text):
        calls.append((chat_id, text))
        return 101

    first = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
    )
    assert first["sent"] == 1

    outbox_path = tmp_path / "state" / "derived" / "paper_telegram_delivery_outbox.json"
    outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
    outbox["items"][0]["status"] = "external_ack_ambiguous"
    outbox["items"][0]["problem"] = "synthetic_crash_after_ack"
    outbox_path.write_text(json.dumps(outbox, ensure_ascii=False), encoding="utf-8")

    second = sender.send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["111"],
        send_text=fake_send,
    )

    assert len(calls) == 1
    assert second["sent"] == 0
    assert second["external_ack_ambiguous_messages"] == 1
    data = json.loads(Path(second["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["items"][0]["status"] == "external_ack_ambiguous"
    assert data["items"][0]["problem"] == "external_ack_requires_operator_recovery"
    assert data["items"][0]["message_id"] == 101


def test_sender_rejects_existing_pending_outbox_owner_without_resend(tmp_path):
    preview = _preview()
    _write_preview_snapshot(tmp_path, [preview])
    delivery_key = sender._delivery_key(preview, "111")
    outbox_path = tmp_path / "state" / "derived" / "paper_telegram_delivery_outbox.json"
    outbox_path.parent.mkdir(parents=True, exist_ok=True)
    outbox_path.write_text(
        json.dumps(
            {
                "schema": "paper_telegram_delivery_outbox.v1",
                "items": [
                    {
                        "schema": "paper_telegram_delivery_outbox_item.v1",
                        "delivery_key": delivery_key,
                        "preview_id": preview["preview_id"],
                        "source_signal_id": preview["source_signal_id"],
                        "recipient_hash": sender._recipient_hash("111"),
                        "status": "pending",
                        "transport_kind": "telegram_text",
                        "message_id": None,
                        "problem": "",
                        "paper_only": True,
                        "execution_allowed": False,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    async def fake_send(chat_id, text):
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
    assert summary["pending_delivery_claim_messages"] == 1
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["items"][0]["status"] == "pending_delivery_claim"
    assert data["items"][0]["problem"] == "delivery_owned_by_existing_attempt"
    assert data["items"][0]["delivery_key"] == delivery_key
