import json

from src.research_lab.agent_role_registry import role_registry_summary, validate_role_payload
from src.research_lab.llm_role_reviews import normalize_review_payload, request_role_review, review_summary
from src.research_lab.llm_provider import NullProposalProvider


def test_role_registry_exposes_required_roles():
    summary = role_registry_summary()
    role_ids = {row["role_id"] for row in summary["rows"]}
    assert {
        "farm_calculator_advisor",
        "outcome_reviewer",
        "validator_reviewer",
        "source_trust_reviewer",
        "vip_vision_reviewer",
        "education_qa",
    }.issubset(role_ids)
    assert summary["paper_only"] is True
    assert summary["execution_allowed"] is False


def test_review_role_rejects_trade_authority_fields():
    ok, problems = validate_role_payload(
        "outcome_reviewer",
        {
            "diagnosis": "late_entry",
            "confidence": 0.8,
            "entry": 1.23,
            "execute": True,
        },
    )
    assert ok is False
    assert "forbidden field: entry" in problems
    assert "forbidden field: execute" in problems


def test_review_payload_normalizes_provider_synonyms_without_bypassing_forbidden_fields():
    payload = normalize_review_payload(
        "validator_reviewer",
        {
            "classification": "underpowered",
            "reason": "Too few trades.",
            "suggested_next_tests": "collect more bars",
            "entry": 123.0,
        },
    )
    assert payload["validator_class"] == "underpowered"
    assert payload["summary"] == "Too few trades."
    assert payload["next_test_dimensions"] == ["collect more bars"]
    ok, problems = validate_role_payload("validator_reviewer", payload)
    assert ok is False
    assert "forbidden field: entry" in problems


def test_disabled_provider_writes_private_review_row(tmp_path):
    review = request_role_review(
        tmp_path,
        role_id="validator_reviewer",
        source_ref="case_1",
        source_payload={"validator_class": "underpowered"},
        provider=NullProposalProvider(),
    )
    assert review.accepted is False
    assert review.problems == ["provider_not_configured"]

    path = tmp_path / "state" / "llm_advice" / "validator_reviews.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["schema"] == "ValidatorReview.v1"
    assert rows[0]["paper_only"] is True
    assert rows[0]["execution_allowed"] is False

    summary = review_summary(tmp_path)
    assert summary["roles"]["validator_reviewer"]["rows"] == 1
    assert summary["roles"]["validator_reviewer"]["accepted"] == 0
