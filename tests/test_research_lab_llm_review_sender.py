# -*- coding: utf-8 -*-

import json

from src.research_lab.llm_review_sender import (
    NullReviewSender,
    SendResult,
    daily_cap,
    evaluate_send_gates,
    record_send_intent,
    send_review_pack,
)


class _FakeSender:
    name = "fake"
    configured = True

    def __init__(self):
        self.calls = []

    def send(self, pack, *, meta):
        self.calls.append((pack, meta))
        return SendResult("sent", self.name, "ok", "fake provider send")


def _gates(**over):
    base = dict(dry_run=False, send_requested=True, enabled=True, provider_configured=True, cap=5.0, spent_today=0.0)
    base.update(over)
    return evaluate_send_gates(**base)


def test_all_gates_pass():
    assert _gates().allowed is True


def test_dry_run_blocks():
    assert _gates(dry_run=True).reason == "dry_run_active"


def test_send_flag_required():
    assert _gates(send_requested=False).reason == "send_flag_not_set"


def test_env_enable_required():
    assert _gates(enabled=False).reason.startswith("env_")


def test_provider_required():
    assert _gates(provider_configured=False).reason == "no_provider_configured"


def test_budget_cap_required():
    assert _gates(cap=None).reason == "no_daily_budget_cap"
    assert _gates(cap=0).reason == "no_daily_budget_cap"


def test_budget_exhausted_blocks():
    assert _gates(cap=2.0, spent_today=2.0).reason == "daily_budget_exhausted"


def test_default_does_not_call_sender():
    sender = _FakeSender()
    decision, result = send_review_pack(
        {"x": 1}, sender=sender, dry_run=False, send_requested=False, cap=5.0, enabled=True,
    )
    assert decision.allowed is False
    assert result is None
    assert sender.calls == []


def test_send_without_env_does_not_call_sender():
    sender = _FakeSender()
    decision, result = send_review_pack(
        {"x": 1}, sender=sender, dry_run=False, send_requested=True, cap=5.0, enabled=False,
    )
    assert decision.reason.startswith("env_")
    assert sender.calls == []


def test_env_without_send_does_not_call_sender():
    sender = _FakeSender()
    _, result = send_review_pack(
        {"x": 1}, sender=sender, dry_run=False, send_requested=False, cap=5.0, enabled=True,
    )
    assert result is None
    assert sender.calls == []


def test_cost_cap_blocks_sending():
    sender = _FakeSender()
    decision, result = send_review_pack(
        {"x": 1}, sender=sender, dry_run=False, send_requested=True, cap=2.0, spent_today=5.0, enabled=True,
    )
    assert decision.reason == "daily_budget_exhausted"
    assert sender.calls == []


def test_configured_sender_called_only_when_all_gates_pass():
    sender = _FakeSender()
    decision, result = send_review_pack(
        {"x": 1}, sender=sender, dry_run=False, send_requested=True, cap=5.0, spent_today=0.0, enabled=True,
    )
    assert decision.allowed is True
    assert result is not None and result.status == "sent"
    assert len(sender.calls) == 1


def test_null_sender_never_sends():
    sender = NullReviewSender()
    assert sender.configured is False
    result = sender.send({"x": 1}, meta={})
    assert result.status == "not_sent"
    # Even with every other gate satisfied, an unconfigured provider blocks the send.
    decision, send_result = send_review_pack(
        {"x": 1}, sender=sender, dry_run=False, send_requested=True, cap=5.0, enabled=True,
    )
    assert decision.reason == "no_provider_configured"
    assert send_result is None


def test_daily_cap_parsing():
    assert daily_cap({"STRATEGY_LAB_LLM_DAILY_CAP": "1.5"}) == 1.5
    assert daily_cap({"STRATEGY_LAB_LLM_DAILY_CAP": "0"}) is None
    assert daily_cap({"STRATEGY_LAB_LLM_DAILY_CAP": "nope"}) is None
    assert daily_cap({}) is None


def test_record_send_intent_writes_private_and_no_keys(tmp_path):
    from src.research_lab.llm_review_sender import SendDecision

    path = record_send_intent(
        tmp_path,
        decision=SendDecision(False, "no_provider_configured"),
        pack_label="reports/llm_review/pack_x.json",
        provider="null",
        result=None,
    )
    assert path.exists()
    assert path.parent == tmp_path / "reports" / "llm_review"
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["reason"] == "no_provider_configured"
    assert row["allowed"] is False
    assert "key" not in path.read_text(encoding="utf-8").lower()
