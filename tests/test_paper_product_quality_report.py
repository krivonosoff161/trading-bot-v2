import json

from src.research_lab.paper_product_quality_report import build_paper_product_quality_report


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _append_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_quality_report_aggregates_private_rows_without_raw_items(tmp_path):
    derived = tmp_path / "state" / "derived"
    _write_json(
        derived / "paper_product_trades.json",
        {
            "schema": "paper_product_trade_ledger.v1",
            "trades": 25,
            "live_ready": 0,
            "live_blocked": 25,
            "active_trades": 3,
            "active_live_ready": 0,
            "active_live_blocked": 3,
            "active_by_source": {"farm": 3},
            "active_by_family": {"early_tp_tactical": 3},
            "by_live_block": {"missing_ready_strategy_id": 25},
            "items": [
                {
                    "source_signal_id": "private_signal",
                    "status": "armed",
                    "live_block_reason": "missing_ready_strategy_id",
                },
                {
                    "source_signal_id": "private_signal_2",
                    "status": "opened_paper",
                    "live_block_reason": "missing_ready_strategy_id",
                },
                {
                    "source_signal_id": "old_private_signal",
                    "status": "reviewed",
                    "live_block_reason": "missing_ready_strategy_id",
                },
            ],
        },
    )
    _write_json(derived / "paper_telegram_preview.json", {"schema": "paper_telegram_preview.v1", "rendered": 3})
    _write_json(
        derived / "paper_telegram_delivery.json",
        {
            "schema": "paper_telegram_delivery.v1",
            "eligible": 3,
            "eligible_cards": 3,
            "target_recipients": 2,
            "potential_messages": 6,
            "sent": 0,
            "sent_messages": 0,
            "sent_cards": 0,
            "duplicates": 3,
            "duplicate_messages": 3,
            "duplicate_cards": 2,
            "errors": 0,
            "error_messages": 0,
            "error_cards": 0,
            "configured": True,
            "sends_network": True,
        },
    )
    _write_json(
        derived / "ready_strategy_catalog.json",
        {
            "schema": "ready_strategy_catalog.v1",
            "ready": 43,
            "rejected_quality": 10,
            "ready_by_family": {"mean_reversion_fade": 22, "momentum_breakout": 21},
            "ready_by_timeframe": {"4h": 24, "15m": 10, "1h": 9},
        },
    )
    _write_json(
        derived / "main_paper_instructions.json",
        {
            "schema": "main_paper_bridge.v1",
            "active_source_signals": 3,
            "instructions": 0,
            "skip_reasons": {"missing_ready_strategy_id": 3},
        },
    )
    _write_json(
        derived / "paper_signals_status.json",
        {
            "schema": "paper_signals_status.v1",
            "last_cycle": {
                "generated": 0,
                "observed": 3,
                "pfr_counts": {
                    "pfr_records_loaded": 53,
                    "pfr_passed_quality": 43,
                    "pfr_duplicate_setup_variant": 32,
                    "pfr_unique_setups": 11,
                    "pfr_rejected_quality": 10,
                    "pfr_rejected:no_breakout": 6,
                    "pfr_rejected:no_fade_signal:move_pct_threshold=8.0": 5,
                    "pfr_near_trigger:breakout_gap_le_0_5pct": 4,
                    "pfr_near_trigger:fade_gap_gt_2pct": 5,
                    "pfr_reserved_slots": 2,
                },
                "gate_counts": {"stale_data": 4, "dedup_active": 3, "network_fetch_limit_reached": 1},
            },
        },
    )
    _write_json(
        derived / "paper_signals.json",
        {
            "schema": "paper_signals.v1",
            "active": [
                {
                    "status": "armed",
                    "created_at": 1_000.0,
                    "expires_at": 11_800.0,
                    "outcome": {"result": "pending_arm"},
                },
                {
                    "status": "opened_paper",
                    "created_at": 6_400.0,
                    "expires_at": 17_200.0,
                    "outcome": {"result": "pending_open"},
                },
                {
                    "status": "armed",
                    "created_at": 9_000.0,
                    "expires_at": 9_500.0,
                    "outcome": {},
                },
                {"status": "reviewed", "outcome": {"result": "take"}},
            ],
        },
    )
    _write_json(
        derived / "paper_telegram_sent_keys.json",
        {"schema": "paper_telegram_sent_keys.v1", "sent_keys": ["card1:user1", "card2:user1"]},
    )
    _write_json(
        derived / "paper_signal_training.json",
        {
            "schema": "paper_signal_training_export.v2",
            "rows": 22,
            "terminal_only": True,
            "by_result": {"take": 10, "stop": 5, "simple_be": 4, "expired_no_entry": 3},
        },
    )
    rows = []
    for idx in range(22):
        rows.append(
            {
                "schema": "TrainingRow.v2",
                "signal_id": f"secret_{idx}",
                "family": "early_tp_tactical",
                "result": "take" if idx < 10 else "stop" if idx < 15 else "simple_be",
                "diagnosis": "good_signal" if idx < 10 else "breakeven_save",
                "net_r": 0.25,
                "net_pct": 0.1,
                "paper_pnl_usdt": 0.4 if idx < 10 else -0.2,
                "farm_geometry_profile_id": "base" if idx < 12 else "faster_capture",
                "final_card_text": "private text must not be copied",
            }
        )
    _append_jsonl(derived / "paper_signal_training.jsonl", rows)

    summary = build_paper_product_quality_report(tmp_path, now=10_000.0)

    assert summary["schema"] == "paper_product_quality_report.v1"
    assert summary["active_trades"] == 3
    assert summary["active_live_ready"] == 0
    assert summary["operator_action"] == "strict_main_waiting_for_active_pfr_candidates"
    assert summary["active_by_source"] == {"farm": 3}
    assert summary["active_live_blockers"] == {"missing_ready_strategy_id": 2}
    assert summary["total_live_blockers"] == {"missing_ready_strategy_id": 25}
    assert summary["telegram"]["eligible_cards"] == 3
    assert summary["telegram"]["target_recipients"] == 2
    assert summary["telegram"]["duplicate_messages"] == 3
    assert summary["telegram"]["duplicate_cards"] == 2
    assert summary["telegram"]["sent_previews_total"] == 2
    assert summary["pfr_funnel"]["catalog_ready"] == 43
    assert summary["pfr_funnel"]["catalog_rejected_quality"] == 10
    assert summary["pfr_funnel"]["bridge_active_source_signals"] == 3
    assert summary["pfr_funnel"]["bridge_instructions"] == 0
    assert summary["pfr_funnel"]["bridge_validated_instructions"] == 0
    assert summary["pfr_funnel"]["bridge_skip_reasons"] == {"missing_ready_strategy_id": 3}
    assert summary["pfr_funnel"]["last_cycle_pfr_generated"] == 0
    assert summary["pfr_funnel"]["last_cycle_pfr_counts"] == {
        "pfr_records_loaded": 53,
        "pfr_passed_quality": 43,
        "pfr_duplicate_setup_variant": 32,
        "pfr_unique_setups": 11,
        "pfr_rejected_quality": 10,
        "pfr_rejected:no_breakout": 6,
    }
    assert summary["pfr_funnel"]["live_trigger_reasons"] == {
        "pfr_rejected:no_breakout": 6,
        "pfr_rejected:no_fade_signal:move_pct_threshold=8.0": 5,
    }
    assert summary["pfr_funnel"]["near_trigger_counts"] == {
        "pfr_near_trigger:fade_gap_gt_2pct": 5,
        "pfr_near_trigger:breakout_gap_le_0_5pct": 4,
    }
    assert summary["pfr_funnel"]["cycle_resource_reasons"] == {
        "stale_data": 4,
        "network_fetch_limit_reached": 1,
    }
    assert summary["pfr_trigger_state"] == {
        "state": "waiting_for_live_trigger",
        "catalog_ready": 43,
        "bridge_instructions": 0,
        "bridge_validated_instructions": 0,
        "last_cycle_generated": 0,
        "last_cycle_pfr_generated": 0,
        "top_reasons": {
            "pfr_rejected:no_breakout": 6,
            "pfr_rejected:no_fade_signal:move_pct_threshold=8.0": 5,
        },
    }
    assert summary["active_signal_lifecycle"] == {
        "active": 3,
        "by_status": {"armed": 2, "opened_paper": 1},
        "by_outcome_result": {"pending_arm": 1, "pending_open": 1},
        "pending_outcomes": 2,
        "active_without_outcome": 1,
        "oldest_age_hours": 2.5,
        "next_expiry_hours": 0.5,
        "overdue_expiry": 1,
        "age_buckets": {"le_1h": 2, "le_3h": 1},
        "expiry_buckets": {"le_1h": 1, "le_3h": 1, "overdue": 1},
        "terminal_training_backlog": 0,
    }
    assert summary["families"][0]["family"] == "early_tp_tactical"
    assert summary["families"][0]["rows"] == 22
    assert summary["families"][0]["quality_label"] == "candidate_watch"
    assert summary["geometry_profiles"][0]["profile_id"] == "base"
    assert summary["geometry_profiles"][0]["rows"] == 12
    assert summary["geometry_profiles"][1]["profile_id"] == "faster_capture"
    assert summary["geometry_profiles"][1]["rows"] == 10
    assert "items" not in summary
    raw = (derived / "paper_product_quality_report.json").read_text(encoding="utf-8")
    assert "private_signal" not in raw
    assert "private text must not be copied" not in raw
    markdown = (derived / "paper_product_quality_report.md").read_text(encoding="utf-8")
    assert "live-trigger state: waiting_for_live_trigger" in markdown
    assert "pending active outcomes: 2" in markdown
    assert "next active expiry hours: 0.5" in markdown
    assert "## Farm Geometry Profiles" in markdown
    assert "faster_capture" in markdown


def test_quality_report_flags_missing_pfr_context_as_fix_needed(tmp_path):
    derived = tmp_path / "state" / "derived"
    _write_json(
        derived / "paper_product_trades.json",
        {
            "schema": "paper_product_trade_ledger.v1",
            "trades": 1,
            "active_trades": 1,
            "active_live_ready": 0,
            "active_live_blocked": 1,
            "active_by_source": {"pfr_farm": 1},
            "by_live_block": {"missing_ready_strategy_id": 1},
        },
    )

    summary = build_paper_product_quality_report(tmp_path)

    assert summary["operator_action"] == "fix_pfr_context_missing_ready_strategy_id"


def test_quality_report_has_no_live_order_or_telegram_imports():
    from pathlib import Path

    text = Path("src/research_lab/paper_product_quality_report.py").read_text(encoding="utf-8")

    forbidden = (
        "okx_client",
        "ccxt",
        "order_exec",
        "live_engine",
        "auto_trade",
        "dotenv",
        "src.utils.telegram",
        "paper_telegram_sender",
        "paper_telegram_transport",
    )
    for marker in forbidden:
        assert marker not in text
