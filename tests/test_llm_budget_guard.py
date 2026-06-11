# -*- coding: utf-8 -*-
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.utils import llm_budget_guard as B  # noqa: E402
from src.utils import llm_client as L  # noqa: E402


def test_daily_spend_reads_scanner_budget_log(tmp_path):
    log = tmp_path / "llm_budget.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-06-12T01:00:00Z", "cost_rub": 1.25}),
                json.dumps({"ts": "2026-06-12T02:00:00Z", "cost_rub": 2.75}),
                json.dumps({"ts": "2026-06-11T02:00:00Z", "cost_rub": 10}),
                "not-json",
            ]
        ),
        encoding="utf-8",
    )
    assert B.daily_spend_rub("2026-06-12", path=log) == 4.0


def test_budget_guard_blocks_when_stop_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(B, "BUDGET_LOG", tmp_path / "missing.jsonl")
    monkeypatch.setattr(B, "today_utc", lambda: "2026-06-12")
    monkeypatch.setenv("LLM_STOP_ON_BUDGET", "true")
    monkeypatch.setenv("LLM_SCAN_RUB_CAP", "0.01")
    B.reset_session()

    blocked, reason, ctx = B.should_block("cheap", estimated_tokens=1000, estimated_cost_rub=0.02)

    assert blocked is True
    assert reason == "LLM_SCAN_RUB_CAP"
    assert ctx["projected_scan_rub"] == 0.02


def test_budget_guard_allows_when_stop_disabled(monkeypatch):
    monkeypatch.setenv("LLM_STOP_ON_BUDGET", "false")
    monkeypatch.setenv("LLM_SCAN_RUB_CAP", "0.01")
    B.reset_session()

    blocked, reason, _ctx = B.should_block("cheap", estimated_tokens=1000, estimated_cost_rub=99.0)

    assert blocked is False
    assert reason == ""


def test_llm_client_budget_skip_happens_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(B, "BUDGET_LOG", tmp_path / "missing.jsonl")
    monkeypatch.setattr(B, "today_utc", lambda: "2026-06-12")
    monkeypatch.setattr(L, "PROVIDER", "alibaba")
    monkeypatch.setenv("LLM_STOP_ON_BUDGET", "true")
    monkeypatch.setenv("LLM_SCAN_RUB_CAP", "0.0001")
    B.reset_session()

    text, usage = asyncio.run(
        L.call("cheap", "system", "user " * 2000, json_mode=True, max_tokens=700)
    )

    assert text is None
    assert usage["status"] == "budget_skipped"
    assert usage["error_type"] == "LLM_SCAN_RUB_CAP"
    assert usage["total_tokens"] == 0


def test_budget_guard_chief_call_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(B, "BUDGET_LOG", tmp_path / "missing.jsonl")
    monkeypatch.setattr(B, "today_utc", lambda: "2026-06-12")
    monkeypatch.setenv("LLM_STOP_ON_BUDGET", "true")
    monkeypatch.setenv("LLM_MAX_CHIEF_PER_SCAN", "1")
    B.reset_session()
    B.record_usage("chief", 100, 0.01)

    blocked, reason, _ctx = B.should_block("chief", estimated_tokens=10, estimated_cost_rub=0.01)

    assert blocked is True
    assert reason == "LLM_MAX_CHIEF_PER_SCAN"
