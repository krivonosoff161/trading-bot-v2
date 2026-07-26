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
from src.research_lab.llm_invocation_ledger import make_trace_context  # noqa: E402


class _FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self):
        return {
            "choices": [{"message": {"content": "synthetic answer"}}],
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
            },
        }

    async def read(self):
        return b""


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def post(self, *_args, **_kwargs):
        return _FakeResponse()


class _RejectedResponse(_FakeResponse):
    status = 400

    async def read(self):
        return b"SYNTHETIC_PROVIDER_BODY_MARKER"


class _RejectedSession(_FakeSession):
    def post(self, *_args, **_kwargs):
        return _RejectedResponse()


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


def test_llm_client_records_correlated_synthetic_response(monkeypatch, tmp_path):
    monkeypatch.setattr(B, "BUDGET_LOG", tmp_path / "budget.jsonl")
    monkeypatch.setattr(L, "PROVIDER", "alibaba")
    monkeypatch.setattr(L, "_ALIBABA_KEY", "synthetic-test-key")
    monkeypatch.setattr(L.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setenv("LLM_STOP_ON_BUDGET", "false")
    B.reset_session()
    trace = make_trace_context(
        tmp_path,
        surface="scanner.layer_agent",
        source_ref="synthetic-doc",
    )

    text, usage = asyncio.run(
        L.call(
            "cheap",
            "synthetic system",
            "synthetic user",
            json_mode=True,
            max_tokens=50,
            trace_context=trace,
        )
    )

    assert text == "synthetic answer"
    assert usage["status"] == "ok"
    assert usage["correlation_id"] == trace.correlation_id
    assert usage["invocation_id"]
    rows = [
        json.loads(line)
        for line in (
            tmp_path / "state" / "llm_advice" / "invocations.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert [row["status"] for row in rows] == ["started", "accepted"]
    assert rows[-1]["response_received"] is True
    assert rows[-1]["output_hash"]
    assert rows[-1]["attempt_count"] == 1


def test_trace_start_failure_blocks_network(monkeypatch, tmp_path):
    called = False

    def fail_session():
        nonlocal called
        called = True
        return _FakeSession()

    monkeypatch.setattr(L, "PROVIDER", "alibaba")
    monkeypatch.setattr(L, "_ALIBABA_KEY", "synthetic-test-key")
    monkeypatch.setattr(L.aiohttp, "ClientSession", fail_session)
    monkeypatch.setattr(
        L,
        "start_transport_invocation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic")),
    )
    trace = make_trace_context(
        tmp_path,
        surface="scanner.chief",
        source_ref="synthetic-doc",
    )

    text, usage = asyncio.run(
        L.call("chief", "system", "user", trace_context=trace)
    )

    assert text is None
    assert usage["error_type"] == "trace_start_failed"
    assert called is False


def test_trace_finish_failure_hides_unrecorded_response(monkeypatch, tmp_path):
    monkeypatch.setattr(B, "BUDGET_LOG", tmp_path / "budget.jsonl")
    monkeypatch.setattr(L, "PROVIDER", "alibaba")
    monkeypatch.setattr(L, "_ALIBABA_KEY", "synthetic-test-key")
    monkeypatch.setattr(L.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setenv("LLM_STOP_ON_BUDGET", "false")
    monkeypatch.setattr(
        L,
        "finish_transport_invocation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic")),
    )
    B.reset_session()
    trace = make_trace_context(
        tmp_path,
        surface="public_news.editor",
        source_ref="synthetic-item",
    )

    text, usage = asyncio.run(
        L.call("mid", "system", "user", trace_context=trace)
    )

    assert text is None
    assert usage["error_type"] == "trace_finish_failed"
    assert usage["correlation_id"] == trace.correlation_id


def test_http_error_body_is_not_logged_or_persisted(
    monkeypatch,
    tmp_path,
    capsys,
):
    monkeypatch.setattr(B, "BUDGET_LOG", tmp_path / "budget.jsonl")
    monkeypatch.setattr(L, "PROVIDER", "alibaba")
    monkeypatch.setattr(L, "_ALIBABA_KEY", "synthetic-test-key")
    monkeypatch.setattr(L.aiohttp, "ClientSession", _RejectedSession)
    monkeypatch.setenv("LLM_STOP_ON_BUDGET", "false")
    B.reset_session()
    trace = make_trace_context(
        tmp_path,
        surface="scanner.layer_agent",
        source_ref="synthetic-rejected-doc",
    )

    text, usage = asyncio.run(
        L.call("cheap", "system", "user", trace_context=trace)
    )

    captured = capsys.readouterr()
    ledger = (
        tmp_path / "state" / "llm_advice" / "invocations.jsonl"
    ).read_text(encoding="utf-8")
    assert text is None
    assert usage["error_type"] == "http_400"
    assert "HTTP 400" in captured.out
    assert "SYNTHETIC_PROVIDER_BODY_MARKER" not in captured.out
    assert "SYNTHETIC_PROVIDER_BODY_MARKER" not in ledger
    terminal = json.loads(ledger.splitlines()[-1])
    assert terminal["status"] == "provider_error"
    assert terminal["response_received"] is True
    assert terminal["attempt_count"] == 1
