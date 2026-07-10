from __future__ import annotations

import json

from src.research_lab.paper_acceptance import evaluate_acceptance, start_acceptance
from src.research_lab.paper_account_ledger import build_paper_account_ledger


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _seed(tmp_path):
    derived = tmp_path / "state" / "derived"
    _write_jsonl(derived / "paper_signal_training.jsonl", [])
    _write_jsonl(derived / "trade_thesis_events.jsonl", [])
    _write_json(
        derived / "paper_telegram_card_ledger.json",
        {"items": [], "paper_only": True, "execution_allowed": False},
    )
    _write_json(
        derived / "paper_lineage.json",
        {
            "envelopes": 1,
            "conflicts": 0,
            "main_without_trade": 0,
            "terminal_without_training": 0,
            "valid": True,
        },
    )
    _write_json(derived / "outcome_retest_results.json", {"results": 0})
    _write_json(derived / "outcome_retest_specs.json", {"specs": 1, "queueable": 1})
    _write_json(derived / "trading_policy_calibration.json", {"trusted_terminal_rows": 0})
    _write_json(
        tmp_path / "state" / "farm_loop_status.json",
        {"stage": "sleep", "paper_only": True, "execution_allowed": False, "details": {"errors": 0}},
    )
    build_paper_account_ledger(
        tmp_path,
        [
            {
                "paper_trade_id": "trade1",
                "source_signal_id": "sig1",
                "validation_tier": "validated_pfr",
                "okx_inst_id": "X-USDT-SWAP",
                "timeframe": "15m",
                "side": "long",
                "boundary_ts": 1,
                "signal_status": "opened_paper",
                "outcome": {"opened_at_bar_ts": 2},
            }
        ],
    )


def test_acceptance_starts_private_baseline_and_passes_after_required_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTO_TRADE", raising=False)
    _seed(tmp_path)
    started = start_acceptance(tmp_path, hours=24, now=1_000)
    assert started["paper_only"] is True
    assert started["execution_allowed"] is False

    derived = tmp_path / "state" / "derived"
    _write_jsonl(
        derived / "paper_signal_training.jsonl",
        [
            {
                "lifecycle_schema": "PaperSignalLifecycle.v2",
                "opened_at_bar_ts": 2,
                "bars_held": 3,
                "result": "take",
            }
        ],
    )
    _write_jsonl(
        derived / "trade_thesis_events.jsonl",
        [{"event_type": "scenario_closed", "event_id": "close1"}],
    )
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"png")
    _write_json(
        derived / "paper_telegram_card_ledger.json",
        {"items": [{"consumer_status": "scenario_closed", "chart_path": str(chart)}]},
    )
    _write_json(derived / "outcome_retest_results.json", {"results": 1})

    report = evaluate_acceptance(tmp_path, now=1_000 + 24 * 3600)

    assert report["passed"] is True
    assert all(report["checks"].values())
    assert report["deltas"]["trusted_lifecycle_rows"] == 1


def test_acceptance_blocks_auto_trade_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_TRADE", "1")
    _seed(tmp_path)

    try:
        start_acceptance(tmp_path, now=1_000)
    except RuntimeError as exc:
        assert "AUTO_TRADE" in str(exc)
    else:
        raise AssertionError("acceptance must reject AUTO_TRADE")
