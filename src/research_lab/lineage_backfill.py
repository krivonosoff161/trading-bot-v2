"""Derived lineage backfill for existing paper/research artifacts.

This pass never rewrites legacy logs. It writes old_id -> new lineage id mappings
and a summary under the private root only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import append_jsonl, stable_id, utc_now
from src.research_lab.paper_signals import store

SCHEMA = "LineageBackfillMapping.v1"
SUMMARY_SCHEMA = "LineageBackfillSummary.v1"
LEGACY_UNKNOWN_SOURCE = "legacy_unknown_source"


def mapping_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "lineage" / "backfill_mapping.jsonl"


def summary_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "lineage" / "backfill_summary.json"


def _load_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        return list(data.get("items") or [])
    return []


def _ids(surface: str, old_id: str, source: str) -> dict[str, str]:
    base = {"surface": surface, "old_id": old_id, "source": source or LEGACY_UNKNOWN_SOURCE}
    return {
        "scanner_event_id": stable_id("se", base),
        "data_packet_id": stable_id("mdp", base),
        "feature_packet_id": stable_id("fp", base),
        "setup_candidate_id": stable_id("setup", base),
        "validation_id": stable_id("validation", base),
        "paper_signal_id": old_id if surface == "paper_signals" else "",
        "telegram_card_id": old_id if surface == "paper_telegram_preview" else "",
        "outcome_id": stable_id("outcome", base),
        "training_row_id": old_id if surface == "paper_signal_training" else stable_id("training", base),
    }


def _mapping(surface: str, old_id: str, *, source: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    lineage = _ids(surface, old_id, source or LEGACY_UNKNOWN_SOURCE)
    return {
        "schema": SCHEMA,
        "surface": surface,
        "old_id": old_id,
        "source": source or LEGACY_UNKNOWN_SOURCE,
        "lineage": lineage,
        "extra": extra or {},
        "created_at": utc_now(),
        "paper_only": True,
        "execution_allowed": False,
    }


def build_lineage_backfill(private_root: Path, *, limit: int | None = None) -> dict[str, Any]:
    private_root = Path(private_root)
    out = mapping_path(private_root)
    if out.exists():
        out.unlink()
    rows: list[dict[str, Any]] = []

    for sig in store.load_signals(private_root):
        source = sig.source or LEGACY_UNKNOWN_SOURCE
        rows.append(_mapping("paper_signals", sig.signal_id, source=source, extra={
            "symbol": sig.symbol,
            "timeframe": sig.timeframe,
            "family": sig.setup_family,
        }))

    derived = private_root / "state" / "derived"
    for surface, filename, id_key in (
        ("paper_signal_training", "paper_signal_training.json", "training_row_id"),
        ("paper_telegram_preview", "paper_telegram_preview.json", "telegram_card_id"),
        ("main_paper_instructions", "main_paper_instructions.json", "instruction_id"),
        ("main_paper_consumed", "main_paper_consumed.json", "consumer_id"),
        ("main_paper_runtime_queue", "main_paper_runtime_queue.json", "runtime_id"),
        ("main_paper_runtime_observation", "main_paper_runtime_observation.json", "runtime_id"),
    ):
        for item in _load_items(derived / filename):
            old_id = str(item.get(id_key) or item.get("source_signal_id") or item.get("signal_id") or "")
            if not old_id:
                continue
            rows.append(_mapping(surface, old_id, source=str(item.get("source") or LEGACY_UNKNOWN_SOURCE)))

    state = private_root / "state"
    for surface, filename, id_key in (
        ("pfr_records", "strategy_lab.sqlite", ""),
        ("setup_memory", "setup_outcome_memory.jsonl", ""),
        ("validation_results", "validation_feedback.jsonl", ""),
    ):
        path = state / filename
        if path.exists():
            rows.append(_mapping(surface, stable_id("legacy", {"surface": surface, "path": filename}), extra={
                "legacy_artifact": f"strategy-lab/state/{filename}",
                "reason": "source rows require legacy parser",
            }))

    if limit is not None:
        rows = rows[: max(0, int(limit))]
    for row in rows:
        append_jsonl(out, row)

    by_surface: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for row in rows:
        by_surface[row["surface"]] = by_surface.get(row["surface"], 0) + 1
        by_source[row["source"]] = by_source.get(row["source"], 0) + 1
    summary = {
        "schema": SUMMARY_SCHEMA,
        "rows": len(rows),
        "by_surface": by_surface,
        "by_source": by_source,
        "mapping_label": "strategy-lab/state/lineage/backfill_mapping.jsonl",
        "non_destructive": True,
        "paper_only": True,
        "execution_allowed": False,
    }
    sp = summary_path(private_root)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
