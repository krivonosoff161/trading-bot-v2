from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from scripts.ci.check_trading_portfolio_docs import (
    REQUIRED_GOVERNANCE,
    ROOT,
    _uncontrolled_implementation_paths,
    load_contract,
    validate_contract,
)


def test_current_trading_portfolio_contract_is_valid() -> None:
    assert validate_contract(load_contract()) == []


def test_documentation_governance_fields_fail_closed() -> None:
    for field in REQUIRED_GOVERNANCE:
        contract = load_contract()
        contract[field] = "synthetic-mismatch"

        failures = validate_contract(contract)

        assert f"invalid documentation governance field: {field}" in failures


def test_duplicate_module_owner_and_invalid_authority_fail_closed() -> None:
    contract = load_contract()
    duplicate = deepcopy(contract["modules"][0])
    duplicate["owner_repository"] = "another-repository"
    duplicate["authority"] = "runtime"
    contract["modules"].append(duplicate)

    failures = validate_contract(contract)

    assert any("duplicate or empty module_id" in item for item in failures)
    assert any("invalid owner" in item for item in failures)
    assert any("invalid authority" in item for item in failures)


def test_implemented_module_without_evidence_is_rejected() -> None:
    contract = load_contract()
    contract["modules"][0]["implemented_evidence"] = []

    failures = validate_contract(contract)

    assert any("lacks evidence" in item for item in failures)


def test_private_pointer_is_rejected_without_echoing_value() -> None:
    contract = load_contract()
    synthetic_pointer = "X:\\synthetic-private\\state.sqlite"
    contract["modules"][0]["missing_evidence"] = synthetic_pointer

    failures = validate_contract(contract)

    assert "roadmap contains a private or runtime pointer" in failures
    assert all(synthetic_pointer not in item for item in failures)


def test_uncontrolled_current_document_is_rejected(tmp_path: Path) -> None:
    contract = load_contract()
    document = tmp_path / "current.md"
    document.write_text("# Current\n\nStatus: **CURRENT**\n", encoding="utf-8")
    contract["current_documents"] = [
        {
            "path": document.name,
            "scope": "synthetic",
            "evidence": "synthetic",
            "residual_risk": "synthetic",
            "next_gate": "synthetic",
        }
    ]
    contract["modules"] = []

    failures = validate_contract(contract, root=tmp_path)

    assert any("lacks control field" in item for item in failures)


def test_validator_root_is_repository_root() -> None:
    assert (ROOT / "docs" / "trading-portfolio-roadmap.yaml").is_file()


def test_only_documentation_control_paths_are_allowed_after_baseline() -> None:
    assert _uncontrolled_implementation_paths(
        [
            ".github/workflows/ci.yml",
            "docs/README.md",
            "CURRENT_STATE.md",
            "scripts/ci/check_trading_portfolio_docs.py",
            "tests/test_trading_portfolio_docs.py",
        ]
    ) == []
    assert _uncontrolled_implementation_paths(
        ["src/research_lab/product_progress.py", "docs/README.md"]
    ) == ["src/research_lab/product_progress.py"]
