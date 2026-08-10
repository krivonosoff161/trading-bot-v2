"""Bounded, advisory-only feedback contract for the system analyst role."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.research_lab.lineage_contract import utc_now
from src.research_lab.paths import resolve_private_child


SCHEMA = "SystemAnalystFeedback.v1"
EVENT_SCHEMA = "SystemAnalystFeedbackEvent.v1"
EVENT_TYPES = frozenset({"quality_accepted", "routed", "acknowledged", "superseded"})
RECIPIENTS = frozenset({"farm", "validator", "trader"})
LIFECYCLE = frozenset({"quality_accepted", "routed", "acknowledged", "superseded"})
ALLOWED_ACTIONS = frozenset(
    {
        "collect_more_evidence",
        "inspect_data_quality",
        "rerun_bounded_sweep",
        "retest_candidate",
        "review_validator_threshold",
        "review_paper_outcome",
        "no_action",
    }
)
PROHIBITED_FIELDS = frozenset(
    {
        "auto_trade",
        "close",
        "entry",
        "entry_zone",
        "execute",
        "execution_allowed",
        "hard_status",
        "leverage",
        "order",
        "paper_ready",
        "position_size",
        "side",
        "stop",
        "stop_loss",
        "take_profit",
        "trader_verdict",
        "validator_verdict",
    }
)
MAX_RECIPIENTS = 3
MAX_EVIDENCE_REFS = 20
MAX_RECOMMENDATIONS = 12
MAX_TEXT_CHARS = 4_000
MAX_EVENT_BYTES = 64_000
MAX_LEDGER_EVENTS = 10_000


_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}


def _thread_lock(private_root: Path) -> threading.Lock:
    """Serialize same-process writers before taking the cross-process lock."""
    key = os.path.normcase(str(ledger_path(private_root).resolve(strict=False)))
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


def _parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(dt.timezone.utc)


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def evidence_content_hash(value: Any) -> str:
    return _canonical_hash({"evidence": value})


def source_refs_hash(source_refs: Sequence[str], evidence_hashes: Mapping[str, str]) -> str:
    return _canonical_hash({
        "source_refs": list(source_refs),
        "source_evidence_hashes": dict(sorted(evidence_hashes.items())),
    })


def _contains_prohibited_key(value: Any, *, envelope: bool = False) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in PROHIBITED_FIELDS and not (envelope and normalized == "execution_allowed"):
                return normalized
            found = _contains_prohibited_key(nested)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _contains_prohibited_key(nested)
            if found:
                return found
    return None


@dataclass(frozen=True)
class TemporalProvenance:
    observed_at: str
    hypothesis_frozen_at: str
    knowledge_cutoff_at: str
    outcome_window_end: str
    evaluation_started_at: str
    valid_until: str
    generated_at: str
    source_refs: tuple[str, ...]
    source_evidence_hashes: dict[str, str]
    source_evidence: dict[str, Any]
    source_hash: str


@dataclass(frozen=True)
class Recommendation:
    recipient: str
    action: str
    reason: str
    evidence_refs: tuple[str, ...] = ()
    task_spec: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SystemAnalystFeedback:
    feedback_id: str
    subject_ref: str
    summary: str
    recipients: tuple[str, ...]
    provenance: TemporalProvenance
    recommendations: tuple[Recommendation, ...]
    quality_score: float
    quality_reasons: tuple[str, ...]
    supersedes: str = ""
    advisory_only: bool = True
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_schema(payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Validate shape and authority boundaries; this is not quality acceptance."""
    problems: list[str] = []
    required = {"feedback_id", "subject_ref", "summary", "recipients", "provenance", "recommendations"}
    missing = sorted(key for key in required if not payload.get(key))
    if payload.get("schema") != SCHEMA:
        problems.append(f"schema_must_be:{SCHEMA}")
    if missing:
        problems.append(f"missing_fields:{','.join(missing)}")
    prohibited = _contains_prohibited_key(payload, envelope=True)
    if prohibited:
        problems.append(f"prohibited_field:{prohibited}")
    if payload.get("advisory_only") is not True:
        problems.append("advisory_only_must_be_true")
    if payload.get("paper_only") is not True:
        problems.append("paper_only_must_be_true")
    if payload.get("execution_allowed") is not False:
        problems.append("execution_allowed_must_be_false")

    recipients = payload.get("recipients")
    if not isinstance(recipients, list) or not recipients or len(recipients) > MAX_RECIPIENTS:
        problems.append("recipients_invalid")
    elif len(recipients) != len(set(recipients)) or not set(recipients) <= RECIPIENTS:
        problems.append("recipients_not_allowlisted")

    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        problems.append("provenance_invalid")
    else:
        try:
            observed = _parse_time(str(provenance["observed_at"]))
            frozen = _parse_time(str(provenance["hypothesis_frozen_at"]))
            cutoff = _parse_time(str(provenance["knowledge_cutoff_at"]))
            outcome_end = _parse_time(str(provenance["outcome_window_end"]))
            evaluation_start = _parse_time(str(provenance["evaluation_started_at"]))
            valid_until = _parse_time(str(provenance["valid_until"]))
            generated = _parse_time(str(provenance["generated_at"]))
            if not observed <= frozen <= outcome_end <= cutoff <= generated <= valid_until:
                problems.append("temporal_order_invalid")
            if evaluation_start <= generated:
                problems.append("evaluation_not_after_feedback_freeze")
        except (KeyError, TypeError, ValueError):
            problems.append("temporal_provenance_invalid")
        refs = provenance.get("source_refs")
        if not isinstance(refs, list) or not refs or len(refs) > MAX_EVIDENCE_REFS:
            problems.append("source_refs_invalid")
        source_hash = str(provenance.get("source_hash") or "")
        evidence_hashes = provenance.get("source_evidence_hashes")
        source_evidence = provenance.get("source_evidence")
        if not isinstance(evidence_hashes, Mapping) or set(evidence_hashes) != set(refs or []):
            problems.append("source_evidence_hashes_invalid")
            evidence_hashes = {}
        elif any(
            len(str(value)) != 64 or any(ch not in "0123456789abcdef" for ch in str(value))
            for value in evidence_hashes.values()
        ):
            problems.append("source_evidence_content_hash_invalid")
        if not isinstance(source_evidence, Mapping) or set(source_evidence) != set(refs or []):
            problems.append("source_evidence_invalid")
            source_evidence = {}
        elif any(
            str(evidence_hashes.get(str(ref)) or "") != evidence_content_hash(source_evidence[ref])
            for ref in refs or []
        ):
            problems.append("source_evidence_hash_mismatch")
        if len(source_hash) != 64 or any(ch not in "0123456789abcdef" for ch in source_hash):
            problems.append("source_hash_invalid")
        elif isinstance(refs, list) and source_hash != source_refs_hash(
            [str(ref) for ref in refs], {str(key): str(value) for key, value in evidence_hashes.items()}
        ):
            problems.append("source_hash_mismatch")

    recommendations = payload.get("recommendations")
    if not isinstance(recommendations, list) or not recommendations or len(recommendations) > MAX_RECOMMENDATIONS:
        problems.append("recommendations_invalid")
    else:
        recipient_set = set(recipients) if isinstance(recipients, list) else set()
        for item in recommendations:
            if not isinstance(item, Mapping):
                problems.append("recommendation_invalid")
                continue
            if item.get("recipient") not in recipient_set:
                problems.append("recommendation_recipient_invalid")
            if item.get("action") not in ALLOWED_ACTIONS:
                problems.append("recommendation_action_not_allowlisted")
            if not str(item.get("reason") or "").strip():
                problems.append("recommendation_reason_required")
            task_spec = item.get("task_spec")
            if not isinstance(task_spec, Mapping):
                problems.append("recommendation_task_spec_required")
            elif task_spec.get("schema") != "RoleTaskSpec.v1":
                problems.append("recommendation_task_spec_schema_invalid")
            elif task_spec.get("kind") not in {
                "bounded_sweep", "untouched_validation", "paper_replay"
            }:
                problems.append("recommendation_task_kind_invalid")
        recommendation_recipients = [str(item.get("recipient") or "") for item in recommendations if isinstance(item, Mapping)]
        if len(recommendation_recipients) != len(set(recommendation_recipients)):
            problems.append("duplicate_recommendation_recipient")
    if len(str(payload.get("summary") or "")) > MAX_TEXT_CHARS:
        problems.append("summary_too_large")
    return not problems, sorted(set(problems))


def quality_gate(payload: Mapping[str, Any], *, now: str | None = None) -> tuple[bool, list[str]]:
    """Apply deterministic evidence-quality gates after schema validation."""
    schema_valid, problems = validate_schema(payload)
    if not schema_valid:
        return False, ["schema_rejected", *problems]
    quality_problems: list[str] = []
    provenance = payload["provenance"]
    current = _parse_time(now or utc_now())
    if current > _parse_time(str(provenance["valid_until"])):
        quality_problems.append("provenance_expired")
    if current < _parse_time(str(provenance["generated_at"])):
        quality_problems.append("generated_in_future")
    source_refs = set(provenance["source_refs"])
    for item in payload["recommendations"]:
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            quality_problems.append("recommendation_evidence_required")
        elif not set(refs) <= source_refs:
            quality_problems.append("recommendation_evidence_unproven")
    score = payload.get("quality_score")
    reasons = payload.get("quality_reasons")
    if not isinstance(score, (int, float)) or not 0.0 <= float(score) <= 1.0:
        quality_problems.append("quality_score_invalid")
    elif float(score) < 0.7:
        quality_problems.append("quality_score_below_threshold")
    if not isinstance(reasons, list) or not reasons:
        quality_problems.append("quality_reasons_required")
    deterministic_score = min(
        1.0,
        0.5
        + min(0.2, 0.1 * len(set(source_refs)))
        + min(0.2, 0.1 * len(payload["recommendations"]))
        + (0.1 if isinstance(reasons, list) and reasons else 0.0),
    )
    if deterministic_score < 0.7:
        quality_problems.append("deterministic_quality_below_threshold")
    return not quality_problems, sorted(set(quality_problems))


def build_feedback(payload: Mapping[str, Any], *, now: str | None = None) -> SystemAnalystFeedback:
    accepted, problems = quality_gate(payload, now=now)
    if not accepted:
        raise ValueError("feedback rejected: " + ";".join(problems))
    provenance = payload["provenance"]
    return SystemAnalystFeedback(
        feedback_id=str(payload["feedback_id"]),
        subject_ref=str(payload["subject_ref"]),
        summary=str(payload["summary"]),
        recipients=tuple(payload["recipients"]),
        provenance=TemporalProvenance(
            observed_at=str(provenance["observed_at"]),
            hypothesis_frozen_at=str(provenance["hypothesis_frozen_at"]),
            knowledge_cutoff_at=str(provenance["knowledge_cutoff_at"]),
            outcome_window_end=str(provenance["outcome_window_end"]),
            evaluation_started_at=str(provenance["evaluation_started_at"]),
            valid_until=str(provenance["valid_until"]),
            generated_at=str(provenance["generated_at"]),
            source_refs=tuple(provenance["source_refs"]),
            source_evidence_hashes={
                str(key): str(value)
                for key, value in provenance["source_evidence_hashes"].items()
            },
            source_evidence={str(key): value for key, value in provenance["source_evidence"].items()},
            source_hash=str(provenance["source_hash"]),
        ),
        recommendations=tuple(
            Recommendation(
                recipient=str(item["recipient"]),
                action=str(item["action"]),
                reason=str(item["reason"]),
                evidence_refs=tuple(item["evidence_refs"]),
                task_spec=dict(item["task_spec"]),
            )
            for item in payload["recommendations"]
        ),
        quality_score=float(payload["quality_score"]),
        quality_reasons=tuple(payload["quality_reasons"]),
        supersedes=str(payload.get("supersedes") or ""),
    )


def ledger_path(private_root: Path) -> Path:
    return resolve_private_child(private_root, "state", "system_analyst_feedback", "ledger.jsonl")


@contextmanager
def _ledger_lock(private_root: Path):
    with _thread_lock(private_root):
        parent = ledger_path(private_root).parent
        parent.mkdir(parents=True, exist_ok=True)
        lock_dir = parent / "ledger.lockdir"
        deadline = time.monotonic() + 15.0
        while True:
            try:
                lock_dir.mkdir()
                (lock_dir / "owner.json").write_text(
                    json.dumps({"pid": os.getpid(), "created_at": time.time()}), encoding="utf-8"
                )
                break
            except (FileExistsError, PermissionError):
                owner_path = lock_dir / "owner.json"
                stale = False
                try:
                    owner = json.loads(owner_path.read_text(encoding="utf-8"))
                    pid = int(owner.get("pid") or 0)
                    if pid <= 0:
                        stale = True
                    else:
                        try:
                            os.kill(pid, 0)
                        except OSError:
                            stale = True
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    try:
                        stale = time.time() - lock_dir.stat().st_mtime > 1.0
                    except OSError:
                        stale = False
                if stale:
                    try:
                        shutil.rmtree(lock_dir)
                        continue
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for feedback ledger lock")
                time.sleep(0.01)
        try:
            yield
        finally:
            shutil.rmtree(lock_dir, ignore_errors=True)


def _load_events_unlocked(private_root: Path) -> list[dict[str, Any]]:
    path = ledger_path(private_root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    previous_hash = ""
    raw_bytes = path.read_bytes()
    lines = raw_bytes.splitlines(keepends=True)
    verified_bytes = bytearray()
    for index, raw_line in enumerate(lines):
        complete = raw_line.endswith((b"\n", b"\r"))
        try:
            raw = raw_line.decode("utf-8", errors="strict").strip()
            if not raw:
                verified_bytes.extend(raw_line)
                continue
            row = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            if index == len(lines) - 1 and not complete:
                with path.open("wb") as handle:
                    handle.write(verified_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                break
            raise ValueError("invalid feedback ledger JSON event")
        if not isinstance(row, dict) or row.get("schema") != EVENT_SCHEMA:
            raise ValueError("invalid feedback ledger event")
        if row.get("event") not in EVENT_TYPES or not row.get("feedback_id"):
            raise ValueError("invalid feedback ledger event shape")
        if str(row.get("previous_event_hash") or "") != previous_hash:
            raise ValueError("feedback ledger hash chain mismatch")
        event_hash = str(row.get("event_hash") or "")
        unsigned = {key: value for key, value in row.items() if key != "event_hash"}
        if event_hash != _canonical_hash(unsigned):
            raise ValueError("feedback ledger event hash mismatch")
        if row.get("event") == "quality_accepted":
            feedback = row.get("feedback")
            schema_valid, schema_problems = (
                validate_schema(feedback) if isinstance(feedback, Mapping) else (False, [])
            )
            # RoleTaskSpec was added after the first accepted ledger generation.
            # Historical hash-bound events remain readable only when this is the
            # sole incompatibility; every authority/provenance invariant must
            # still pass the current validator.
            legacy_task_spec_only = bool(schema_problems) and set(schema_problems) == {
                "recommendation_task_spec_required"
            }
            if not isinstance(feedback, Mapping) or not (schema_valid or legacy_task_spec_only):
                raise ValueError("invalid accepted feedback event")
            if row.get("payload_hash") != _canonical_hash(feedback):
                raise ValueError("accepted feedback payload hash mismatch")
        rows.append(row)
        verified_bytes.extend(raw_line)
        previous_hash = event_hash
        if len(rows) > MAX_LEDGER_EVENTS:
            raise ValueError("feedback ledger event limit exceeded")
    return rows


def _load_events(private_root: Path) -> list[dict[str, Any]]:
    with _ledger_lock(private_root):
        return _load_events_unlocked(private_root)


def _append_event_unlocked(
    private_root: Path, event: dict[str, Any], events: list[dict[str, Any]]
) -> Path:
    event = {
        **event,
        "previous_event_hash": str(events[-1].get("event_hash") or "") if events else "",
    }
    event["event_hash"] = _canonical_hash(event)
    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
        raise ValueError("feedback event too large")
    path = ledger_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise ValueError("feedback ledger cannot be a link")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    events.append(event)
    return path


def route_feedback(private_root: Path, feedback: SystemAnalystFeedback) -> Path:
    """Persist accepted feedback and one delivery event per recipient."""
    with _ledger_lock(private_root):
        events = _load_events_unlocked(private_root)
        known = {str(row.get("feedback_id") or "") for row in events}
        if feedback.supersedes and feedback.supersedes not in known:
            raise ValueError("superseded feedback does not exist")
        fingerprint = _canonical_hash(feedback.to_dict())
        existing_accepts = [row for row in events if row.get("feedback_id") == feedback.feedback_id and row.get("event") == "quality_accepted"]
        if existing_accepts and existing_accepts[-1].get("payload_hash") != fingerprint:
            raise ValueError("feedback_id payload conflict")
        timestamp = utc_now()
        if not existing_accepts:
            _append_event_unlocked(private_root, {
                "schema": EVENT_SCHEMA, "event": "quality_accepted", "feedback_id": feedback.feedback_id,
                "payload_hash": fingerprint, "feedback": feedback.to_dict(), "at": timestamp,
                "paper_only": True, "execution_allowed": False,
            }, events)
        for recipient in feedback.recipients:
            if not any(row.get("event") == "routed" and row.get("feedback_id") == feedback.feedback_id and row.get("recipient") == recipient for row in events):
                _append_event_unlocked(private_root, {
                    "schema": EVENT_SCHEMA, "event": "routed", "feedback_id": feedback.feedback_id,
                    "recipient": recipient, "at": timestamp, "paper_only": True, "execution_allowed": False,
                }, events)
        if feedback.supersedes and not any(row.get("event") == "superseded" and row.get("feedback_id") == feedback.supersedes and row.get("superseded_by") == feedback.feedback_id for row in events):
            _append_event_unlocked(private_root, {
                "schema": EVENT_SCHEMA, "event": "superseded", "feedback_id": feedback.supersedes,
                "superseded_by": feedback.feedback_id, "at": timestamp, "paper_only": True, "execution_allowed": False,
            }, events)
        return ledger_path(private_root)


def acknowledge_feedback(
    private_root: Path,
    *,
    feedback_id: str,
    recipient: str,
    ack_id: str,
    disposition: str = "acknowledged",
    applied_artifact_refs: Sequence[str] = (),
) -> Path:
    if recipient not in RECIPIENTS or not feedback_id or not ack_id:
        raise ValueError("valid feedback_id, recipient, and ack_id are required")
    with _ledger_lock(private_root):
        events = _load_events_unlocked(private_root)
        if not any(row.get("event") == "routed" and row.get("feedback_id") == feedback_id and row.get("recipient") == recipient for row in events):
            raise ValueError("feedback was not routed to recipient")
        matching = [row for row in events if row.get("event") == "acknowledged" and row.get("ack_id") == ack_id]
        if matching:
            existing = matching[-1]
            if (
                existing.get("feedback_id") != feedback_id
                or existing.get("recipient") != recipient
                or existing.get("disposition") != disposition
                or list(existing.get("applied_artifact_refs") or []) != list(applied_artifact_refs)
            ):
                raise ValueError("ack_id conflict")
            return ledger_path(private_root)
        return _append_event_unlocked(private_root, {
            "schema": EVENT_SCHEMA, "event": "acknowledged", "feedback_id": feedback_id,
            "recipient": recipient, "ack_id": ack_id, "disposition": disposition,
            "applied_artifact_refs": list(applied_artifact_refs), "at": utc_now(),
            "paper_only": True, "execution_allowed": False,
        }, events)


def pending_feedback(
    private_root: Path,
    recipient: str,
    *,
    limit: int = 100,
    expected_generation_run_id: str | None = None,
) -> list[dict[str, Any]]:
    if recipient not in RECIPIENTS or not 1 <= limit <= 100:
        raise ValueError("invalid recipient or limit")
    events = _load_events(private_root)
    superseded = {str(row["feedback_id"]) for row in events if row.get("event") == "superseded"}
    acked = {(str(row.get("feedback_id")), str(row.get("recipient"))) for row in events if row.get("event") == "acknowledged"}
    feedback_by_id = {
        str(row["feedback_id"]): row["feedback"] for row in events if row.get("event") == "quality_accepted"
    }
    result: list[dict[str, Any]] = []
    for row in events:
        feedback_id = str(row.get("feedback_id") or "")
        if row.get("event") != "routed" or row.get("recipient") != recipient:
            continue
        if feedback_id in superseded or (feedback_id, recipient) in acked:
            continue
        if feedback_id in feedback_by_id:
            feedback = feedback_by_id[feedback_id]
            if expected_generation_run_id:
                recommendations = feedback.get("recommendations") or []
                generation_matches = any(
                    isinstance(item, dict)
                    and item.get("recipient") == recipient
                    and isinstance(item.get("task_spec"), dict)
                    and str(item["task_spec"].get("paper_generation_run_id") or "")
                    == str(expected_generation_run_id)
                    for item in recommendations
                )
                if not generation_matches:
                    continue
            result.append(feedback)
        if len(result) >= limit:
            break
    return result
