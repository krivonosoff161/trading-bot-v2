"""Materialize System Analyst feedback into bounded role-environment candidates."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from collections.abc import Iterable
from typing import Any

from src.research_lab.lineage_contract import utc_now
from src.research_lab.adaptive_trial import (
    adaptive_trial_id,
    write_adaptive_trial_record,
)
from src.research_lab.hard_validation_contract import trade_evidence_hash
from src.research_lab.paths import resolve_private_child
from src.research_lab.system_analyst_feedback import (
    acknowledge_feedback,
    pending_feedback,
)

SCHEMA = "RoleEnvironmentCandidate.v1"
STATE_SCHEMA = "RoleEnvironmentState.v1"

RECIPIENT_ACTIONS = {
    "farm": {
        "collect_more_evidence",
        "rerun_bounded_sweep",
        "retest_candidate",
        "no_action",
    },
    "validator": {
        "collect_more_evidence",
        "inspect_data_quality",
        "retest_candidate",
        "review_validator_threshold",
        "no_action",
    },
    "trader": {"collect_more_evidence", "review_paper_outcome", "no_action"},
}

REQUEST_KIND = {
    "farm": "bounded_experiment_request",
    "validator": "validation_review_request",
    "trader": "paper_decision_review",
}

_ENVIRONMENT_ID_RE = re.compile(r"env_[0-9a-f]{24}\Z")


def _candidate_path(private_root: Path, recipient: str, environment_id: str) -> Path:
    if not _ENVIRONMENT_ID_RE.fullmatch(environment_id):
        raise ValueError("invalid role environment id")
    return resolve_private_child(
        private_root, "state", "role_environments", recipient, f"{environment_id}.json"
    )


def _stable_id(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "env_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _adaptive_trial_id_from_row(row: dict[str, Any]) -> str:
    expected = adaptive_trial_id(dict(row.get("task_spec") or {}))
    actual = str(row.get("adaptive_trial_id") or expected)
    if actual != expected:
        raise ValueError("adaptive trial identity mismatch")
    return actual


def environment_dir(private_root: Path, recipient: str) -> Path:
    if recipient not in RECIPIENT_ACTIONS:
        raise ValueError("unknown role environment recipient")
    return resolve_private_child(private_root, "state", "role_environments", recipient)


def _state_path(path: Path) -> Path:
    return path.parent / "_state" / path.name


def _effective_row(path: Path) -> dict[str, Any]:
    candidate = json.loads(path.read_text(encoding="utf-8"))
    state_path = _state_path(path)
    if not state_path.exists():
        return candidate
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("schema") != STATE_SCHEMA or state.get(
        "environment_id"
    ) != candidate.get("environment_id"):
        raise ValueError("role environment state contract mismatch")
    return {**candidate, **state, "schema": candidate["schema"]}


def _write_state(path: Path, state: dict[str, Any]) -> None:
    state_path = _state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(state_path)


def _generation_index_path(
    private_root: Path, recipient: str, generation_run_id: str
) -> Path:
    if recipient not in RECIPIENT_ACTIONS or not generation_run_id:
        raise ValueError("valid role generation index identity is required")
    digest = hashlib.sha256(generation_run_id.encode("utf-8")).hexdigest()[:24]
    return resolve_private_child(
        private_root,
        "state",
        "role_environments",
        "_generation_index",
        f"{recipient}-{digest}.json",
    )


def _index_role_request(private_root: Path, row: dict[str, Any]) -> None:
    task_spec = row.get("task_spec")
    generation_run_id = str(
        task_spec.get("paper_generation_run_id")
        if isinstance(task_spec, dict)
        else ""
    )
    if not generation_run_id:
        return
    recipient = str(row.get("recipient") or "")
    environment_id = str(row.get("environment_id") or "")
    path = _generation_index_path(private_root, recipient, generation_run_id)
    environment_ids: set[str] = set()
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if (
            existing.get("schema") != "RoleEnvironmentGenerationIndex.v1"
            or existing.get("recipient") != recipient
            or existing.get("paper_generation_run_id") != generation_run_id
        ):
            raise ValueError("role generation index contract mismatch")
        environment_ids.update(str(item) for item in existing.get("environment_ids") or ())
    environment_ids.add(environment_id)
    if not all(_ENVIRONMENT_ID_RE.fullmatch(item) for item in environment_ids):
        raise ValueError("role generation index contains an invalid environment id")
    payload = {
        "schema": "RoleEnvironmentGenerationIndex.v1",
        "recipient": recipient,
        "paper_generation_run_id": generation_run_id,
        "environment_ids": sorted(environment_ids),
        "paper_only": True,
        "execution_allowed": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def current_generation_role_requests(
    private_root: Path, *, recipient: str, generation_run_id: str
) -> list[dict[str, Any]]:
    """Load only manifest-indexed role requests for one paper generation."""
    path = _generation_index_path(private_root, recipient, generation_run_id)
    if not path.exists() or path.is_symlink():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("schema") != "RoleEnvironmentGenerationIndex.v1"
        or payload.get("recipient") != recipient
        or payload.get("paper_generation_run_id") != generation_run_id
    ):
        raise ValueError("role generation index contract mismatch")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_id in payload.get("environment_ids") or ():
        environment_id = str(raw_id or "")
        if environment_id in seen or not _ENVIRONMENT_ID_RE.fullmatch(environment_id):
            raise ValueError("role generation index contains an invalid environment id")
        seen.add(environment_id)
        candidate_path = _candidate_path(private_root, recipient, environment_id)
        if not candidate_path.is_file() or candidate_path.is_symlink():
            raise ValueError("indexed role environment candidate is missing")
        row = _effective_row(candidate_path)
        task_spec = row.get("task_spec")
        if (
            not isinstance(task_spec, dict)
            or str(task_spec.get("paper_generation_run_id") or "")
            != generation_run_id
        ):
            raise ValueError("indexed role environment generation mismatch")
        rows.append(row)
    return rows


def _bound_ref(path: Path, content_hash: str | None = None) -> str:
    digest = content_hash or hashlib.sha256(path.read_bytes()).hexdigest()
    return f"{path}#sha256={digest}"


def _content_ref(
    ref: str,
    content_sha256: str,
    *,
    producer_completion_id: str,
    producer_schema: str = "",
    generation: int = 0,
) -> dict[str, Any]:
    return {
        "ref": str(ref),
        "content_sha256": str(content_sha256),
        "producer_completion_id": str(producer_completion_id),
        "producer_schema": str(producer_schema),
        "generation": int(generation),
    }


def _feedback_content_refs(
    feedback: dict[str, Any],
    refs: list[Any] | tuple[Any, ...],
    *,
    generation: int = 0,
) -> list[dict[str, Any]]:
    hashes = feedback.get("provenance", {}).get("source_evidence_hashes")
    if not isinstance(hashes, dict):
        hashes = {}
    completion = str(feedback.get("feedback_id") or "")
    out: list[dict[str, Any]] = []
    for item in refs:
        if isinstance(item, dict):
            out.append(item)
            continue
        ref = str(item)
        digest = str(hashes.get(ref) or "")
        if not digest:
            digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()
        out.append(
            _content_ref(
                ref,
                digest,
                producer_completion_id=completion,
                producer_schema=str(feedback.get("schema") or ""),
                generation=generation,
            )
        )
    return out


def _ensure_task_spec_content_binding(
    task_spec: dict[str, Any],
    feedback: dict[str, Any],
    *,
    recipient: str,
) -> None:
    provenance = (
        raw_provenance
        if isinstance(raw_provenance := feedback.get("provenance"), dict)
        else {}
    )
    source_hash = str(provenance.get("source_hash") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", source_hash):
        source_hash = hashlib.sha256(
            json.dumps(
                provenance, ensure_ascii=False, sort_keys=True, default=str
            ).encode("utf-8")
        ).hexdigest()
    task_spec.setdefault("source_content_sha256", source_hash)
    task_spec.setdefault(
        "producer_completion_id",
        _stable_id(
            {
                "feedback_id": str(feedback.get("feedback_id") or ""),
                "recipient": recipient,
                "source_ref": str(task_spec.get("source_ref") or ""),
                "generation": int(task_spec.get("generation") or 0),
            }
        ),
    )


def recoverable_role_requests(
    private_root: Path,
    recipient: str,
    *,
    environment_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Return candidates whose state projection still needs request acceptance.

    Production callers pass the exact current-generation ids returned by
    ``materialize_role_environment``.  The unbounded directory walk remains only as
    an explicit compatibility/recovery surface; it is not suitable for a hot product
    cycle because the directory is historical evidence, not a work queue.
    """
    directory = environment_dir(private_root, recipient)
    if not directory.exists():
        return []
    if environment_ids is None:
        paths = sorted(directory.glob("env_*.json"))
    else:
        paths = []
        seen: set[str] = set()
        for raw_id in environment_ids:
            environment_id = str(raw_id or "")
            if environment_id in seen:
                continue
            seen.add(environment_id)
            path = _candidate_path(private_root, recipient, environment_id)
            if path.is_file():
                paths.append(path)
    rows = [_effective_row(path) for path in paths]
    return [row for row in rows if row.get("status") == "candidate"]


def _verified_gate_artifact(
    private_root: Path, reference: str, *, environment_id: str, kind: str
) -> tuple[str, str]:
    path = resolve_private_child(private_root, reference)
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{kind} artifact does not exist")
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if kind == "gate":
        valid = (
            payload.get("schema") == "DeterministicRoleGate.v1"
            and payload.get("environment_id") == environment_id
            and isinstance(payload.get("accepted"), bool)
        )
    else:
        selection = payload.get("selection_evidence")
        evaluation = payload.get("evaluation_evidence")
        selection_hash = str(payload.get("selection_evidence_hash") or "")
        evaluation_hash = str(payload.get("evaluation_evidence_hash") or "")
        selection_fp = str(payload.get("selection_data_fingerprint") or "")
        evaluation_fp = str(payload.get("evaluation_data_fingerprint") or "")
        frozen_at = str(payload.get("hypothesis_frozen_at") or "")
        evaluation_started_at = str(payload.get("evaluation_started_at") or "")
        valid = (
            payload.get("schema") == "ValidationEpoch.v1"
            and payload.get("evidence_stage") == "untouched_evaluation"
            and payload.get("environment_id") == environment_id
            and isinstance(selection, list)
            and bool(selection)
            and isinstance(evaluation, list)
            and bool(evaluation)
            and trade_evidence_hash(selection) == selection_hash
            and trade_evidence_hash(evaluation) == evaluation_hash
            and bool(selection_fp)
            and bool(evaluation_fp)
            and selection_fp != evaluation_fp
            and bool(frozen_at)
            and bool(evaluation_started_at)
            and evaluation_started_at > frozen_at
            and isinstance(payload.get("quality_gate_passed"), bool)
        )
    if not valid:
        raise ValueError(f"{kind} artifact contract mismatch")
    return str(path), hashlib.sha256(raw).hexdigest()


def _recommendation(feedback: dict[str, Any], recipient: str) -> dict[str, Any] | None:
    rows = [
        row
        for row in feedback.get("recommendations") or []
        if isinstance(row, dict) and row.get("recipient") == recipient
    ]
    if len(rows) != 1:
        return None
    return rows[0]


def materialize_role_environment(
    private_root: Path,
    *,
    recipient: str,
    parent_environment_id: str = "",
    limit: int = 20,
    expected_generation_run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Create immutable, non-authoritative environment candidates and ACK them."""
    allowed = RECIPIENT_ACTIONS.get(recipient)
    if allowed is None:
        raise ValueError("unknown role environment recipient")
    out_dir = environment_dir(private_root, recipient)
    out_dir.mkdir(parents=True, exist_ok=True)
    materialized: list[dict[str, Any]] = []
    for feedback in pending_feedback(
        private_root,
        recipient,
        limit=100,
        expected_generation_run_id=expected_generation_run_id,
    ):
        if len(materialized) >= max(0, int(limit)):
            break
        recommendation = _recommendation(feedback, recipient)
        if recommendation is None or recommendation.get("action") not in allowed:
            continue
        task_spec = dict(recommendation.get("task_spec") or {})
        task_spec.setdefault(
            "subject", {"subject_ref": str(feedback.get("subject_ref") or "")}
        )
        task_spec.setdefault("source_ref", str(feedback["feedback_id"]))
        task_spec.setdefault("generation", 0)
        _ensure_task_spec_content_binding(task_spec, feedback, recipient=recipient)
        if expected_generation_run_id and str(
            task_spec.get("paper_generation_run_id") or ""
        ) != str(expected_generation_run_id):
            continue
        task_spec.setdefault("adaptive_trial_id", adaptive_trial_id(task_spec))
        basis: dict[str, Any] = {
            "feedback_id": str(feedback["feedback_id"]),
            "recipient": recipient,
            "parent_environment_id": parent_environment_id,
            "action": str(recommendation["action"]),
            "task_spec": task_spec,
        }
        basis["evidence_refs"] = _feedback_content_refs(
            feedback,
            list(recommendation.get("evidence_refs") or []),
            generation=int(task_spec.get("generation") or 0),
        )
        trial_id = str(basis["task_spec"].get("adaptive_trial_id") or "")
        expected_trial_id = adaptive_trial_id(basis["task_spec"])
        if trial_id and trial_id != expected_trial_id:
            raise ValueError("adaptive trial identity mismatch")
        trial_id = expected_trial_id
        environment_id = _stable_id(basis)
        row = {
            "schema": SCHEMA,
            "environment_id": environment_id,
            "parent_environment_id": parent_environment_id,
            "recipient": recipient,
            "request_kind": REQUEST_KIND[recipient],
            "feedback_id": basis["feedback_id"],
            "adaptive_trial_id": trial_id,
            "proposed_action": basis["action"],
            "reason": str(recommendation.get("reason") or ""),
            "evidence_refs": basis["evidence_refs"],
            "task_spec": basis["task_spec"],
            "status": "candidate",
            "created_at": utc_now(),
            "requires_deterministic_gate": True,
            "requires_untouched_evaluation": True,
            "mutates_code": False,
            "mutates_model_weights": False,
            "advisory_only": True,
            "paper_only": True,
            "execution_allowed": False,
        }
        path = out_dir / f"{environment_id}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing_basis = {
                "feedback_id": existing.get("feedback_id"),
                "recipient": existing.get("recipient"),
                "parent_environment_id": existing.get("parent_environment_id"),
                "action": existing.get("proposed_action"),
                "evidence_refs": existing.get("evidence_refs"),
                "task_spec": existing.get("task_spec") or {},
            }
            if existing_basis != basis:
                raise ValueError("role environment id conflict")
        else:
            path.write_text(
                json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        materialized.append(row)
        write_adaptive_trial_record(
            private_root,
            trial_id=trial_id,
            stage="role_candidate",
            role=recipient,
            artifact_id=environment_id,
            evidence_refs=tuple(basis["evidence_refs"]),
        )
    return materialized


def gate_role_environment(
    private_root: Path,
    *,
    recipient: str,
    environment_id: str,
    accepted: bool,
    gate_result_ref: str,
    untouched_evaluation_ref: str,
) -> dict[str, Any]:
    """Record a recipient-owned deterministic gate before acknowledging feedback."""
    if not gate_result_ref or not untouched_evaluation_ref:
        raise ValueError("gate and untouched evaluation references are required")
    path = _candidate_path(private_root, recipient, environment_id)
    if not path.exists() or path.is_symlink():
        raise ValueError("role environment candidate does not exist")
    row = _effective_row(path)
    if row.get("schema") != SCHEMA or row.get("recipient") != recipient:
        raise ValueError("role environment candidate contract mismatch")
    if row.get("status") != "request_accepted":
        raise ValueError(
            "role environment must be request_accepted and can be gated only once"
        )
    gate_path, gate_hash = _verified_gate_artifact(
        private_root, gate_result_ref, environment_id=environment_id, kind="gate"
    )
    evaluation_path, evaluation_hash = _verified_gate_artifact(
        private_root,
        untouched_evaluation_ref,
        environment_id=environment_id,
        kind="evaluation",
    )
    gate_payload = json.loads(Path(gate_path).read_text(encoding="utf-8"))
    evaluation_payload = json.loads(Path(evaluation_path).read_text(encoding="utf-8"))
    if bool(gate_payload["accepted"]) is not bool(accepted):
        raise ValueError("gate artifact disposition mismatch")
    if accepted and evaluation_payload.get("quality_gate_passed") is not True:
        raise ValueError("untouched evaluation did not pass its quality gate")
    status = "accepted" if accepted else "rejected"
    gated = {
        **row,
        "status": status,
        "schema": STATE_SCHEMA,
        "environment_id": environment_id,
        "deterministic_gate_result": gate_path,
        "deterministic_gate_hash": gate_hash,
        "untouched_evaluation_ref": evaluation_path,
        "untouched_evaluation_hash": evaluation_hash,
        "gated_at": utc_now(),
    }
    acknowledge_feedback(
        private_root,
        feedback_id=str(row["feedback_id"]),
        recipient=recipient,
        ack_id=f"ack::{environment_id}",
        disposition=status,
        applied_artifact_refs=(
            _bound_ref(path),
            _bound_ref(Path(gate_path), gate_hash),
            _bound_ref(Path(evaluation_path), evaluation_hash),
        ),
    )
    _write_state(
        path,
        {
            "schema": STATE_SCHEMA,
            "environment_id": environment_id,
            "status": status,
            "deterministic_gate_result": gate_path,
            "deterministic_gate_hash": gate_hash,
            "untouched_evaluation_ref": evaluation_path,
            "untouched_evaluation_hash": evaluation_hash,
            "gated_at": gated["gated_at"],
        },
    )
    write_adaptive_trial_record(
        private_root,
        trial_id=_adaptive_trial_id_from_row(row),
        stage="final_gate_accepted" if accepted else "final_gate_rejected",
        role=recipient,
        artifact_id=environment_id,
        evidence_refs=(
            _content_ref(
                _bound_ref(Path(gate_path), gate_hash),
                gate_hash,
                producer_completion_id=f"gate::{environment_id}",
                producer_schema="DeterministicRoleGate.v1",
                generation=int((row.get("task_spec") or {}).get("generation") or 0),
            ),
            _content_ref(
                _bound_ref(Path(evaluation_path), evaluation_hash),
                evaluation_hash,
                producer_completion_id=f"evaluation::{environment_id}",
                producer_schema="ValidationEpoch.v1",
                generation=int((row.get("task_spec") or {}).get("generation") or 0),
            ),
        ),
    )
    return gated


def accept_role_request(
    private_root: Path, *, recipient: str, environment_id: str
) -> dict[str, Any]:
    """Recipient-owned schema gate for a research request, not a policy promotion."""
    path = _candidate_path(private_root, recipient, environment_id)
    if not path.exists() or path.is_symlink():
        raise ValueError("role environment candidate does not exist")
    row = _effective_row(path)
    if row.get("schema") != SCHEMA or row.get("recipient") != recipient:
        raise ValueError("role request contract mismatch")
    if row.get("status") != "candidate":
        if row.get("status") == "request_accepted":
            _index_role_request(private_root, row)
            acknowledge_feedback(
                private_root,
                feedback_id=str(row["feedback_id"]),
                recipient=recipient,
                ack_id=f"request-ack::{environment_id}",
                disposition="request_accepted",
                applied_artifact_refs=(_bound_ref(path),),
            )
            return row
        raise ValueError("role request is no longer a candidate")
    if row.get("proposed_action") not in RECIPIENT_ACTIONS[recipient]:
        raise ValueError("role request action is not allowed")
    if not row.get("evidence_refs") or row.get("execution_allowed") is not False:
        raise ValueError("role request evidence or authority boundary invalid")
    accepted = {
        **row,
        "status": "request_accepted",
        "deterministic_gate_result": "recipient_contract_passed",
        "accepted_at": utc_now(),
    }
    # The compact generation index is written before the two-step ledger/state
    # transition.  It makes an ACK->state crash recoverable by exact id without
    # turning the immutable historical directory into a hot work queue.
    _index_role_request(private_root, row)
    acknowledge_feedback(
        private_root,
        feedback_id=str(row["feedback_id"]),
        recipient=recipient,
        ack_id=f"request-ack::{environment_id}",
        disposition="request_accepted",
        applied_artifact_refs=(_bound_ref(path),),
    )
    _write_state(
        path,
        {
            "schema": STATE_SCHEMA,
            "environment_id": environment_id,
            "status": "request_accepted",
            "deterministic_gate_result": "recipient_contract_passed",
            "accepted_at": accepted["accepted_at"],
        },
    )
    write_adaptive_trial_record(
        private_root,
        trial_id=_adaptive_trial_id_from_row(row),
        stage="request_accepted",
        role=recipient,
        artifact_id=environment_id,
        evidence_refs=tuple(row.get("evidence_refs") or ()),
    )
    return accepted
