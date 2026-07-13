from __future__ import annotations

import json

from src.research_lab.llm_invocation_ledger import (
    invocation_summary,
    preflight_invocation,
    record_invocation,
)
from src.research_lab.llm_provider import LLMUsage


class _Provider:
    configured = True

    def __init__(self, name="ollama", model="calculator-swarm"):
        self.name = name
        self.model_name = model


def test_preflight_deduplicates_completed_invocation(tmp_path):
    provider = _Provider()
    permit = preflight_invocation(
        tmp_path,
        role_id="calculator_context_classifier",
        source_ref="fp1",
        input_payload={"safe": 1},
        provider=provider,
        local_only=True,
    )
    assert permit.allowed is True
    record_invocation(
        tmp_path,
        permit,
        status="accepted",
        usage=LLMUsage(provider="ollama", model="calculator-swarm", total_tokens=12),
    )

    duplicate = preflight_invocation(
        tmp_path,
        role_id="calculator_context_classifier",
        source_ref="fp1",
        input_payload={"safe": 1},
        provider=provider,
        local_only=True,
    )

    assert duplicate.allowed is False
    assert duplicate.reason == "duplicate_completed"
    assert invocation_summary(tmp_path)["total_tokens"] == 12


def test_local_role_rejects_cloud_and_unallowlisted_model(tmp_path):
    cloud = preflight_invocation(
        tmp_path,
        role_id="calculator_context_classifier",
        source_ref="fp1",
        input_payload={},
        provider=_Provider("alibaba", "qwen-plus"),
        local_only=True,
    )
    wrong_model = preflight_invocation(
        tmp_path,
        role_id="calculator_context_classifier",
        source_ref="fp1",
        input_payload={},
        provider=_Provider("ollama", "other"),
        local_only=True,
    )

    assert cloud.reason == "local_provider_required"
    assert wrong_model.reason == "local_model_not_allowlisted"

    tagged = preflight_invocation(
        tmp_path,
        role_id="calculator_context_classifier",
        source_ref="fp2",
        input_payload={},
        provider=_Provider("ollama", "calculator-swarm:latest"),
        local_only=True,
    )
    assert tagged.allowed is True


def test_circuit_opens_after_three_provider_errors(tmp_path):
    provider = _Provider()
    for index in range(3):
        permit = preflight_invocation(
            tmp_path,
            role_id="outcome_reviewer",
            source_ref=f"case{index}",
            input_payload={"index": index},
            provider=provider,
        )
        assert permit.allowed is True
        record_invocation(tmp_path, permit, status="provider_error", problems=["timeout"])

    blocked = preflight_invocation(
        tmp_path,
        role_id="outcome_reviewer",
        source_ref="case4",
        input_payload={"index": 4},
        provider=provider,
    )

    assert blocked.allowed is False
    assert blocked.reason == "circuit_open"
    rows = [json.loads(line) for line in (tmp_path / "state" / "llm_advice" / "invocations.jsonl").read_text().splitlines()]
    assert len(rows) == 3
    assert all(row["secrets_exposed"] is False for row in rows)


def test_schema_rejection_gets_bounded_retries(tmp_path):
    provider = _Provider("alibaba", "qwen-plus")
    for attempt in range(3):
        permit = preflight_invocation(
            tmp_path,
            role_id="outcome_reviewer",
            source_ref="case-retry",
            input_payload={"same": True},
            provider=provider,
        )
        assert permit.allowed is True
        record_invocation(tmp_path, permit, status="schema_rejected", problems=["bad schema"])

    exhausted = preflight_invocation(
        tmp_path,
        role_id="outcome_reviewer",
        source_ref="case-retry",
        input_payload={"same": True},
        provider=provider,
    )
    assert exhausted.allowed is False
    assert exhausted.reason == "retry_exhausted"


def test_provider_configuration_failure_can_recover(tmp_path):
    provider = _Provider("alibaba", "qwen-plus")
    provider.configured = False
    blocked = preflight_invocation(
        tmp_path,
        role_id="outcome_reviewer",
        source_ref="case-config",
        input_payload={"same": True},
        provider=provider,
    )
    assert blocked.reason == "provider_not_configured"
    record_invocation(tmp_path, blocked, status="blocked", problems=[blocked.reason])

    provider.configured = True
    recovered = preflight_invocation(
        tmp_path,
        role_id="outcome_reviewer",
        source_ref="case-config",
        input_payload={"same": True},
        provider=provider,
    )
    assert recovered.allowed is True
