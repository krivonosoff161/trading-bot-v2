"""Shared fail-closed reader for current v2 paper projections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.paper_evidence_store import PaperEvidenceStore


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
