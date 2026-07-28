"""Bounded Project Brain event retrieval through a verified archive catalog."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.research_lab.archive_catalog import ArchiveCatalog, ArchiveCatalogError


class ProjectBrainArchiveError(RuntimeError):
    """Archived Project Brain events are unsafe, stale, or outside the budget."""


def load_archived_project_brain_events(
    catalog: ArchiveCatalog,
    *,
    contours: Iterable[str],
    allowed_commit_shas: Iterable[str],
    max_records: int = 200,
    max_uncompressed_bytes: int = 2 * 1024 * 1024,
) -> list[dict[str, Any]]:
    """Return only revision-bound, contour-matched safe memory events.

    The archive catalog itself enforces content hashes, the public-safe
    sensitivity label, record schema, secret scanning, and byte/record budgets.
    """

    if max_records <= 0 or max_records > 1_000:
        raise ProjectBrainArchiveError("Project Brain archive record budget is invalid")
    if max_uncompressed_bytes <= 0 or max_uncompressed_bytes > 16 * 1024 * 1024:
        raise ProjectBrainArchiveError("Project Brain archive byte budget is invalid")
    contour_set = set(contours)
    commit_set = set(allowed_commit_shas)
    if not contour_set or not commit_set:
        raise ProjectBrainArchiveError(
            "Project Brain archive requires explicit contour and revision scope"
        )
    manifests = catalog.query(
        kinds=("project_brain_events",),
        limit=min(1_000, max_records),
    )
    record_events: list[dict[str, Any]] = []
    causal_candidates: list[dict[str, Any]] = []
    selected_record_ids: set[str] = set()
    selected_link_ids: set[str] = set()
    remaining_bytes = max_uncompressed_bytes
    for manifest in reversed(manifests):
        if manifest.source_revision not in commit_set:
            continue
        try:
            rows = catalog.read_bounded_jsonl(
                manifest.artifact_id,
                max_records=max_records,
                max_uncompressed_bytes=remaining_bytes,
            )
        except ArchiveCatalogError as exc:
            raise ProjectBrainArchiveError(
                "Project Brain archive verification failed"
            ) from exc
        for row in rows:
            if row.get("event") == "record":
                record = row.get("record")
                if not isinstance(record, dict):
                    raise ProjectBrainArchiveError(
                        "Project Brain archived record is malformed"
                    )
                if (
                    str(record.get("contour") or "") not in contour_set
                    or str(record.get("commit_sha") or "") not in commit_set
                ):
                    continue
                record_id = str(record.get("record_id") or "")
                if not record_id or record_id in selected_record_ids:
                    continue
                selected_record_ids.add(record_id)
                record_events.append(row)
            elif row.get("event") == "causal_link":
                # Causal links have no contour/commit of their own. They are
                # indexed only after their scoped records are selected.
                causal_candidates.append(row)
            else:
                raise ProjectBrainArchiveError(
                    "Project Brain archived event type is unsupported"
                )
            if len(record_events) >= max_records:
                return record_events
        remaining_bytes -= min(remaining_bytes, manifest.logical_bytes)
        if remaining_bytes <= 0:
            break
    events = list(record_events)
    for row in causal_candidates:
        link_id = str(row.get("link_id") or "")
        source_id = str(row.get("source_record_id") or "")
        target_id = str(row.get("target_record_id") or "")
        if (
            not link_id
            or link_id in selected_link_ids
            or source_id not in selected_record_ids
            or target_id not in selected_record_ids
        ):
            continue
        selected_link_ids.add(link_id)
        events.append(row)
        if len(events) >= max_records:
            break
    return events
