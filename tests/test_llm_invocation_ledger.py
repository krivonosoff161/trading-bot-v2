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


class _EndpointProvider(_Provider):
    def __init__(self, base_url: str, name="ollama", model="calculator-swarm"):
        super().__init__(name, model)
        self.base_url = base_url


def test_local_only_ollama_rejects_remote_endpoint_identity(tmp_path):
    remote = _EndpointProvider("https://remote.example/v1")

    permit = preflight_invocation(
        tmp_path,
        role_id="calculator_context_classifier",
        source_ref="fp-remote",
        input_payload={"safe": 1},
        provider=remote,
        local_only=True,
    )

    assert permit.allowed is False
    assert permit.reason in {
        "local_endpoint_required",
        "local_endpoint_identity_required",
    }


def test_local_only_ollama_rejects_injected_redirect_identity(tmp_path):
    provider = _EndpointProvider("http://127.0.0.1:11434/v1")
    provider.redirected_to = "https://remote.example/v1"

    permit = preflight_invocation(
        tmp_path,
        role_id="calculator_context_classifier",
        source_ref="fp-redirect",
        input_payload={"safe": 1},
        provider=provider,
        local_only=True,
    )

    assert permit.allowed is False
    assert permit.reason == "local_endpoint_required"
    assert "redirect_identity_v1" in permit.boundary_checks


def test_local_only_requires_immutable_endpoint_identity_not_mutable_name(tmp_path):
    provider = _Provider("ollama", "calculator-swarm")

    permit = preflight_invocation(
        tmp_path,
        role_id="calculator_context_classifier",
        source_ref="fp-no-endpoint",
        input_payload={"safe": 1},
        provider=provider,
        local_only=True,
    )

    assert permit.allowed is False
    assert permit.reason == "local_endpoint_identity_required"


def test_local_only_loopback_endpoint_is_bound_into_invocation_id(tmp_path):
    provider = _EndpointProvider("http://127.0.0.1:11434/v1")
    same_host_alias = _EndpointProvider("http://localhost:11434/v1")
    other_port = _EndpointProvider("http://127.0.0.1:11435/v1")

    first = preflight_invocation(
        tmp_path,
        role_id="calculator_context_classifier",
        source_ref="fp-local",
        input_payload={"safe": 1},
        provider=provider,
        local_only=True,
    )
    alias = preflight_invocation(
        tmp_path,
        role_id="calculator_context_classifier",
        source_ref="fp-local",
        input_payload={"safe": 1},
        provider=same_host_alias,
        local_only=True,
    )
    changed_endpoint = preflight_invocation(
        tmp_path,
        role_id="calculator_context_classifier",
        source_ref="fp-local",
        input_payload={"safe": 1},
        provider=other_port,
        local_only=True,
    )

    assert first.allowed is True
    assert alias.allowed is True
    assert first.invocation_id == alias.invocation_id
    assert changed_endpoint.invocation_id != first.invocation_id


def test_preflight_deduplicates_completed_invocation(tmp_path):
    provider = _EndpointProvider("http://127.0.0.1:11434/v1")
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
        provider=_EndpointProvider("http://127.0.0.1:11434/v1", "ollama", "other"),
        local_only=True,
    )

    assert cloud.reason == "local_provider_required"
    assert wrong_model.reason == "local_model_not_allowlisted"

    tagged = preflight_invocation(
        tmp_path,
        role_id="calculator_context_classifier",
        source_ref="fp2",
        input_payload={},
        provider=_EndpointProvider("http://127.0.0.1:11434/v1", "ollama", "calculator-swarm:latest"),
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
