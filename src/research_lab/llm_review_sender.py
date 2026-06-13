# -*- coding: utf-8 -*-
"""Controlled LLM review send path — disabled by default, no accidental spend.

Exporting a review pack is always allowed and never calls a network. SENDING it to
a model is gated behind several explicit conditions and, even then, the only sender
shipped today is `NullReviewSender`, which never calls a network. A real provider
is a documented next step; wiring one in must keep every gate.

Send gates (all required):
  1. dry-run is not active
  2. the caller passed --send
  3. env STRATEGY_LAB_LLM_ENABLED=1
  4. a provider is configured
  5. a daily budget cap is present and not exhausted

This module never logs API keys and never writes outside the private research root.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

ENV_ENABLED = "STRATEGY_LAB_LLM_ENABLED"
ENV_DAILY_CAP = "STRATEGY_LAB_LLM_DAILY_CAP"


@dataclass(frozen=True)
class SendDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class SendResult:
    status: str  # sent | not_sent | error
    provider: str
    reason: str = ""
    detail: str = ""  # human-readable; never contains secrets/keys


@runtime_checkable
class ReviewSender(Protocol):
    """A provider-agnostic review sender. Implementations must never log API keys."""

    name: str
    configured: bool

    def send(self, pack: dict[str, Any], *, meta: dict[str, Any]) -> SendResult: ...


class NullReviewSender:
    """Default sender: never calls a network, always reports not_sent."""

    name = "null"
    configured = False

    def send(self, pack: dict[str, Any], *, meta: dict[str, Any]) -> SendResult:
        return SendResult(
            status="not_sent",
            provider=self.name,
            reason="no_provider_configured",
            detail="NullReviewSender never calls a network (export-only).",
        )


def env_enabled(environ: dict[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get(ENV_ENABLED, "")).strip().lower() in {"1", "true", "yes"}


def daily_cap(environ: dict[str, str] | None = None) -> float | None:
    """Parsed daily budget cap from the env, or None if unset/invalid/non-positive."""
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_DAILY_CAP, "")).strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def evaluate_send_gates(
    *,
    dry_run: bool,
    send_requested: bool,
    enabled: bool,
    provider_configured: bool,
    cap: float | None,
    spent_today: float = 0.0,
) -> SendDecision:
    """Decide whether a send may proceed. Returns the first failing gate's reason."""
    if dry_run:
        return SendDecision(False, "dry_run_active")
    if not send_requested:
        return SendDecision(False, "send_flag_not_set")
    if not enabled:
        return SendDecision(False, f"env_{ENV_ENABLED}_not_set")
    if not provider_configured:
        return SendDecision(False, "no_provider_configured")
    if cap is None or cap <= 0:
        return SendDecision(False, "no_daily_budget_cap")
    if spent_today >= cap:
        return SendDecision(False, "daily_budget_exhausted")
    return SendDecision(True, "all_gates_passed")


def send_review_pack(
    pack: dict[str, Any],
    *,
    sender: ReviewSender,
    dry_run: bool,
    send_requested: bool,
    cap: float | None,
    spent_today: float = 0.0,
    enabled: bool | None = None,
    meta: dict[str, Any] | None = None,
) -> tuple[SendDecision, SendResult | None]:
    """Evaluate gates, then call the sender ONLY if every gate passes.

    Returns (decision, result). result is None when the gates block the send, so a
    blocked attempt never touches the sender at all.
    """
    is_enabled = env_enabled() if enabled is None else enabled
    decision = evaluate_send_gates(
        dry_run=dry_run,
        send_requested=send_requested,
        enabled=is_enabled,
        provider_configured=bool(getattr(sender, "configured", False)),
        cap=cap,
        spent_today=spent_today,
    )
    if not decision.allowed:
        return decision, None
    result = sender.send(pack, meta=dict(meta or {}))
    return decision, result


def record_send_intent(
    private_root: Path,
    *,
    decision: SendDecision,
    pack_label: str = "",
    provider: str = "",
    result: SendResult | None = None,
) -> Path:
    """Append a send-intent record under the private root. Never logs keys."""
    out_dir = private_root / "reports" / "llm_review"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "send_log.jsonl"
    record = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pack_label": pack_label,
        "provider": provider,
        "allowed": decision.allowed,
        "reason": decision.reason,
        "result_status": result.status if result else "not_attempted",
        "result_reason": result.reason if result else "",
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path
