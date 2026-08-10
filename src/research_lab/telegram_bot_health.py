"""Secret-safe Telegram poll liveness for the canonical RCC profile."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.research_lab.product_progress import _atomic_json


SCHEMA = "TelegramBotHealth.v1"
STATES = frozenset({"starting", "ready", "degraded", "failed", "stopped"})


def status_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "telegram_bot_health.json"


def publish_health(
    private_root: Path,
    *,
    state: str,
    started_at: float,
    updated_at: float,
    last_success_at: float = 0.0,
    consecutive_failures: int = 0,
    failure_type: str = "",
) -> dict[str, Any]:
    if state not in STATES:
        raise ValueError("invalid Telegram bot health state")
    candidate_failure_type = str(failure_type or "").split(":", 1)[0][:100]
    safe_failure_type = (
        candidate_failure_type
        if all(char.isalnum() or char in "._" for char in candidate_failure_type)
        else "unclassified_error"
    )
    payload = {
        "schema": SCHEMA,
        "pid": os.getpid(),
        "state": state,
        "started_at": float(started_at),
        "updated_at": float(updated_at),
        "last_success_at": float(last_success_at),
        "consecutive_failures": max(0, int(consecutive_failures)),
        "failure_type": safe_failure_type,
        "paper_only": True,
        "execution_allowed": False,
    }
    _atomic_json(status_path(private_root), payload)
    return payload


@dataclass(frozen=True)
class TelegramBotHealthAssessment:
    ready: bool
    state: str
    success_age_seconds: float | None
    hard_failure: str | None = None


def assess_health(
    payload: Mapping[str, Any] | None,
    *,
    expected_pid: int,
    run_started_at: float,
    now: float,
    startup_budget_seconds: float,
    stale_seconds: float,
    require_ready: bool,
) -> TelegramBotHealthAssessment:
    row = payload if isinstance(payload, Mapping) else {}
    try:
        payload_pid = int(row.get("pid") or 0)
        payload_started_at = float(row.get("started_at") or 0.0)
        last_success_at = float(row.get("last_success_at") or 0.0)
    except (TypeError, ValueError, OverflowError):
        payload_pid = 0
        payload_started_at = 0.0
        last_success_at = 0.0
    valid = bool(
        row.get("schema") == SCHEMA
        and row.get("paper_only") is True
        and row.get("execution_allowed") is False
        and payload_pid == int(expected_pid)
        and payload_started_at >= float(run_started_at)
    )
    last_success_at = last_success_at if valid else 0.0
    success_age = max(0.0, float(now) - last_success_at) if last_success_at else None
    ready = bool(
        valid
        and row.get("state") in {"ready", "degraded"}
        and last_success_at >= float(run_started_at)
        and success_age is not None
        and success_age <= float(stale_seconds)
    )
    if ready:
        return TelegramBotHealthAssessment(
            True, str(row.get("state") or "ready"), success_age
        )
    elapsed = max(0.0, float(now) - float(run_started_at))
    if require_ready and success_age is not None and success_age > float(stale_seconds):
        return TelegramBotHealthAssessment(
            False,
            "failed",
            success_age,
            "telegram_bot_poll_stale",
        )
    if require_ready or elapsed >= float(startup_budget_seconds):
        return TelegramBotHealthAssessment(
            False,
            "failed",
            success_age,
            "telegram_bot_poll_unready",
        )
    return TelegramBotHealthAssessment(False, "starting", success_age)
