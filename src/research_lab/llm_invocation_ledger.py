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
from src.research_lab.llm_provider import LLMUsage, ProposalProvider

SCHEMA = "LLMInvocation.v1"
LOCAL_PROVIDER_NAMES = {"ollama", "ollama-local"}
TEST_PROVIDER_NAMES = {"synthetic"}
DEFAULT_LOCAL_MODEL_ALLOWLIST = ("calculator-swarm",)
CIRCUIT_FAILURE_LIMIT = 3
TERMINAL_CALL_STATUSES = {"accepted", "schema_rejected", "blocked"}


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
    input_hash = _canonical_hash(input_payload)
    invocation_id = stable_id(
        "llminv",
        {
            "role_id": role_id,
            "source_ref": source_ref,
            "input_hash": input_hash,
            "provider": provider_name,
            "model": model,
        },
        length=24,
    )
    rows = _rows(private_root)
    if any(
        str(row.get("invocation_id") or "") == invocation_id
        and str(row.get("status") or "") in TERMINAL_CALL_STATUSES
        for row in rows
    ):
        return InvocationPermit(
            invocation_id, role_id, source_ref, input_hash, provider_name, model, False, "duplicate_completed"
        )
    if not bool(getattr(provider, "configured", False)):
        return InvocationPermit(
            invocation_id, role_id, source_ref, input_hash, provider_name, model, False, "provider_not_configured"
        )
    if local_only and provider_name not in LOCAL_PROVIDER_NAMES | TEST_PROVIDER_NAMES:
        return InvocationPermit(
            invocation_id, role_id, source_ref, input_hash, provider_name, model, False, "local_provider_required"
        )
    if local_only and provider_name in LOCAL_PROVIDER_NAMES and not _local_model_allowed(model, local_model_allowlist):
        return InvocationPermit(
            invocation_id, role_id, source_ref, input_hash, provider_name, model, False, "local_model_not_allowlisted"
        )
    recent = [
        row
        for row in rows
        if row.get("role_id") == role_id and row.get("provider") == provider_name
    ][-CIRCUIT_FAILURE_LIMIT:]
    if len(recent) >= CIRCUIT_FAILURE_LIMIT and all(row.get("status") == "provider_error" for row in recent):
        return InvocationPermit(
            invocation_id, role_id, source_ref, input_hash, provider_name, model, False, "circuit_open"
        )
    return InvocationPermit(invocation_id, role_id, source_ref, input_hash, provider_name, model, True, "allowed")


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
