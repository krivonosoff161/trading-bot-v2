from __future__ import annotations

from src.research_lab.strategy_history_proof import build_history_proofs
from src.research_lab.strategy_registry import REGISTRY
from src.research_lab.strategy_requirements import derive_requirement


def test_history_proof_covers_every_registered_family() -> None:
    proofs = build_history_proofs()

    assert set(proofs) == set(REGISTRY)
    assert len(proofs) == 27
    for strategy_id, proof in proofs.items():
        definition = REGISTRY[strategy_id]
        assert proof.strategy_id == strategy_id
        assert proof.required_history_bars == definition.required_history_bars(
            definition.parameter_defaults
        )
        assert proof.boundary_rows == proof.required_history_bars
        assert proof.before_boundary_rows == max(0, proof.required_history_bars - 1)
        assert proof.before_boundary_status in {
            "no_signal_before_boundary",
            "no_usable_rows_before_boundary",
        }
        assert proof.boundary_status in {"signals", "no_signal_predicate"}
        assert proof.boundary_reason


def test_history_proof_covers_every_formula_parameter_term() -> None:
    proofs = build_history_proofs()

    for strategy_id, proof in proofs.items():
        formula_terms = {
            key
            for formula in REGISTRY[strategy_id].history_formulas
            for key, _multiplier in formula.terms
        }
        assert set(proof.parameter_boundary_checks) == formula_terms
        for key, check in proof.parameter_boundary_checks.items():
            assert check.parameter == key
            assert check.boundary_value >= check.default_value
            assert check.required_history_bars >= proof.required_history_bars
            assert check.status in {"signals", "no_signal_predicate"}
            assert check.reason


def test_data_requirements_use_exact_history_without_hiding_side_data() -> None:
    proofs = build_history_proofs()

    for strategy_id, proof in proofs.items():
        definition = REGISTRY[strategy_id]
        req = derive_requirement(strategy_id, "BTC_USDT_SWAP", "1h")
        assert req.warmup_bars == proof.required_history_bars
        assert req.history_formulas == definition.history_formula_labels()
        assert req.required_data == definition.required_data
        if definition.required_data:
            assert proof.required_data_missing_status == "side_data_unavailable"
            assert proof.required_data_missing_reason
        else:
            assert proof.required_data_missing_status == "not_required"


def test_history_proof_claim_boundary_is_public_synthetic_only() -> None:
    proofs = build_history_proofs()

    for proof in proofs.values():
        assert proof.fixture_scope == "public_synthetic_ohlcv"
        assert proof.claim_boundary == (
            "formula_generator_history_alignment_only_no_private_history_or_profitability_claim"
        )
