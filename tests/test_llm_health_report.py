# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import llm_health_report as H  # noqa: E402


def test_llm_health_report_summarizes_budget_and_usage(tmp_path, monkeypatch):
    budget = tmp_path / "llm_budget.jsonl"
    reasoning = tmp_path / "scanner_reasoning.jsonl"
    budget.write_text(
        json.dumps({"ts": "2026-06-12T01:00:00Z", "n_cards": 2, "n_dropped": 1,
                    "n_llm_fail": 0, "total_tokens": 300, "cost_rub": 1.5}) + "\n",
        encoding="utf-8",
    )
    reasoning.write_text(
        json.dumps(
            {
                "ts": "2026-06-12T01:00:00Z",
                "usage": [
                    {
                        "provider": "alibaba",
                        "model": "cheap-model",
                        "role": "cheap",
                        "total_tokens": 100,
                        "cost_usd": 0.001,
                        "cost_rub": 0.09,
                        "status": "ok",
                    },
                    {
                        "provider": "alibaba",
                        "model": "chief-model",
                        "role": "chief",
                        "total_tokens": 0,
                        "cost_usd": 0,
                        "cost_rub": 0,
                        "status": "budget_skipped",
                        "error_type": "LLM_MAX_CHIEF_PER_SCAN",
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(H, "BUDGET", budget)
    monkeypatch.setattr(H, "REASONING", reasoning)

    report = H.summarize("2026-06-12")

    assert report["totals"]["passes"] == 1
    assert report["totals"]["cost_rub"] == 1.5
    assert report["errors"] == {"LLM_MAX_CHIEF_PER_SCAN": 1}
    assert any(row["budget_skipped"] == 1 for row in report["models"])


def test_live_probe_budget_is_persisted(monkeypatch):
    captured = {}

    def fake_write_budget(row):
        captured.update(row)

    monkeypatch.setattr(H.J, "write_budget", fake_write_budget)

    H._write_probe_budget(
        [
            {"ok": True, "usage": {"total_tokens": 10, "cost_rub": 0.1}},
            {"ok": False, "usage": {"total_tokens": 0, "cost_rub": 0.0}},
        ]
    )

    assert captured["source"] == "llm_health_probe"
    assert captured["n_probe_calls"] == 2
    assert captured["n_llm_fail"] == 1
    assert captured["total_tokens"] == 10
    assert captured["cost_rub"] == 0.1
