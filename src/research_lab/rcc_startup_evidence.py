"""Revision-bound, secret-safe evidence for RCC startup before heartbeat.

The RCC heartbeat cannot describe failures that happen while constructing the
Tk root or before its publisher thread starts.  This module provides the small
durable bridge needed by an external startup monitor.  It records stage names
and exception *types* only; exception messages and environment values are never
persisted.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
import re
import time
from typing import Any, Mapping


STARTUP_EVIDENCE_SCHEMA = "RccStartupEvidence.v1"
STARTUP_ASSESSMENT_SCHEMA = "RccPreheartbeatAssessment.v1"
STARTUP_STAGES = (
    "revision_verified",
    "lock_acquiring",
    "lock_acquired",
    "ui_initializing",
    "heartbeat_starting",
    "heartbeat_started",
    "mainloop_running",
    "mainloop_stopped",
)
_REVISION = re.compile(r"[0-9a-f]{40}")
_ATTEMPT_ID = re.compile(r"rccstartup_[0-9a-f]{32}")
_SAFE_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")


class RccStartupEvidenceError(RuntimeError):
    """Startup evidence is malformed, stale, or internally inconsistent."""


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".pending")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _safe_failure_type(exc: BaseException) -> str:
    value = type(exc).__name__
    return value if _SAFE_TYPE.fullmatch(value) else "Exception"


def _attempt_id(
    revision: str, pid: int, process_started_at: float, started_at: float
) -> str:
    identity = (
        f"{revision}:{pid}:{process_started_at:.9f}:{started_at:.9f}"
    ).encode("ascii")
    return "rccstartup_" + hashlib.sha256(identity).hexdigest()[:32]


@dataclass(frozen=True)
class RccRunIdentity:
    """Public identity shared by one RCC and every child it supervises.

    This is deliberately not an authority token.  It only makes a checkpoint
    or heartbeat attributable to one exact RCC process generation and checkout
    revision, so a record from an earlier run cannot establish readiness for a
    newly launched canonical paper profile.
    """

    attempt_id: str
    revision: str
    pid: int
    process_started_at: float

    def __post_init__(self) -> None:
        revision = self.revision.strip().lower()
        attempt_id = self.attempt_id.strip()
        started_at = float(self.process_started_at)
        if (
            not _ATTEMPT_ID.fullmatch(attempt_id)
            or not _REVISION.fullmatch(revision)
            or isinstance(self.pid, bool)
            or int(self.pid) <= 0
            or not math.isfinite(started_at)
            or started_at <= 0.0
        ):
            raise ValueError("RCC run identity is invalid")
        object.__setattr__(self, "attempt_id", attempt_id)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "pid", int(self.pid))
        object.__setattr__(self, "process_started_at", started_at)

    @classmethod
    def from_startup_writer(cls, writer: "RccStartupEvidenceWriter") -> "RccRunIdentity":
        return cls(
            attempt_id=writer.attempt_id,
            revision=writer.revision,
            pid=writer.pid,
            process_started_at=writer.process_started_at,
        )

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> "RccRunIdentity":
        if not isinstance(value, Mapping):
            raise ValueError("RCC run identity payload is invalid")
        raw_pid = value.get("pid")
        raw_started_at = value.get("process_started_at")
        if (
            isinstance(raw_pid, bool)
            or not isinstance(raw_pid, int)
            or isinstance(raw_started_at, bool)
            or not isinstance(raw_started_at, (int, float))
        ):
            raise ValueError("RCC run identity payload is invalid")
        return cls(
            attempt_id=str(value.get("attempt_id") or ""),
            revision=str(value.get("revision") or ""),
            pid=raw_pid,
            process_started_at=float(raw_started_at),
        )

    def to_payload(self) -> dict[str, str | int | float]:
        return {
            "attempt_id": self.attempt_id,
            "revision": self.revision,
            "pid": self.pid,
            "process_started_at": self.process_started_at,
        }

    def to_child_environment(self) -> dict[str, str]:
        """Return the fixed public lineage values inherited by RCC children."""

        return {
            "TRADING_BOT_RCC_ATTEMPT_ID": self.attempt_id,
            "TRADING_BOT_RCC_REVISION": self.revision,
            "TRADING_BOT_RCC_PID": str(self.pid),
            "TRADING_BOT_RCC_PROCESS_STARTED_AT": repr(self.process_started_at),
        }


def rcc_run_identity_from_environment(
    environment: Mapping[str, str] | None = None,
) -> RccRunIdentity | None:
    """Read a complete RCC child lineage envelope or fail closed.

    Direct bounded tools intentionally have no envelope.  A partially supplied
    or malformed envelope is never silently discarded: a purported canonical
    child must not publish an unattributable checkpoint.
    """

    values = os.environ if environment is None else environment
    raw = {
        "attempt_id": str(values.get("TRADING_BOT_RCC_ATTEMPT_ID", "")).strip(),
        "revision": str(values.get("TRADING_BOT_RCC_REVISION", "")).strip(),
        "pid": str(values.get("TRADING_BOT_RCC_PID", "")).strip(),
        "process_started_at": str(
            values.get("TRADING_BOT_RCC_PROCESS_STARTED_AT", "")
        ).strip(),
    }
    if not any(raw.values()):
        return None
    if not all(raw.values()):
        raise ValueError("RCC child lineage envelope is incomplete")
    try:
        return RccRunIdentity(
            attempt_id=raw["attempt_id"],
            revision=raw["revision"],
            pid=int(raw["pid"]),
            process_started_at=float(raw["process_started_at"]),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("RCC child lineage envelope is invalid") from exc


class RccStartupEvidenceWriter:
    """Append-by-replacement state for one exact RCC process generation."""

    def __init__(
        self,
        path: Path,
        *,
        revision: str,
        pid: int | None = None,
        process_started_at: float,
        started_at: float | None = None,
    ) -> None:
        normalized = revision.strip().lower()
        if not _REVISION.fullmatch(normalized):
            raise ValueError("RCC startup revision must be an exact commit")
        self.path = Path(path)
        self.revision = normalized
        self.pid = int(os.getpid() if pid is None else pid)
        self.process_started_at = float(process_started_at)
        self.started_at = float(time.time() if started_at is None else started_at)
        if self.pid <= 0 or self.process_started_at <= 0 or self.started_at <= 0:
            raise ValueError("RCC startup process identity is invalid")
        self.attempt_id = _attempt_id(
            self.revision, self.pid, self.process_started_at, self.started_at
        )
        self._stage_index = -1
        self._terminal = False

    def transition(
        self,
        stage: str,
        *,
        state: str = "starting",
        failure_type: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        if self._terminal:
            raise ValueError("RCC startup evidence is already terminal")
        try:
            stage_index = STARTUP_STAGES.index(stage)
        except ValueError as exc:
            raise ValueError("unknown RCC startup stage") from exc
        if stage_index < self._stage_index:
            raise ValueError("RCC startup stage cannot move backwards")
        if state not in {"starting", "running", "failed", "stopped"}:
            raise ValueError("invalid RCC startup state")
        if failure_type is not None and not _SAFE_TYPE.fullmatch(failure_type):
            raise ValueError("invalid RCC startup failure type")
        if (state == "failed") != (failure_type is not None):
            raise ValueError("RCC startup failure evidence is inconsistent")
        if state == "running" and stage != "mainloop_running":
            raise ValueError("RCC running state requires mainloop stage")
        if state == "stopped" and stage != "mainloop_stopped":
            raise ValueError("RCC stopped state requires stopped stage")
        updated_at = float(time.time() if now is None else now)
        if not math.isfinite(updated_at) or updated_at < self.started_at:
            raise ValueError("RCC startup update time is invalid")
        self._stage_index = stage_index
        payload: dict[str, Any] = {
            "schema": STARTUP_EVIDENCE_SCHEMA,
            "attempt_id": self.attempt_id,
            "revision": self.revision,
            "pid": self.pid,
            "process_started_at": self.process_started_at,
            "started_at": self.started_at,
            "updated_at": updated_at,
            "stage": stage,
            "state": state,
            "failure_type": failure_type,
            "paper_only": True,
            "execution_allowed": False,
        }
        payload["record_digest"] = _digest(payload)
        _write_json_atomic(self.path, payload)
        self._terminal = state in {"failed", "stopped"}
        return payload

    def fail(
        self,
        stage: str,
        exc: BaseException,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        return self.transition(
            stage,
            state="failed",
            failure_type=_safe_failure_type(exc),
            now=now,
        )


def load_rcc_startup_evidence(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RccStartupEvidenceError("RCC startup evidence is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != STARTUP_EVIDENCE_SCHEMA:
        raise RccStartupEvidenceError("RCC startup evidence schema mismatch")
    supplied_digest = payload.pop("record_digest", None)
    expected_digest = _digest(payload)
    payload["record_digest"] = supplied_digest
    if supplied_digest != expected_digest:
        raise RccStartupEvidenceError("RCC startup evidence digest mismatch")
    revision = str(payload.get("revision") or "").lower()
    failure_type = payload.get("failure_type")
    state = payload.get("state")
    stage = payload.get("stage")
    try:
        pid = int(payload.get("pid") or 0)
        timestamps = tuple(
            float(payload.get(key) or 0)
            for key in ("process_started_at", "started_at", "updated_at")
        )
    except (TypeError, ValueError) as exc:
        raise RccStartupEvidenceError("RCC startup evidence values are invalid") from exc
    attempt_id = str(payload.get("attempt_id") or "")
    if (
        not _REVISION.fullmatch(revision)
        or not _ATTEMPT_ID.fullmatch(attempt_id)
        or attempt_id != _attempt_id(revision, pid, timestamps[0], timestamps[1])
        or pid <= 0
        or any(value <= 0 or not math.isfinite(value) for value in timestamps)
        or timestamps[2] < timestamps[1]
        or stage not in STARTUP_STAGES
        or state not in {"starting", "running", "failed", "stopped"}
        or (failure_type is not None and not _SAFE_TYPE.fullmatch(str(failure_type)))
        or (state == "failed") != (failure_type is not None)
        or (state == "running" and stage != "mainloop_running")
        or (state == "stopped" and stage != "mainloop_stopped")
        or payload.get("paper_only") is not True
        or payload.get("execution_allowed") is not False
    ):
        raise RccStartupEvidenceError("RCC startup evidence values are invalid")
    return payload


@dataclass(frozen=True)
class RccPreheartbeatAssessment:
    state: str
    reason: str
    attempt_id: str | None
    pid: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": STARTUP_ASSESSMENT_SCHEMA,
            "state": self.state,
            "reason": self.reason,
            "attempt_id": self.attempt_id,
            "pid": self.pid,
            "paper_only": True,
            "execution_allowed": False,
        }


def assess_rcc_preheartbeat_liveness(
    payload: dict[str, Any],
    *,
    expected_revision: str,
    previous_attempt_id: str | None,
    live_process_started_at: float | None,
    startup_requested_at: float | None = None,
    now: float | None = None,
    new_attempt_grace_seconds: float = 15.0,
) -> RccPreheartbeatAssessment:
    """Fail closed when a new RCC dies before its first heartbeat."""

    attempt_id = str(payload.get("attempt_id") or "") or None
    pid = int(payload.get("pid") or 0) or None
    normalized_revision = expected_revision.strip().lower()
    if not _REVISION.fullmatch(normalized_revision):
        return RccPreheartbeatAssessment(
            "failed", "startup_expected_revision_invalid", attempt_id, pid
        )
    if not math.isfinite(float(new_attempt_grace_seconds)) or new_attempt_grace_seconds <= 0:
        return RccPreheartbeatAssessment(
            "failed", "startup_liveness_policy_invalid", attempt_id, pid
        )
    if (
        startup_requested_at is not None
        and now is not None
        and float(now) < float(startup_requested_at)
    ):
        return RccPreheartbeatAssessment(
            "failed", "startup_clock_regression", attempt_id, pid
        )
    if str(payload.get("revision") or "").lower() != normalized_revision:
        return RccPreheartbeatAssessment(
            "failed", "startup_evidence_revision_mismatch", attempt_id, pid
        )
    if previous_attempt_id and attempt_id == previous_attempt_id:
        if (
            startup_requested_at is not None
            and now is not None
            and float(now) - float(startup_requested_at)
            >= float(new_attempt_grace_seconds)
        ):
            return RccPreheartbeatAssessment(
                "failed", "startup_evidence_not_advanced", attempt_id, pid
            )
        return RccPreheartbeatAssessment(
            "starting", "startup_evidence_pending_new_attempt", attempt_id, pid
        )
    if (
        startup_requested_at is not None
        and float(payload.get("started_at") or 0) + 1.0 < float(startup_requested_at)
    ):
        return RccPreheartbeatAssessment(
            "failed", "startup_evidence_predates_launch", attempt_id, pid
        )
    state = str(payload.get("state") or "")
    if state == "failed":
        failure_type = str(payload.get("failure_type") or "Exception")
        return RccPreheartbeatAssessment(
            "failed", f"rcc_preheartbeat_failed:{failure_type}", attempt_id, pid
        )
    if state == "stopped":
        return RccPreheartbeatAssessment(
            "failed", "rcc_stopped_before_heartbeat", attempt_id, pid
        )
    expected_started_at = float(payload.get("process_started_at") or 0)
    if live_process_started_at is None:
        return RccPreheartbeatAssessment(
            "failed", "rcc_process_exited_before_heartbeat", attempt_id, pid
        )
    if abs(float(live_process_started_at) - expected_started_at) > 1.0:
        return RccPreheartbeatAssessment(
            "failed", "rcc_process_generation_mismatch", attempt_id, pid
        )
    return RccPreheartbeatAssessment(
        "starting", f"rcc_preheartbeat:{payload['stage']}", attempt_id, pid
    )
