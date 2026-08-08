"""Shared fail-closed reader for current v2 paper projections."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from src.research_lab.paper_evidence_store import PaperEvidenceStore
from src.research_lab.paper_signals import outcome_evidence

TRUSTED_TRAINING_LIFECYCLE_SCHEMA = "PaperSignalLifecycle.v2"


def default_evidence_database(private_root: Path | str) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_evidence.sqlite3"


def read_projection_view(
    private_root: Path | str,
    projection_kind: str,
    *,
    legacy_snapshot: Path | str | None = None,
    evidence_database_path: Path | str | None = None,
) -> dict[str, Any]:
    """Read DB authority, never falling back when an authority DB is present.

    Before v2 activation, a legacy file remains available as clearly labelled
    display-only history.  Once the selected DB exists, an incomplete, mismatched or
    corrupt generation returns no current items instead of silently using filenames.
    """
    database_path = (
        Path(evidence_database_path)
        if evidence_database_path is not None
        else default_evidence_database(private_root)
    )
    authority_exists = database_path.is_file()
    current = PaperEvidenceStore.read_completed_projection(database_path, projection_kind)
    if current.get("current"):
        return {
            **current,
            "authority_database_path": str(database_path),
            "authority_database_exists": True,
            "source_kind": "v2_completed_projection",
        }
    if authority_exists:
        return {
            **current,
            "items": [],
            "authority_database_path": str(database_path),
            "authority_database_exists": True,
            "source_kind": "v2_authority_unavailable",
        }
    items: list[dict[str, Any]] = []
    legacy_path = Path(legacy_snapshot) if legacy_snapshot is not None else None
    if legacy_path is not None and legacy_path.is_file():
        try:
            payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        items = [item for item in raw_items or [] if isinstance(item, dict)]
    return {
        "current": False,
        "display_only": True,
        "generation_status": "legacy_unversioned_projection",
        "paper_only": True,
        "execution_allowed": False,
        "items": items,
        "authority_database_path": str(database_path),
        "authority_database_exists": False,
        "source_kind": "legacy_display_only",
        "legacy_snapshot_path": str(legacy_path) if legacy_path is not None else "",
    }


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def _same_number(left: Any, right: Any) -> bool:
    return bool(
        _is_number(left)
        and _is_number(right)
        and math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)
    )


def _instrument_key(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_")


def _same_int(left: Any, right: Any) -> bool:
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return False


def select_current_terminal_training_rows(
    rows: list[dict[str, Any]],
    generation: dict[str, Any],
) -> dict[str, Any]:
    """Select account-bound terminal rows from one completed current generation.

    A filename and a lifecycle label are not evidence authority.  The generation
    envelope must be the current completed v2 projection, and every training row
    must bind back to its exact run, subject, terminal lifecycle event and account
    generation.  The returned rows are copies so downstream aggregators cannot
    mutate the source export while adding derived labels.
    """
    source_rows = len(rows)
    run_id = str(generation.get("paper_generation_run_id") or "")
    account_generation_id = str(generation.get("account_generation_id") or "")
    subject_generation_ids = {
        str(value)
        for value in generation.get("paper_subject_generation_ids") or []
        if str(value or "")
    }
    projection_items = [
        item for item in generation.get("items") or [] if isinstance(item, dict)
    ]
    projection_by_signal = {
        str(item.get("source_signal_id") or ""): item
        for item in projection_items
        if str(item.get("source_signal_id") or "")
    }
    generation_ready = bool(
        generation.get("current") is True
        and generation.get("display_only") is False
        and str(generation.get("generation_status") or "") == "completed"
        and generation.get("paper_only") is True
        and generation.get("execution_allowed") is False
        and run_id
        and account_generation_id
        and subject_generation_ids
    )
    if not generation_ready:
        rejection_counts = (
            {"generation_not_current_or_complete": source_rows} if source_rows else {}
        )
        return {
            "items": [],
            "source_rows": source_rows,
            "eligible_rows": 0,
            "excluded_rows": source_rows,
            "rejection_counts": rejection_counts,
            "paper_generation_run_id": run_id,
            "account_generation_id": account_generation_id,
            "generation_status": str(generation.get("generation_status") or ""),
            "current_generation_compatible": False,
            "display_only": True,
            "paper_only": True,
            "execution_allowed": False,
        }

    selected: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for row in rows:
        reason = ""
        signal_id = str(row.get("paper_signal_id") or row.get("signal_id") or "")
        subject_generation_id = str(row.get("paper_subject_generation_id") or "")
        projection = projection_by_signal.get(signal_id)
        if not outcome_evidence.is_market_outcome(row):
            reason = "operational_incident_not_training_evidence"
        elif row.get("paper_only") is not True or row.get("execution_allowed") is not False:
            reason = "paper_boundary_mismatch"
        elif (
            str(row.get("lifecycle_schema") or "")
            != TRUSTED_TRAINING_LIFECYCLE_SCHEMA
        ):
            reason = "lifecycle_schema_mismatch"
        elif row.get("immutable_terminal_evidence") is not True:
            reason = "immutable_terminal_evidence_missing"
        elif str(row.get("paper_generation_run_id") or "") != run_id:
            reason = "paper_generation_run_mismatch"
        elif (
            not subject_generation_id
            or subject_generation_id not in subject_generation_ids
        ):
            reason = "paper_subject_generation_mismatch"
        elif str(row.get("account_generation_id") or "") != account_generation_id:
            reason = "account_generation_mismatch"
        elif not str(row.get("terminal_lifecycle_event_id") or ""):
            reason = "terminal_lifecycle_event_missing"
        elif not _is_number(row.get("net_pct")) or not _is_number(
            row.get("paper_pnl_usdt")
        ):
            reason = "terminal_account_result_missing"
        elif projection is None:
            reason = "current_projection_item_missing"
        elif str(projection.get("paper_account_decision") or "") in {
            "allocation_rejected",
            "counterfactual_excluded",
        }:
            reason = "current_projection_account_excluded"
        elif (
            str(projection.get("paper_generation_run_id") or "") != run_id
            or str(projection.get("paper_subject_generation_id") or "")
            != subject_generation_id
            or str(projection.get("account_generation_id") or "")
            != account_generation_id
            or str(projection.get("terminal_lifecycle_event_id") or "")
            != str(row.get("terminal_lifecycle_event_id") or "")
        ):
            reason = "current_projection_lineage_mismatch"
        elif (
            _instrument_key(
                projection.get("okx_inst_id") or projection.get("pair")
            )
            != _instrument_key(row.get("okx_inst_id") or row.get("symbol"))
            or str(projection.get("timeframe") or "")
            != str(row.get("timeframe") or "")
            or str(projection.get("setup_family") or "")
            != str(row.get("family") or row.get("setup_family") or "")
            or str(projection.get("side") or "") != str(row.get("side") or "")
            or not _same_int(
                projection.get("boundary_ts"),
                row.get("boundary_ts"),
            )
            or str(projection.get("farm_geometry_profile_id") or "")
            != str(row.get("farm_geometry_profile_id") or "")
        ):
            reason = "current_projection_policy_fields_mismatch"
        elif not _same_number(
            (projection.get("outcome") or {}).get("net_pct"),
            row.get("net_pct"),
        ) or not _same_number(
            (projection.get("paper_account") or {}).get("pnl_usdt"),
            row.get("paper_pnl_usdt"),
        ):
            reason = "current_projection_result_mismatch"
        if reason:
            rejected[reason] += 1
            continue
        selected.append(dict(row))

    return {
        "items": selected,
        "source_rows": source_rows,
        "eligible_rows": len(selected),
        "excluded_rows": source_rows - len(selected),
        "rejection_counts": dict(sorted(rejected.items())),
        "paper_generation_run_id": run_id,
        "account_generation_id": account_generation_id,
        "generation_status": "completed",
        "current_generation_compatible": True,
        "display_only": False,
        "paper_only": True,
        "execution_allowed": False,
    }
