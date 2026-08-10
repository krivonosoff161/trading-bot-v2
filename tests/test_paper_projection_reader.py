import json

from src.research_lab.paper_projection_reader import (
    read_projection_view,
    select_current_terminal_training_rows,
)


def _generation():
    return {
        "current": True,
        "display_only": False,
        "generation_status": "completed",
        "paper_only": True,
        "execution_allowed": False,
        "paper_generation_run_id": "run-v2",
        "account_generation_id": "account-v2",
        "paper_subject_generation_ids": ["subject-v2"],
        "items": [
            {
                "source_signal_id": "signal-v2",
                "paper_generation_run_id": "run-v2",
                "paper_subject_generation_id": "subject-v2",
                "terminal_lifecycle_event_id": "terminal-v2",
                "account_generation_id": "account-v2",
                "paper_account_decision": "position_closed",
                "okx_inst_id": "X-USDT-SWAP",
                "timeframe": "1h",
                "setup_family": "continuation",
                "side": "long",
                "boundary_ts": 100,
                "farm_geometry_profile_id": "base",
                "outcome": {"net_pct": 0.75},
                "paper_account": {"pnl_usdt": 1.5},
            }
        ],
    }


def _training_row():
    return {
        "paper_only": True,
        "execution_allowed": False,
        "signal_id": "signal-v2",
        "lifecycle_schema": "PaperSignalLifecycle.v2",
        "immutable_terminal_evidence": True,
        "paper_generation_run_id": "run-v2",
        "paper_subject_generation_id": "subject-v2",
        "terminal_lifecycle_event_id": "terminal-v2",
        "account_generation_id": "account-v2",
        "symbol": "X_USDT_SWAP",
        "timeframe": "1h",
        "family": "continuation",
        "side": "long",
        "boundary_ts": 100,
        "farm_geometry_profile_id": "base",
        "net_pct": 0.75,
        "paper_pnl_usdt": 1.5,
    }


def test_legacy_file_is_display_only_before_v2_activation(tmp_path):
    legacy = tmp_path / "trades.json"
    legacy.write_text(json.dumps({"items": [{"runtime_id": "legacy"}]}), encoding="utf-8")

    view = read_projection_view(tmp_path, "trades", legacy_snapshot=legacy)

    assert view["current"] is False
    assert view["generation_status"] == "legacy_unversioned_projection"
    assert view["items"] == [{"runtime_id": "legacy"}]


def test_existing_invalid_authority_database_blocks_legacy_fallback(tmp_path):
    legacy = tmp_path / "trades.json"
    legacy.write_text(json.dumps({"items": [{"runtime_id": "legacy"}]}), encoding="utf-8")
    database = tmp_path / "paper-evidence.sqlite3"
    database.write_bytes(b"not-a-sqlite-database")

    view = read_projection_view(
        tmp_path,
        "trades",
        legacy_snapshot=legacy,
        evidence_database_path=database,
    )

    assert view["current"] is False
    assert view["authority_database_exists"] is True
    assert view["items"] == []


def test_training_selector_accepts_only_exact_current_account_bound_terminal_row():
    row = _training_row()

    selected = select_current_terminal_training_rows([row], _generation())

    assert selected["eligible_rows"] == 1
    assert selected["excluded_rows"] == 0
    assert selected["items"] == [row]
    assert selected["items"][0] is not row
    assert selected["current_generation_compatible"] is True


def test_training_selector_rejects_stale_unbound_and_non_account_rows():
    stale = _training_row() | {"paper_generation_run_id": "old-run"}
    unbound = _training_row() | {"paper_subject_generation_id": ""}
    no_account_result = _training_row() | {"paper_pnl_usdt": None}

    selected = select_current_terminal_training_rows(
        [stale, unbound, no_account_result],
        _generation(),
    )

    assert selected["eligible_rows"] == 0
    assert selected["rejection_counts"] == {
        "paper_generation_run_mismatch": 1,
        "paper_subject_generation_mismatch": 1,
        "terminal_account_result_missing": 1,
    }


def test_training_selector_rejects_mutable_export_policy_or_result_forgery():
    wrong_profile = _training_row() | {
        "farm_geometry_profile_id": "runner_probe"
    }
    wrong_pnl = _training_row() | {"paper_pnl_usdt": 99.0}

    selected = select_current_terminal_training_rows(
        [wrong_profile, wrong_pnl],
        _generation(),
    )

    assert selected["eligible_rows"] == 0
    assert selected["rejection_counts"] == {
        "current_projection_policy_fields_mismatch": 1,
        "current_projection_result_mismatch": 1,
    }


def test_training_selector_fails_closed_when_projection_is_display_only():
    generation = _generation() | {
        "current": False,
        "display_only": True,
        "generation_status": "legacy_unversioned_projection",
    }

    selected = select_current_terminal_training_rows([_training_row()], generation)

    assert selected["eligible_rows"] == 0
    assert selected["rejection_counts"] == {
        "generation_not_current_or_complete": 1
    }
    assert selected["current_generation_compatible"] is False


def test_training_selector_accepts_completed_current_empty_generation():
    generation = _generation() | {
        "paper_subject_generation_ids": [],
        "items": [],
    }

    selected = select_current_terminal_training_rows([], generation)

    assert selected["items"] == []
    assert selected["source_rows"] == 0
    assert selected["eligible_rows"] == 0
    assert selected["rejection_counts"] == {}
    assert selected["paper_generation_run_id"] == "run-v2"
    assert selected["current_generation_compatible"] is True
    assert selected["display_only"] is False


def test_training_selector_rejects_nonempty_projection_without_subject_identity():
    generation = _generation() | {"paper_subject_generation_ids": []}

    selected = select_current_terminal_training_rows([_training_row()], generation)

    assert selected["items"] == []
    assert selected["rejection_counts"] == {
        "generation_not_current_or_complete": 1
    }
    assert selected["current_generation_compatible"] is False
