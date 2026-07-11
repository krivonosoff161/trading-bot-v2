"""Canonical paper-only bridge from accepted outcome reviews to role inboxes."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Iterable

from src.research_lab.lineage_contract import stable_id
from src.research_lab.lineage_contract import utc_now
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
            {"source_ref": source_ref, "review_id": review.get("review_id")},
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
                {"recipient": "farm", "action": "retest_candidate", "reason": "Run a bounded next-window experiment.", "evidence_refs": source_refs},
                {"recipient": "validator", "action": "retest_candidate", "reason": "Evaluate independently on an untouched window.", "evidence_refs": source_refs},
                {"recipient": "trader", "action": "review_paper_outcome", "reason": "Review the paper lifecycle without changing state authority.", "evidence_refs": source_refs},
            ],
            "quality_score": 0.9,
            "quality_reasons": ["accepted bounded outcome review", "training lineage present"],
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
