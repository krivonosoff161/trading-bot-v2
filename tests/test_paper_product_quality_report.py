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
            "by_live_block": {"missing_ready_strategy_id": 25},
            "items": [{"source_signal_id": "private_signal"}],
        },
    )
    _write_json(derived / "paper_telegram_preview.json", {"schema": "paper_telegram_preview.v1", "rendered": 3})
    _write_json(
        derived / "paper_telegram_delivery.json",
        {
            "schema": "paper_telegram_delivery.v1",
            "eligible": 3,
            "sent": 0,
            "duplicates": 3,
            "errors": 0,
            "configured": True,
            "sends_network": True,
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
                "final_card_text": "private text must not be copied",
            }
        )
    _append_jsonl(derived / "paper_signal_training.jsonl", rows)

    summary = build_paper_product_quality_report(tmp_path)

    assert summary["schema"] == "paper_product_quality_report.v1"
    assert summary["active_trades"] == 3
    assert summary["active_live_ready"] == 0
    assert summary["operator_action"] == "fix_promotion_gap_missing_ready_strategy_id"
    assert summary["telegram"]["sent_previews_total"] == 2
    assert summary["families"][0]["family"] == "early_tp_tactical"
    assert summary["families"][0]["rows"] == 22
    assert summary["families"][0]["quality_label"] == "candidate_watch"
    assert "items" not in summary
    raw = (derived / "paper_product_quality_report.json").read_text(encoding="utf-8")
    assert "private_signal" not in raw
    assert "private text must not be copied" not in raw


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
