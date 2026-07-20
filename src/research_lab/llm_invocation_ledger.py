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
from pathlib import Path
from typing import Any, Mapping

from src.research_lab.lineage_contract import append_jsonl, stable_id, utc_now
from src.research_lab.llm_boundary_identity import EndpointIdentity, endpoint_identity_from_url
from src.research_lab.llm_provider import LLMUsage, ProposalProvider

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


def ledger_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "llm_advice" / "invocations.jsonl"


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    ]
    if any(
        str(row.get("invocation_id") or "") == invocation_id
        and str(row.get("status") or "") in TERMINAL_CALL_STATUSES
        for row in rows
    ):
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
    retryable_attempts = sum(
        1 for row in matching
        if str(row.get("status") or "") in RETRYABLE_CALL_STATUSES
    )
    if retryable_attempts >= MAX_RETRYABLE_ATTEMPTS:
        return _permit(invocation_id, role_id, source_ref, input_hash, provider_name, model, False,
                       "retry_exhausted", provider_class=provider_class, endpoint=endpoint,
                       boundary_checks=boundary_checks)
    recent = [
        row
        for row in rows
        if row.get("role_id") == role_id and row.get("provider") == provider_name
    ][-CIRCUIT_FAILURE_LIMIT:]
    if len(recent) >= CIRCUIT_FAILURE_LIMIT and all(row.get("status") == "provider_error" for row in recent):
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
) -> Path:
    usage_payload = usage.to_dict() if usage is not None else {}
    row = {
        "schema": SCHEMA,
        **asdict(permit),
        "status": status,
        "output_ref": output_ref,
        "problems": list(problems or []),
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


def invocation_summary(private_root: Path) -> dict[str, Any]:
    rows = _rows(private_root)
    by_status: dict[str, int] = {}
    by_role: dict[str, int] = {}
    total_tokens = 0
    total_cost = 0.0
    for row in rows:
        status = str(row.get("status") or "unknown")
        role = str(row.get("role_id") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        by_role[role] = by_role.get(role, 0) + 1
        total_tokens += int(row.get("total_tokens") or 0)
        total_cost += float(row.get("cost_rub") or 0.0)
    return {
        "schema": "LLMInvocationSummary.v1",
        "invocations": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "by_role": dict(sorted(by_role.items())),
        "total_tokens": total_tokens,
        "total_cost_rub": round(total_cost, 4),
        "paper_only": True,
        "execution_allowed": False,
        "secrets_exposed": False,
    }
