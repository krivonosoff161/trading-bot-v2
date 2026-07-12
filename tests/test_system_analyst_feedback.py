from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.research_lab.system_analyst_feedback import (
    acknowledge_feedback,
    build_feedback,
    pending_feedback,
    quality_gate,
    route_feedback,
    evidence_content_hash,
    source_refs_hash,
    validate_schema,
)


NOW = "2026-07-11T10:00:00+00:00"


def _payload(feedback_id: str = "saf-1", *, supersedes: str = "") -> dict:
    source_refs = ["lineage:link-1", "report:validation-1"]
    source_evidence = {
        source_refs[0]: {"lineage": "frozen-1"},
        source_refs[1]: {"report": "frozen-1"},
    }
    evidence_hashes = {ref: evidence_content_hash(value) for ref, value in source_evidence.items()}
    return {
        "schema": "SystemAnalystFeedback.v1",
        "feedback_id": feedback_id,
        "subject_ref": "candidate:c-1",
        "summary": "Evidence supports a bounded retest, not promotion.",
        "recipients": ["farm", "validator", "trader"],
        "provenance": {
            "observed_at": "2026-07-11T08:00:00+00:00",
            "hypothesis_frozen_at": "2026-07-11T08:15:00+00:00",
            "outcome_window_end": "2026-07-11T08:30:00+00:00",
            "knowledge_cutoff_at": "2026-07-11T08:45:00+00:00",
            "evaluation_started_at": "2026-07-11T09:30:00+00:00",
            "generated_at": "2026-07-11T09:00:00+00:00",
            "valid_until": "2026-07-12T09:00:00+00:00",
            "source_refs": source_refs,
            "source_evidence_hashes": evidence_hashes,
            "source_evidence": source_evidence,
            "source_hash": source_refs_hash(source_refs, evidence_hashes),
        },
        "recommendations": [
            {
                "recipient": recipient,
                "action": "retest_candidate" if recipient != "trader" else "review_paper_outcome",
                "reason": "Deterministic evidence requires review.",
                "evidence_refs": [source_refs[0]],
            }
            for recipient in ("farm", "validator", "trader")
        ],
        "quality_score": 0.9,
        "quality_reasons": ["two bounded evidence references"],
        "supersedes": supersedes,
        "advisory_only": True,
        "paper_only": True,
        "execution_allowed": False,
    }


def test_schema_valid_llm_output_is_not_quality_accepted() -> None:
    payload = _payload()
    payload["quality_score"] = 0.2
    assert validate_schema(payload)[0] is True
    accepted, problems = quality_gate(payload, now=NOW)
    assert accepted is False
    assert "quality_score_below_threshold" in problems


@pytest.mark.parametrize("field,value", [("execution_allowed", True), ("paper_only", False)])
def test_no_authority_is_fail_closed(field: str, value: bool) -> None:
    payload = _payload()
    payload[field] = value
    assert validate_schema(payload)[0] is False
    with pytest.raises(ValueError):
        build_feedback(payload, now=NOW)


def test_nested_prohibited_action_and_unknown_recipient_are_rejected() -> None:
    payload = _payload()
    payload["recommendations"][0]["parameters"] = {"order": "buy"}
    payload["recipients"] = ["farm", "operator"]
    valid, problems = validate_schema(payload)
    assert valid is False
    assert "prohibited_field:order" in problems
    assert "recipients_not_allowlisted" in problems


def test_temporal_provenance_expires_fail_closed() -> None:
    accepted, problems = quality_gate(_payload(), now="2026-07-13T10:00:00+00:00")
    assert accepted is False
    assert problems == ["provenance_expired"]


def test_evaluation_window_must_start_after_feedback_is_generated() -> None:
    payload = _payload()
    payload["provenance"]["evaluation_started_at"] = "2026-07-11T08:00:00+00:00"
    valid, problems = validate_schema(payload)
    assert valid is False
    assert "evaluation_not_after_feedback_freeze" in problems


def test_feedback_cannot_use_knowledge_from_its_evaluation_window() -> None:
    payload = _payload()
    payload["provenance"]["evaluation_started_at"] = "2026-07-11T08:40:00+00:00"
    valid, problems = validate_schema(payload)
    assert valid is False
    assert "evaluation_not_after_feedback_freeze" in problems


def test_route_and_ack_are_idempotent(tmp_path) -> None:
    feedback = build_feedback(_payload(), now=NOW)
    path = route_feedback(tmp_path, feedback)
    route_feedback(tmp_path, feedback)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 4
    assert len(pending_feedback(tmp_path, "farm")) == 1

    acknowledge_feedback(tmp_path, feedback_id="saf-1", recipient="farm", ack_id="ack-1")
    acknowledge_feedback(tmp_path, feedback_id="saf-1", recipient="farm", ack_id="ack-1")
    assert pending_feedback(tmp_path, "farm") == []
    assert len(path.read_text(encoding="utf-8").splitlines()) == 5


def test_ack_requires_prior_route_and_is_recipient_bound(tmp_path) -> None:
    with pytest.raises(ValueError, match="not routed"):
        acknowledge_feedback(tmp_path, feedback_id="missing", recipient="farm", ack_id="ack-1")
    route_feedback(tmp_path, build_feedback(_payload(), now=NOW))
    acknowledge_feedback(tmp_path, feedback_id="saf-1", recipient="farm", ack_id="ack-1")
    with pytest.raises(ValueError, match="conflict"):
        acknowledge_feedback(tmp_path, feedback_id="saf-1", recipient="validator", ack_id="ack-1")


def test_ack_id_cannot_change_disposition_or_evidence(tmp_path) -> None:
    route_feedback(tmp_path, build_feedback(_payload(), now=NOW))
    acknowledge_feedback(
        tmp_path, feedback_id="saf-1", recipient="farm", ack_id="ack-1",
        disposition="rejected", applied_artifact_refs=("artifact:a",),
    )
    with pytest.raises(ValueError, match="conflict"):
        acknowledge_feedback(
            tmp_path, feedback_id="saf-1", recipient="farm", ack_id="ack-1",
            disposition="accepted", applied_artifact_refs=("artifact:b",),
        )


def test_supersedes_hides_old_feedback(tmp_path) -> None:
    route_feedback(tmp_path, build_feedback(_payload("saf-1"), now=NOW))
    route_feedback(tmp_path, build_feedback(_payload("saf-2", supersedes="saf-1"), now=NOW))
    pending = pending_feedback(tmp_path, "validator")
    assert [row["feedback_id"] for row in pending] == ["saf-2"]


def test_conflicting_reuse_of_feedback_id_is_rejected(tmp_path) -> None:
    route_feedback(tmp_path, build_feedback(_payload(), now=NOW))
    changed = _payload()
    changed["summary"] = "Different accepted payload."
    with pytest.raises(ValueError, match="payload conflict"):
        route_feedback(tmp_path, build_feedback(changed, now=NOW))


def test_tampered_ledger_event_is_rejected(tmp_path) -> None:
    path = route_feedback(tmp_path, build_feedback(_payload(), now=NOW))
    rows = path.read_text(encoding="utf-8").splitlines()
    event = json.loads(rows[0])
    event["feedback"]["summary"] = "tampered"
    rows[0] = json.dumps(event)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        pending_feedback(tmp_path, "farm")


def test_concurrent_routing_remains_idempotent(tmp_path) -> None:
    feedback = build_feedback(_payload(), now=NOW)
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda _: route_feedback(tmp_path, feedback), range(16)))
    assert len({str(path) for path in paths}) == 1
    assert len(paths[0].read_text(encoding="utf-8").splitlines()) == 4
