from __future__ import annotations

import json

from src.research_lab.llm_invocation_ledger import (
    finish_transport_invocation,
    invocation_summary,
    make_trace_context,
    preflight_invocation,
    record_invocation,
    start_transport_invocation,
)
from src.research_lab.llm_boundary_identity import endpoint_identity_from_url
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


def test_transport_trace_correlates_start_and_response_without_raw_content(
    tmp_path,
):
    context = make_trace_context(
        tmp_path,
        surface="scanner.layer_agent",
        source_ref="doc-1",
    )
    permit = start_transport_invocation(
        context,
        role_id="cheap",
        provider="alibaba",
        model="synthetic-model",
        input_payload={
            "system": "SYNTHETIC_PROMPT_MARKER",
            "user": "SYNTHETIC_USER_MARKER",
        },
        provider_class="synthetic.transport",
    )
    finish_transport_invocation(
        context,
        permit,
        status="accepted",
        output_text="SYNTHETIC_RESPONSE_MARKER",
        usage={
            "provider": "alibaba",
            "model": "synthetic-model",
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "cost_rub": 0.25,
        },
        response_received=True,
    )

    path = tmp_path / "state" / "llm_advice" / "invocations.jsonl"
    raw = path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in raw.splitlines()]
    assert len(rows) == 2
    assert [row["event_phase"] for row in rows] == ["started", "completed"]
    assert {row["correlation_id"] for row in rows} == {context.correlation_id}
    assert rows[1]["output_hash"]
    assert rows[1]["response_received"] is True
    assert rows[0]["source_ref"].startswith("sha256:")
    assert rows[0]["source_ref"] == rows[1]["source_ref"]
    assert "doc-1" not in raw
    assert "SYNTHETIC_PROMPT_MARKER" not in raw
    assert "SYNTHETIC_USER_MARKER" not in raw
    assert "SYNTHETIC_RESPONSE_MARKER" not in raw
    summary = invocation_summary(tmp_path)
    assert summary["invocations"] == 1
    assert summary["invocation_events"] == 2
    assert summary["by_status"] == {"accepted": 1}
    assert summary["total_tokens"] == 15


def test_trace_context_without_source_ref_persists_only_a_hash(tmp_path):
    context = make_trace_context(
        tmp_path,
        surface="telegram.education",
        source_payload={"question": "SYNTHETIC_PRIVATE_QUESTION"},
    )

    assert context.source_ref.startswith("sha256:")
    assert "SYNTHETIC_PRIVATE_QUESTION" not in context.source_ref


def test_trace_context_hashes_supplied_source_reference(tmp_path):
    context = make_trace_context(
        tmp_path,
        surface="public_news.editor",
        source_ref="SYNTHETIC_SOURCE_REFERENCE",
    )

    assert context.source_ref.startswith("sha256:")
    assert "SYNTHETIC_SOURCE_REFERENCE" not in context.source_ref


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


def test_malformed_local_endpoint_fails_closed_without_value_error(tmp_path):
    malformed = (
        "http://127.0.0.1:99999/v1",
        "http://127.0.0.1:not-a-port/v1",
        "http://[::1/v1",
    )

    for index, base_url in enumerate(malformed):
        identity = endpoint_identity_from_url(base_url)
        assert identity.loopback_proven is False
        assert identity.problems

        permit = preflight_invocation(
            tmp_path,
            role_id="calculator_context_classifier",
            source_ref=f"fp-malformed-{index}",
            input_payload={"safe": 1},
            provider=_EndpointProvider(base_url),
            local_only=True,
        )

        assert permit.allowed is False
        assert permit.reason == "invalid_endpoint"
        assert any(
            problem.startswith("invalid_endpoint")
            for problem in permit.endpoint_identity["problems"]
        )


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
