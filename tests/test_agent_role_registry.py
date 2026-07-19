import json

from src.research_lab.advisory_payload_validator import normalize_advisory_key
from src.research_lab.agent_role_registry import role_registry_summary, validate_role_payload
from src.research_lab.llm_role_reviews import build_review_input, normalize_review_payload, request_role_review, review_summary
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


def test_review_role_rejects_nested_trade_authority_fields():
    ok, problems = validate_role_payload(
        "outcome_reviewer",
        {
            "summary": "Advisory text only.",
            "warnings": [
                {
                    "execute": True,
                    "order": "BUY",
                    "nested": {"stop_loss": 123.0},
                }
            ],
            "confidence": 0.7,
        },
    )

    assert ok is False
    assert any("execute" in problem for problem in problems)
    assert any("order" in problem for problem in problems)
    assert any("stop_loss" in problem for problem in problems)


def test_review_role_normalizes_forbidden_key_variants_recursively():
    ok, problems = validate_role_payload(
        "validator_reviewer",
        {
            "summary": "No authority.",
            "evidence": [
                {"Auto-Trade": False},
                {"execution allowed": False},
                {"TakeProfitPlan": "promote"},
            ],
            "confidence": 0.5,
        },
    )

    assert ok is False
    assert any("auto_trade" in problem or "Auto-Trade" in problem for problem in problems)
    assert any("execution_allowed" in problem or "execution allowed" in problem for problem in problems)
    assert any("take_profit_plan" in problem or "TakeProfitPlan" in problem for problem in problems)


def test_review_role_rejects_unicode_confusable_authority_keys_recursively():
    confusable_keys = {
        "\u0430uto_trade": "auto_trade",  # Cyrillic small a
        "executi\u043en_allowed": "execution_allowed",  # Cyrillic small o
        "take_pr\u03bffit_plan": "take_profit_plan",  # Greek small omicron
        "\uff21\uff55\uff54\uff4f\uff0d\uff34\uff52\uff41\uff44\uff45": "auto_trade",  # NFKC full-width form
    }

    for key, expected in confusable_keys.items():
        assert normalize_advisory_key(key) == expected

    ok, problems = validate_role_payload(
        "validator_reviewer",
        {
            "summary": "No authority.",
            "evidence": [{key: True} for key in confusable_keys],
            "confidence": 0.5,
        },
    )

    assert ok is False
    for expected in confusable_keys.values():
        assert any(expected in problem for problem in problems)

    for unmapped_key in ("a\u0301uto_trade", "auto\u200dtrade"):
        ok, problems = validate_role_payload(
            "validator_reviewer",
            {"summary": "No authority.", "evidence": [{unmapped_key: True}]},
        )
        assert ok is False
        assert any("advisory key" in problem or "auto_trade" in problem for problem in problems)

    ok, problems = validate_role_payload(
        "validator_reviewer",
        {"summary": "\u0411\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u044b\u0439 \u0442\u0435\u043a\u0441\u0442; \u043a\u043b\u044e\u0447\u0438 ASCII.", "confidence": 0.5},
    )
    assert ok is True
    assert problems == []


def test_outcome_reviewer_accepts_learning_fields_but_not_authority():
    ok, problems = validate_role_payload(
        "outcome_reviewer",
        {
            "summary": "Loss gave back after favourable move.",
            "review_kind": "loss",
            "outcome_bucket": "gave_back",
            "actionability": "retest_exit_or_capture",
            "counterfactual_summary": "Earlier profit lock needs deterministic retest.",
            "counterfactual_delta_class": "possible_loss_to_small_win",
            "confidence_basis": "mfe_exceeded_mae",
            "evidence_refs": ["training_s1"],
            "learning_tags": ["exit_capture"],
            "candidate_rule": "do not promote; retest exit dimension only",
            "requires_retest": True,
            "risk_to_good_trades": "may reduce upside if too early",
            "confidence": 0.8,
        },
    )
    assert ok is True
    assert problems == []

    ok, problems = validate_role_payload(
        "outcome_reviewer",
        {
            "review_kind": "loss",
            "paper_ready": True,
            "execution_allowed": True,
        },
    )
    assert ok is False
    assert "forbidden field: paper_ready" in problems
    assert "forbidden field: execution_allowed" in problems


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


def test_review_payload_normalizes_wrappers_and_scalar_types():
    payload = normalize_review_payload(
        "source_trust_reviewer",
        {
            "review": {
                "source_classification": "scanner",
                "trust_adjustment": "neutral",
                "confidence": "75%",
                "evidence": "fresh public event",
                "warnings": "needs follow-up",
            }
        },
    )
    assert payload["source_class"] == "scanner"
    assert payload["trust_delta"] == "neutral"
    assert payload["confidence"] == 0.75
    assert payload["evidence"] == ["fresh public event"]
    assert payload["warnings"] == ["needs follow-up"]
    ok, problems = validate_role_payload("source_trust_reviewer", payload)
    assert ok is True
    assert problems == []


def test_source_trust_review_input_marks_internal_farm_source():
    payload = json.loads(
        build_review_input(
            "source_trust_reviewer",
            {"source": "farm", "symbol": "HMSTR-USDT-SWAP", "reason": "paper setup"},
        )
    )

    assert payload["source_context"]["source_name"] == "farm"
    assert payload["source_context"]["is_internal_strategy_lab_source"] is True
    assert "public website" in payload["source_context"]["internal_source_rule"]
    assert payload["hard_rules"]["execution_allowed"] is False


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
