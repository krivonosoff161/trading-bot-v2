from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.ci import check_supply_chain_policy as policy


PINNED_CHECKOUT = "11bd71901bbe5b1630ceea73d27597364c9af683"
PINNED_SETUP_PYTHON = "a26af69be951a213d495a4c3e4e4022e16d87065"


def test_rejects_mutable_external_github_action_refs(tmp_path: Path) -> None:
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        """
        jobs:
          test:
            steps:
              - uses: actions/checkout@v4
              - uses: actions/setup-python@v5
              - uses: ./.github/actions/local-action
        """,
        encoding="utf-8",
    )

    failures = policy.check_workflow_action_refs([workflow])

    assert any("actions/checkout" in failure for failure in failures)
    assert any("actions/setup-python" in failure for failure in failures)
    assert not any("local-action" in failure for failure in failures)


def test_accepts_full_length_action_commit_pins(tmp_path: Path) -> None:
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        f"""
        jobs:
          test:
            steps:
              - uses: actions/checkout@{PINNED_CHECKOUT}
              - uses: actions/setup-python@{PINNED_SETUP_PYTHON}
              - uses: ./.github/actions/local-action
        """,
        encoding="utf-8",
    )

    assert policy.check_workflow_action_refs([workflow]) == []


def test_rejects_bare_and_ranged_ci_requirements(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements-ci.txt"
    requirements.write_text(
        """
        pandas>=2.0.0
        brotli
        numpy==1.*
        pytest==8.4.1
        """,
        encoding="utf-8",
    )

    failures = policy.check_ci_requirements(requirements)

    assert any("pandas" in failure and "exact ==" in failure for failure in failures)
    assert any("brotli" in failure and "exact ==" in failure for failure in failures)
    assert any("numpy" in failure and "wildcard" in failure for failure in failures)
    assert not any("pytest==8.4.1" in failure for failure in failures)


def test_verifies_ci_requirements_manifest_digest(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements-ci.txt"
    lock_sha = tmp_path / "requirements-ci.sha256"
    text = "pytest==8.4.1\n"
    requirements.write_text(text, encoding="utf-8")
    lock_sha.write_text(
        f"{hashlib.sha256(requirements.read_bytes()).hexdigest()}  requirements-ci.txt\n",
        encoding="utf-8",
    )

    assert policy.check_ci_requirements(requirements) == []
    assert policy.check_requirements_digest(requirements, lock_sha) == []

    lock_sha.write_text("0" * 64 + "  requirements-ci.txt\n", encoding="utf-8")

    assert policy.check_requirements_digest(requirements, lock_sha)


def test_secret_like_scan_reports_location_without_secret_value(tmp_path: Path) -> None:
    tracked = tmp_path / "docs" / "leak.md"
    tracked.parent.mkdir()
    secret_value = "SYNTHETIC_TOKEN_VALUE_DO_NOT_PRINT_1234567890"
    tracked.write_text(f"telegram_bot_token = '{secret_value}'\n", encoding="utf-8")

    failures = policy.scan_tracked_text_files([tracked], root=tmp_path)

    assert failures
    joined = "\n".join(failures)
    assert "docs/leak.md:1" in joined
    assert "telegram_bot_token" in joined
    assert secret_value not in joined


def test_current_repository_supply_chain_policy_passes() -> None:
    assert policy.evaluate_policy(Path.cwd()) == []
