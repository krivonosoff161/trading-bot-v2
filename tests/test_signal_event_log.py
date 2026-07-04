import json
from pathlib import Path

from src.utils.signal_event_log import record_manual_analysis_event, record_signal_event


def test_signal_event_writes_sanitized_append_only_row(tmp_path):
    path = tmp_path / "signal_events.jsonl"

    record_signal_event(
        source="manual_telegram",
        mode="manual_analysis",
        decision="ENTRY",
        symbol="BTC-USDT",
        chat_id="123",
        provider="alibaba",
        model="qwen",
        prompt_version="v1",
        artifacts={"summary": str(tmp_path / "summary.txt")},
        path=path,
    )

    row = json.loads(path.read_text(encoding="utf-8"))

    assert row["schema"] == "signal_event.v1"
    assert row["source"] == "manual_telegram"
    assert row["decision"] == "ENTRY"
    assert row["paper_only"] is True
    assert row["execution_allowed"] is False
    assert row["provider"] == "alibaba"
    assert "summary" in row["artifacts"]
    assert "secret" not in path.read_text(encoding="utf-8").lower()


def test_manual_analysis_event_extracts_decision_package(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    summary = run_dir / "BTC-USDT_client_summary.txt"
    chart = run_dir / "BTC-USDT_chart.png"
    report = run_dir / "BTC-USDT_report.md"
    snapshot_path = run_dir / "BTC-USDT_snapshot.json"
    for path in (summary, chart, report, snapshot_path):
        path.write_text("artifact", encoding="utf-8")

    snapshot = {
        "symbol": "BTC-USDT",
        "llm_context": {
            "entry_signal": "ENTRY",
            "side": "buy",
            "entry_price": 100.0,
            "sl_price": 96.0,
            "tp1_price": 108.0,
            "tp2_price": 116.0,
            "max_hold_minutes": 240,
            "regime": "DRIFT",
            "trade_style": "SWING",
            "risk_pct": 4.0,
        },
    }
    out = tmp_path / "signal_events.jsonl"

    record_manual_analysis_event(
        chat_id="123",
        symbol="BTC-USDT",
        captured_at="2026-06-28T12:00:00Z",
        snapshot=snapshot,
        run_dir=run_dir,
        summary_path=summary,
        chart_path=chart,
        report_path=report,
        snapshot_path=snapshot_path,
        message_id=42,
        path=out,
    )

    row = json.loads(out.read_text(encoding="utf-8"))

    assert row["source"] == "manual_telegram"
    assert row["decision"] == "ENTRY"
    assert row["symbol"] == "BTC-USDT"
    assert row["side"] == "buy"
    assert row["entry_zone"] == [100.0, 100.0]
    assert row["stop_loss"] == 96.0
    assert row["take_profit_plan"][0]["price"] == 108.0
    assert row["max_hold_minutes"] == 240
    assert row["message_id"] == 42
    assert row["artifacts"]["chart"] == str(chart)


def test_manual_analysis_event_records_no_trade_with_reason(tmp_path):
    out = tmp_path / "signal_events.jsonl"
    snapshot = {
        "symbol": "BOME-USDT",
        "llm_context": {
            "entry_signal": "NO_TRADE",
            "drop_reason": "middle_of_range",
            "regime": "RANGING",
        },
    }

    record_manual_analysis_event(
        chat_id="123",
        symbol="BOME-USDT",
        captured_at="2026-06-28T12:00:00Z",
        snapshot=snapshot,
        run_dir=Path("logs/users/123/analyses/x"),
        path=out,
    )

    row = json.loads(out.read_text(encoding="utf-8"))

    assert row["decision"] == "NO_TRADE"
    assert "middle_of_range" in row["reason_codes"]
    assert row["entry_zone"] == []
    assert row["paper_only"] is True
