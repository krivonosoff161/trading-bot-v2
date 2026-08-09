"""Canonical private invocation ledger for bounded LLM roles.

The ledger is a control-plane audit, not model memory. It fingerprints sanitized
inputs before a provider call, blocks duplicate work, enforces the local
calculator allowlist, and opens a small provider/role circuit breaker after
repeated errors. It stores hashes and usage metadata, never prompts or secrets.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
import uuid

from src.research_lab.lineage_contract import append_jsonl, stable_id, utc_now
from src.research_lab.runtime_storage_rotation import (
    maybe_runtime_storage_capability,
    llm_invocation_summary as indexed_invocation_summary,
    recent_semantic_statuses,
    semantic_key_exists,
    semantic_status_count,
)
from src.research_lab.llm_boundary_identity import EndpointIdentity, endpoint_identity_from_url
from src.research_lab.llm_provider import LLMUsage, ProposalProvider
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root

SCHEMA = "LLMInvocation.v1"
LOCAL_PROVIDER_NAMES = {"ollama", "ollama-local"}
TEST_PROVIDER_NAMES = {"synthetic"}
DEFAULT_LOCAL_MODEL_ALLOWLIST = ("calculator-swarm",)
CIRCUIT_FAILURE_LIMIT = 3
MAX_RETRYABLE_ATTEMPTS = 3
TERMINAL_CALL_STATUSES = {"accepted"}
RETRYABLE_CALL_STATUSES = {"provider_error", "schema_rejected"}


@dataclass(frozen=True)
class InvocationPermit:
    invocation_id: str
    role_id: str
    source_ref: str
    input_hash: str
    provider: str
    model: str
    allowed: bool
    reason: str
    created_at: str = field(default_factory=utc_now)
    provider_class: str = ""
    endpoint_identity: dict[str, Any] = field(default_factory=dict)
    boundary_checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class LLMTraceContext:
    """Safe correlation metadata supplied by an active LLM call site."""

    private_root: Path
    surface: str
    source_ref: str
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)


def ledger_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "llm_advice" / "invocations.jsonl"


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_label(value: str, *, fallback: str) -> str:
    normalized = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", normalized):
        return normalized
    return fallback


def make_trace_context(
    private_root: Path,
    *,
    surface: str,
    source_ref: str = "",
    source_payload: Mapping[str, Any] | None = None,
) -> LLMTraceContext:
    """Create correlation metadata without persisting raw input content."""
    supplied_surface = str(surface or "").strip()
    if not supplied_surface:
        raise ValueError("trace surface is required")
    normalized_surface = _safe_label(supplied_surface, fallback="")
    if not normalized_surface:
        raise ValueError("trace surface contains unsupported characters")
    supplied_ref = str(source_ref or "").strip()
    source_material: Mapping[str, Any] = (
        {"source_ref": supplied_ref}
        if supplied_ref
        else (source_payload or {})
    )
    normalized_ref = f"sha256:{_canonical_hash(source_material)}"
    return LLMTraceContext(
        private_root=Path(private_root),
        surface=normalized_surface,
        source_ref=normalized_ref,
    )


def make_runtime_trace_context(
    *,
    surface: str,
    source_ref: str = "",
    source_payload: Mapping[str, Any] | None = None,
) -> LLMTraceContext:
    """Resolve the canonical private research root without dotenv access."""
    private_root = resolve_private_root(
        os.getenv("TRADING_BOT_RESEARCH_ROOT") or DEFAULT_PRIVATE_ROOT
    )
    return make_trace_context(
        private_root,
        surface=surface,
        source_ref=source_ref,
        source_payload=source_payload,
    )


def provider_identity(provider: ProposalProvider) -> tuple[str, str]:
    name = str(getattr(provider, "name", "unknown") or "unknown").lower()
    model = str(getattr(provider, "model_name", "") or "")
    return name, model


def _local_model_allowed(model: str, allowlist: tuple[str, ...]) -> bool:
    allowed = set(allowlist)
    return model in allowed or model.split(":", 1)[0] in allowed


def _provider_base_url(provider: ProposalProvider) -> str:
    value = getattr(provider, "base_url", "") or getattr(provider, "_base_url", "")
    if value:
        return str(value)
    url = str(getattr(provider, "_url", "") or "")
    suffix = "/chat/completions"
    return url[: -len(suffix)] if url.endswith(suffix) else ""


def _provider_redirect_url(provider: ProposalProvider) -> str:
    for attr in ("redirect_url", "redirected_to", "final_url"):
        value = str(getattr(provider, attr, "") or "").strip()
        if value:
            return value
    return ""


def _permit(
    invocation_id: str,
    role_id: str,
    source_ref: str,
    input_hash: str,
    provider_name: str,
    model: str,
    allowed: bool,
    reason: str,
    *,
    provider_class: str,
    endpoint: EndpointIdentity | None = None,
    boundary_checks: tuple[str, ...] = (),
) -> InvocationPermit:
    return InvocationPermit(
        invocation_id,
        role_id,
        source_ref,
        input_hash,
        provider_name,
        model,
        allowed,
        reason,
        provider_class=provider_class,
        endpoint_identity=endpoint.to_dict() if endpoint is not None else {},
        boundary_checks=boundary_checks,
    )


def _rows(private_root: Path) -> list[dict[str, Any]]:
    path = ledger_path(private_root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("schema") == SCHEMA:
            rows.append(row)
    return rows


def preflight_invocation(
    private_root: Path,
    *,
    role_id: str,
    source_ref: str,
    input_payload: Mapping[str, Any],
    provider: ProposalProvider,
    local_only: bool = False,
    local_model_allowlist: tuple[str, ...] = DEFAULT_LOCAL_MODEL_ALLOWLIST,
) -> InvocationPermit:
    provider_name, model = provider_identity(provider)
    provider_class = type(provider).__name__
    input_hash = _canonical_hash(input_payload)
    endpoint: EndpointIdentity | None = None
    boundary_checks: tuple[str, ...] = ()
    if local_only and provider_name in LOCAL_PROVIDER_NAMES:
        base_url = _provider_base_url(provider)
        if not base_url:
            endpoint = endpoint_identity_from_url("")
        else:
            endpoint = endpoint_identity_from_url(base_url)
        boundary_checks = ("local_endpoint_identity_v1",)
        redirect_url = _provider_redirect_url(provider)
        if redirect_url:
            redirect_endpoint = endpoint_identity_from_url(redirect_url)
            if (
                not redirect_endpoint.loopback_proven
                or redirect_endpoint.normalized_base_url != endpoint.normalized_base_url
            ):
                endpoint = EndpointIdentity(
                    scheme=endpoint.scheme,
                    host=endpoint.host,
                    port=endpoint.port,
                    base_path=endpoint.base_path,
                    normalized_base_url=endpoint.normalized_base_url,
                    loopback_proven=False,
                    problems=(*endpoint.problems, "redirect_endpoint_not_local"),
                )
            boundary_checks = (*boundary_checks, "redirect_identity_v1")
    invocation_id = stable_id(
        "llminv",
        {
            "role_id": role_id,
            "source_ref": source_ref,
            "input_hash": input_hash,
            "provider": provider_name,
            "provider_class": provider_class,
            "model": model,
            "endpoint_identity": endpoint.to_dict() if endpoint is not None else {},
        },
        length=24,
    )
    rows = _rows(private_root)
    matching = [
        row for row in rows
        if str(row.get("invocation_id") or "") == invocation_id
        and str(row.get("event_phase") or "completed") != "started"
    ]
    capability = maybe_runtime_storage_capability(ledger_path(private_root))
    duplicate_completed = (
        semantic_key_exists(
            private_root,
            stream_id="llm.invocations",
            key_type="invocation_id",
            key_value=invocation_id,
            status="accepted",
        )
        if capability is not None
        else any(
        str(row.get("invocation_id") or "") == invocation_id
        and str(row.get("status") or "") in TERMINAL_CALL_STATUSES
        and str(row.get("event_phase") or "completed") != "started"
        for row in rows
        )
    )
    if duplicate_completed:
        return _permit(invocation_id, role_id, source_ref, input_hash, provider_name, model, False,
                       "duplicate_completed", provider_class=provider_class, endpoint=endpoint,
                       boundary_checks=boundary_checks)
    if not bool(getattr(provider, "configured", False)):
        return _permit(invocation_id, role_id, source_ref, input_hash, provider_name, model, False,
                       "provider_not_configured", provider_class=provider_class, endpoint=endpoint,
                       boundary_checks=boundary_checks)
    if local_only and provider_name not in LOCAL_PROVIDER_NAMES | TEST_PROVIDER_NAMES:
        return _permit(invocation_id, role_id, source_ref, input_hash, provider_name, model, False,
                       "local_provider_required", provider_class=provider_class, endpoint=endpoint,
                       boundary_checks=boundary_checks)
    if (
        local_only
        and provider_name in LOCAL_PROVIDER_NAMES
        and endpoint is not None
        and any(problem.startswith("invalid_endpoint") for problem in endpoint.problems)
    ):
        return _permit(invocation_id, role_id, source_ref, input_hash, provider_name, model, False,
                       "invalid_endpoint", provider_class=provider_class, endpoint=endpoint,
                       boundary_checks=boundary_checks)
    if local_only and provider_name in LOCAL_PROVIDER_NAMES and endpoint is not None and not endpoint.normalized_base_url:
        return _permit(invocation_id, role_id, source_ref, input_hash, provider_name, model, False,
                       "local_endpoint_identity_required", provider_class=provider_class, endpoint=endpoint,
                       boundary_checks=boundary_checks)
    if local_only and provider_name in LOCAL_PROVIDER_NAMES and endpoint is not None and not endpoint.loopback_proven:
        return _permit(invocation_id, role_id, source_ref, input_hash, provider_name, model, False,
                       "local_endpoint_required", provider_class=provider_class, endpoint=endpoint,
                       boundary_checks=boundary_checks)
    if local_only and provider_name in LOCAL_PROVIDER_NAMES and not _local_model_allowed(model, local_model_allowlist):
        return _permit(invocation_id, role_id, source_ref, input_hash, provider_name, model, False,
                       "local_model_not_allowlisted", provider_class=provider_class, endpoint=endpoint,
                       boundary_checks=boundary_checks)
    retryable_attempts = (
        semantic_status_count(
            private_root,
            stream_id="llm.invocations",
            key_type="invocation_id",
            key_value=invocation_id,
            statuses=RETRYABLE_CALL_STATUSES,
        )
        if capability is not None
        else sum(1 for row in matching if str(row.get("status") or "") in RETRYABLE_CALL_STATUSES)
    )
    if retryable_attempts >= MAX_RETRYABLE_ATTEMPTS:
        return _permit(invocation_id, role_id, source_ref, input_hash, provider_name, model, False,
                       "retry_exhausted", provider_class=provider_class, endpoint=endpoint,
                       boundary_checks=boundary_checks)
    recent_rows = [
        row
        for row in rows
        if row.get("role_id") == role_id and row.get("provider") == provider_name
        and str(row.get("event_phase") or "completed") != "started"
    ][-CIRCUIT_FAILURE_LIMIT:]
    recent = (
        recent_semantic_statuses(
            private_root,
            stream_id="llm.invocations",
            role_id=role_id,
            provider=provider_name,
            limit=CIRCUIT_FAILURE_LIMIT,
        )
        if capability is not None
        else [str(row.get("status") or "") for row in recent_rows]
    )
    if len(recent) >= CIRCUIT_FAILURE_LIMIT and all(status == "provider_error" for status in recent):
        return _permit(invocation_id, role_id, source_ref, input_hash, provider_name, model, False,
                       "circuit_open", provider_class=provider_class, endpoint=endpoint,
                       boundary_checks=boundary_checks)
    return _permit(invocation_id, role_id, source_ref, input_hash, provider_name, model, True, "allowed",
                   provider_class=provider_class, endpoint=endpoint, boundary_checks=boundary_checks)


def record_invocation(
    private_root: Path,
    permit: InvocationPermit,
    *,
    status: str,
    output_ref: str = "",
    problems: list[str] | None = None,
    usage: LLMUsage | None = None,
    surface: str = "",
    correlation_id: str = "",
    event_phase: str = "completed",
    output_hash: str = "",
    response_received: bool = False,
    attempt_count: int = 0,
) -> Path:
    usage_payload = usage.to_dict() if usage is not None else {}
    row = {
        "schema": SCHEMA,
        **asdict(permit),
        "status": status,
        "output_ref": output_ref,
        "output_hash": output_hash,
        "problems": list(problems or []),
        "surface": surface,
        "correlation_id": correlation_id,
        "event_phase": event_phase,
        "response_received": bool(response_received),
        "attempt_count": max(0, int(attempt_count)),
        "input_tokens": int(usage_payload.get("input_tokens") or 0),
        "output_tokens": int(usage_payload.get("output_tokens") or 0),
        "total_tokens": int(usage_payload.get("total_tokens") or 0),
        "cost_rub": float(usage_payload.get("cost_rub") or 0.0),
        "paper_only": True,
        "execution_allowed": False,
        "secrets_exposed": False,
        "completed_at": utc_now(),
    }
    return append_jsonl(ledger_path(private_root), row)


def start_transport_invocation(
    context: LLMTraceContext,
    *,
    role_id: str,
    provider: str,
    model: str,
    input_payload: Mapping[str, Any],
    provider_class: str,
) -> InvocationPermit:
    """Persist a start event before a canonical transport may use the network."""
    input_hash = _canonical_hash(input_payload)
    invocation_id = stable_id(
        "llminv",
        {
            "correlation_id": context.correlation_id,
            "surface": context.surface,
            "role_id": role_id,
            "source_ref": context.source_ref,
            "input_hash": input_hash,
            "provider": provider,
            "model": model,
            "provider_class": provider_class,
        },
        length=24,
    )
    permit = _permit(
        invocation_id,
        role_id,
        context.source_ref,
        input_hash,
        str(provider or "unknown"),
        str(model or ""),
        True,
        "trace_started",
        provider_class=provider_class,
        boundary_checks=("safe_hash_only_v1", "response_correlation_v1"),
    )
    record_invocation(
        context.private_root,
        permit,
        status="started",
        surface=context.surface,
        correlation_id=context.correlation_id,
        event_phase="started",
        response_received=False,
    )
    return permit


def finish_transport_invocation(
    context: LLMTraceContext,
    permit: InvocationPermit,
    *,
    status: str,
    output_text: str | None = None,
    usage: Mapping[str, Any] | None = None,
    error_type: str = "",
    response_received: bool = False,
    attempt_count: int = 0,
) -> Path:
    """Persist a terminal event containing hashes and sanitized metadata only."""
    usage_row = usage or {}
    safe_error = _safe_label(str(error_type or ""), fallback="sanitized_error")
    if not error_type:
        safe_error = ""
    llm_usage = LLMUsage(
        provider=str(usage_row.get("provider") or permit.provider),
        model=str(usage_row.get("model") or permit.model),
        input_tokens=int(usage_row.get("input_tokens") or 0),
        output_tokens=int(usage_row.get("output_tokens") or 0),
        total_tokens=int(usage_row.get("total_tokens") or 0),
        cost_rub=float(usage_row.get("cost_rub") or 0.0),
        status=str(usage_row.get("status") or status),
        error_type=safe_error,
    )
    output_hash = (
        hashlib.sha256(output_text.encode("utf-8")).hexdigest()
        if output_text
        else ""
    )
    return record_invocation(
        context.private_root,
        permit,
        status=status,
        problems=[safe_error] if safe_error else [],
        usage=llm_usage,
        surface=context.surface,
        correlation_id=context.correlation_id,
        event_phase="completed",
        output_hash=output_hash,
        response_received=response_received,
        attempt_count=attempt_count,
    )


def invocation_summary(private_root: Path) -> dict[str, Any]:
    if maybe_runtime_storage_capability(ledger_path(private_root)) is not None:
        return indexed_invocation_summary(private_root)
    rows = _rows(private_root)
    logical_rows: list[dict[str, Any]] = []
    transport: dict[str, dict[str, Any]] = {}
    for row in rows:
        correlation_id = str(row.get("correlation_id") or "")
        if not correlation_id:
            logical_rows.append(row)
            continue
        previous = transport.get(correlation_id)
        if previous is None or str(row.get("event_phase") or "") == "completed":
            transport[correlation_id] = row
    logical_rows.extend(transport.values())
    by_status: dict[str, int] = {}
    by_role: dict[str, int] = {}
    total_tokens = 0
    total_cost = 0.0
    for row in logical_rows:
        status = str(row.get("status") or "unknown")
        role = str(row.get("role_id") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        by_role[role] = by_role.get(role, 0) + 1
        total_tokens += int(row.get("total_tokens") or 0)
        total_cost += float(row.get("cost_rub") or 0.0)
    return {
        "schema": "LLMInvocationSummary.v1",
        "invocations": len(logical_rows),
        "invocation_events": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "by_role": dict(sorted(by_role.items())),
        "total_tokens": total_tokens,
        "total_cost_rub": round(total_cost, 4),
        "paper_only": True,
        "execution_allowed": False,
        "secrets_exposed": False,
    }
