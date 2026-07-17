"""Canonical paper-only bridge from accepted outcome reviews to role inboxes."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable

from src.research_lab.lineage_contract import stable_id
from src.research_lab.lineage_contract import utc_now
from src.research_lab.adaptive_trial import adaptive_trial_id
from src.research_lab.outcome_learning import load_outcome_reviews, load_training_rows
from src.research_lab.role_environment import (
    accept_role_request,
    materialize_role_environment,
    recoverable_role_requests,
)
from src.research_lab.system_analyst_feedback import (
    build_feedback,
    evidence_content_hash,
    route_feedback,
    source_refs_hash,
)


DEFAULT_MAX_FEEDBACK_PER_CYCLE = 20


def _string_list(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, dict):
        rows = [f"{key}:{value[key]}" for key in sorted(value)]
    elif isinstance(value, (list, tuple)):
        rows = [str(item) for item in value]
    elif value:
        rows = [str(value)]
    else:
        rows = []
    return [row[:240] for row in rows if row.strip()][:limit]


def _task_spec(
    review_payload: dict[str, Any], recipient: str, source_row: dict[str, Any], source_ref: str
) -> dict[str, Any]:
    prior_spec = source_row.get("task_spec") if isinstance(source_row.get("task_spec"), dict) else {}
    subject_source = prior_spec.get("subject") if isinstance(prior_spec.get("subject"), dict) else source_row
    generation = int(prior_spec.get("generation", -1)) + 1
    subject = {
        key: subject_source.get(key)
        for key in ("symbol", "timeframe", "family", "candidate_id", "training_row_id")
        if subject_source.get(key)
    }
    if not subject:
        subject = {"source_identity": stable_id("subject", {"source_ref": source_ref})}
    task = {
        "schema": "RoleTaskSpec.v1",
        "kind": {
            "farm": "bounded_sweep",
            "validator": "untouched_validation",
            "trader": "paper_replay",
        }[recipient],
        "dimensions": _string_list(review_payload.get("next_test_dimensions")),
        "tests": _string_list(review_payload.get("counterfactual_tests")),
        "hypotheses": _string_list(review_payload.get("parameter_hypotheses")),
        "subject": subject,
        "source_ref": source_ref,
        "generation": generation,
        "parent_family_id": str(source_row.get("search_family_id") or ""),
        "parent_trial_id": str(source_row.get("search_trial_id") or ""),
        "parent_effective_n_trials": int(source_row.get("effective_n_trials") or 0),
        "cumulative_family_policy": (
            "cumulative"
            if source_row.get("search_family_id") and source_row.get("search_trial_id")
            else "independent"
        ),
        "requires_deterministic_mapping": True,
        "requires_untouched_evaluation": True,
        "paper_only": True,
    }
    task["adaptive_trial_id"] = adaptive_trial_id(task)
    return task


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _iso(value: Any, fallback: dt.datetime) -> str:
    if isinstance(value, (int, float)) and float(value) > 0:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        return dt.datetime.fromtimestamp(number, tz=dt.timezone.utc).isoformat()
    if isinstance(value, str) and value:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(dt.timezone.utc).isoformat()
        except ValueError:
            pass
    return fallback.isoformat()


def feedback_payloads_from_outcomes(
    training_rows: Iterable[dict[str, Any]],
    review_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    training = {str(row.get("training_row_id") or ""): row for row in training_rows}
    payloads: list[dict[str, Any]] = []
    for review in review_rows:
        if str(review.get("role_id") or "") != "outcome_reviewer" or not review.get("accepted"):
            continue
        source_ref = str(review.get("source_ref") or "")
        row = training.get(source_ref)
        if not row:
            continue
        review_payload = review.get("payload") if isinstance(review.get("payload"), dict) else {}
        generated = dt.datetime.fromisoformat(
            _iso(review.get("created_at"), dt.datetime.now(dt.timezone.utc))
        )
        observed = dt.datetime.fromisoformat(_iso(row.get("boundary_ts"), generated))
        if observed > generated:
            observed = generated
        source_refs = [f"training:{source_ref}", f"review:{review.get('review_id') or ''}"]
        source_evidence = {
            source_refs[0]: {
                "training_row_id": source_ref,
                "candidate_id": row.get("candidate_id"),
                "symbol": row.get("symbol"),
                "timeframe": row.get("timeframe"),
                "family": row.get("family"),
                "result": row.get("result"),
                "diagnosis": row.get("diagnosis"),
                "boundary_ts": row.get("boundary_ts"),
            },
            source_refs[1]: {
                "review_id": review.get("review_id"),
                "source_ref": source_ref,
                "created_at": review.get("created_at"),
                "payload": review_payload,
                "accepted": True,
            },
        }
        evidence_hashes = {
            ref: evidence_content_hash(value) for ref, value in source_evidence.items()
        }
        feedback_id = stable_id(
            "system_feedback",
            {
                "source_ref": source_ref,
                "review_id": review.get("review_id"),
                "contract": "typed_role_tasks_v1",
            },
            length=24,
        )
        payloads.append({
            "schema": "SystemAnalystFeedback.v1",
            "feedback_id": feedback_id,
            "subject_ref": f"candidate:{row.get('candidate_id') or source_ref}",
            "summary": str(review_payload.get("summary") or "Outcome evidence requires bounded review."),
            "recipients": ["farm", "validator", "trader"],
            "provenance": {
                "observed_at": observed.isoformat(),
                "hypothesis_frozen_at": observed.isoformat(),
                "outcome_window_end": generated.isoformat(),
                "knowledge_cutoff_at": generated.isoformat(),
                "generated_at": generated.isoformat(),
                "evaluation_started_at": (generated + dt.timedelta(microseconds=1)).isoformat(),
                "valid_until": (generated + dt.timedelta(days=7)).isoformat(),
                "source_refs": source_refs,
                "source_evidence_hashes": evidence_hashes,
                "source_evidence": source_evidence,
                "source_hash": source_refs_hash(source_refs, evidence_hashes),
            },
            "recommendations": [
                {
                    "recipient": "farm", "action": "retest_candidate",
                    "reason": "Run a bounded next-window experiment over the analyst dimensions.",
                    "evidence_refs": source_refs,
                    "task_spec": _task_spec(review_payload, "farm", row, source_ref),
                },
                {
                    "recipient": "validator", "action": "retest_candidate",
                    "reason": "Evaluate the candidate independently on an untouched window.",
                    "evidence_refs": source_refs,
                    "task_spec": _task_spec(review_payload, "validator", row, source_ref),
                },
                {
                    "recipient": "trader", "action": "review_paper_outcome",
                    "reason": "Replay the paper lifecycle against the analyst tests.",
                    "evidence_refs": source_refs,
                    "task_spec": _task_spec(review_payload, "trader", row, source_ref),
                },
            ],
            "quality_score": 0.9,
            "quality_reasons": ["accepted bounded outcome review", "training lineage present"],
            "advisory_only": True,
            "paper_only": True,
            "execution_allowed": False,
        })
    return payloads


def feedback_payloads_from_system_results(
    result_rows: Iterable[dict[str, Any]],
    draft_rows: Iterable[dict[str, Any]],
    *,
    max_generation: int = 2,
) -> list[dict[str, Any]]:
    results = {str(row.get("result_id") or ""): row for row in result_rows}
    payloads: list[dict[str, Any]] = []
    for draft in draft_rows:
        if str(draft.get("role_id") or "") != "system_analyst" or not draft.get("accepted"):
            continue
        source_ref = str(draft.get("source_ref") or "")
        result = results.get(source_ref)
        if not result:
            continue
        prior_spec = result.get("task_spec") if isinstance(result.get("task_spec"), dict) else {}
        if int(prior_spec.get("generation", 0)) >= max_generation:
            continue
        draft_payload = draft.get("payload") if isinstance(draft.get("payload"), dict) else {}
        generated = dt.datetime.fromisoformat(_iso(draft.get("created_at"), dt.datetime.now(dt.timezone.utc)))
        refs = [f"role_result:{source_ref}", f"system_review:{draft.get('review_id') or ''}"]
        raw_result = result.get("result") if isinstance(result.get("result"), dict) else {}
        result_evidence = {
            "result_id": source_ref,
            "environment_id": result.get("environment_id"),
            "feedback_id": result.get("feedback_id"),
            "recipient": result.get("recipient"),
            "task_spec": result.get("task_spec") or {},
            "status": raw_result.get("status") or "completed",
            "reason": raw_result.get("reason") or "",
            "task_id": raw_result.get("task_id") or 0,
            "task_type": raw_result.get("task_type") or "paper_replay",
            "result_ref": raw_result.get("result_ref") or "",
        }
        draft_evidence = {
            "review_id": draft.get("review_id"),
            "source_ref": source_ref,
            "created_at": draft.get("created_at"),
            "payload": draft_payload,
            "accepted": True,
        }
        evidence = {refs[0]: result_evidence, refs[1]: draft_evidence}
        hashes = {ref: evidence_content_hash(value) for ref, value in evidence.items()}
        feedback_id = stable_id(
            "system_feedback",
            {
                "source_ref": source_ref,
                "review_id": draft.get("review_id"),
                "contract": "typed_role_tasks_v1",
            },
            length=24,
        )
        payloads.append({
            "schema": "SystemAnalystFeedback.v1",
            "feedback_id": feedback_id,
            "subject_ref": f"role_result:{source_ref}",
            "summary": str(draft_payload.get("summary") or "Completed role work requires bounded follow-up."),
            "recipients": ["farm", "validator", "trader"],
            "provenance": {
                "observed_at": generated.isoformat(),
                "hypothesis_frozen_at": generated.isoformat(),
                "outcome_window_end": generated.isoformat(),
                "knowledge_cutoff_at": generated.isoformat(),
                "generated_at": generated.isoformat(),
                "evaluation_started_at": (generated + dt.timedelta(microseconds=1)).isoformat(),
                "valid_until": (generated + dt.timedelta(days=7)).isoformat(),
                "source_refs": refs,
                "source_evidence_hashes": hashes,
                "source_evidence": evidence,
                "source_hash": source_refs_hash(refs, hashes),
            },
            "recommendations": [
                {
                    "recipient": recipient,
                    "action": "review_paper_outcome" if recipient == "trader" else "retest_candidate",
                    "reason": "Use the completed role result for the next bounded evidence step.",
                    "evidence_refs": refs,
                    "task_spec": _task_spec(draft_payload, recipient, result, source_ref),
                }
                for recipient in ("farm", "validator", "trader")
            ],
            "quality_score": 0.9,
            "quality_reasons": ["completed role result", "accepted bounded system review"],
            "advisory_only": True,
            "paper_only": True,
            "execution_allowed": False,
        })
    return payloads


def run_system_analyst_cycle(
    private_root: Path,
    *,
    apply: bool,
    now: str | None = None,
    max_feedback: int = DEFAULT_MAX_FEEDBACK_PER_CYCLE,
) -> dict[str, Any]:
    if max_feedback < 1:
        raise ValueError("max_feedback must be positive")
    all_payloads = feedback_payloads_from_outcomes(
        load_training_rows(private_root), load_outcome_reviews(private_root)
    )
    all_payloads.extend(feedback_payloads_from_system_results(
        _read_jsonl(Path(private_root) / "state" / "derived" / "system_analyst_result_inbox.jsonl"),
        _read_jsonl(Path(private_root) / "state" / "llm_advice" / "system_analyst_drafts.jsonl"),
    ))
    payloads = sorted(
        all_payloads,
        key=lambda payload: str(payload.get("provenance", {}).get("generated_at") or ""),
        reverse=True,
    )[:max_feedback]
    summary: dict[str, Any] = {
        "schema": "system_analyst_cycle.v1",
        "feedback_candidates_total": len(all_payloads),
        "feedback_candidates": len(payloads),
        "max_feedback": max_feedback,
        "routed": 0,
        "rejected": 0,
        "rejection_reasons": {},
        "role_environment_candidates": {},
        "accepted_role_requests": {},
        "paper_only": True,
        "execution_allowed": False,
        "apply": bool(apply),
    }
    if not apply:
        return summary
    gate_now = now or utc_now()
    for payload in payloads:
        try:
            feedback = build_feedback(payload, now=gate_now)
        except ValueError as exc:
            reason = str(exc).removeprefix("feedback rejected: ")
            summary["rejected"] += 1
            summary["rejection_reasons"][reason] = (
                int(summary["rejection_reasons"].get(reason, 0)) + 1
            )
            continue
        route_feedback(private_root, feedback)
        summary["routed"] += 1
    for recipient in ("farm", "validator", "trader"):
        rows = materialize_role_environment(private_root, recipient=recipient)
        summary["role_environment_candidates"][recipient] = len(rows)
        recoverable = recoverable_role_requests(private_root, recipient)
        summary["accepted_role_requests"][recipient] = sum(
            1
            for row in recoverable
            if accept_role_request(
                private_root,
                recipient=recipient,
                environment_id=str(row["environment_id"]),
            ).get("status") == "request_accepted"
        )
    return summary
