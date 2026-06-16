# -*- coding: utf-8 -*-
"""Backfill a lost strategy timeframe in already-written hard-validation artifacts.

Older candidates were exported before the timeframe was recorded, so their
requests / reports / verdicts / feedback rows / setup cards carry
``timeframe="unknown"``. The real timeframe is still recoverable from the run's
data-file label (e.g. ``DOGE_USDT_SWAP_430d_1Dutc.json`` -> ``1d``) or the run
metrics. This module rebuilds a ``candidate_id -> timeframe`` index from the
registry + run metrics + the request payloads, then repairs the artifacts in
place. Unrecoverable rows are left ``unknown`` and reported, never faked.

Deterministic and offline. No network, no LLM, no live trading. Writes only
under the private research root.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.candidate_registry import load_entries
from src.research_lab.data_inventory import normalize_timeframe, timeframe_from_filename
from src.research_lab.hard_validation_export import _load_experiment_metrics, _recover_timeframe

UNKNOWN = "unknown"


def _recover_from_request(req: dict[str, Any]) -> str:
    """Timeframe recovered from a request payload (it carries data_file_label)."""
    tf = normalize_timeframe(req.get("timeframe"))
    if tf:
        return tf
    metrics = req.get("metrics") or {}
    tf = normalize_timeframe(metrics.get("data_file_timeframe"))
    if tf:
        return tf
    return timeframe_from_filename(str(metrics.get("data_file_label") or ""))


def build_timeframe_index(private_root: Path) -> dict[str, str]:
    """Map candidate_id -> recovered timeframe (only entries that resolve)."""
    index: dict[str, str] = {}

    # 1) registry + run metrics (the same recovery the exporter now uses).
    registry_file = private_root / "candidate-registry" / "candidates.jsonl"
    for entry in load_entries(registry_file):
        cid = str(entry.get("candidate_id") or "")
        if not cid or cid in index:
            continue
        metrics = _load_experiment_metrics(private_root, str(entry.get("artifact_label") or ""), entry) or {}
        tf = _recover_timeframe(metrics, entry)
        if tf and tf != UNKNOWN:
            index[cid] = tf

    # 2) request payloads (carry data_file_label even for legacy candidates).
    requests_dir = private_root / "hard_validation" / "requests"
    if requests_dir.exists():
        for path in sorted(requests_dir.glob("*.json")):
            try:
                req = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            cid = str(req.get("candidate_id") or path.stem)
            if cid in index:
                continue
            tf = _recover_from_request(req)
            if tf:
                index[cid] = tf
    return index


def _needs_repair(value: Any) -> bool:
    tf = str(value or "").strip()
    return tf == "" or tf == UNKNOWN


def _repair_json_file(path: Path, index: dict[str, str], *, dry_run: bool) -> str | None:
    """Repair a single {..., candidate_id, timeframe} JSON file. Returns the new tf if changed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not _needs_repair(data.get("timeframe")):
        return None
    cid = str(data.get("candidate_id") or path.stem.replace("setup-", ""))
    tf = index.get(cid)
    if not tf:
        return None
    if not dry_run:
        data["timeframe"] = tf
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return tf


def _row_candidate_id(row: dict[str, Any]) -> str:
    """Candidate id for a row, deriving it from setup_id (setup-<cid>) when absent."""
    cid = str(row.get("candidate_id") or "")
    if cid:
        return cid
    setup_id = str(row.get("setup_id") or "")
    if setup_id.startswith("setup-"):
        return setup_id[len("setup-"):]
    return ""


def _repair_jsonl(path: Path, index: dict[str, str], *, key: str, dry_run: bool) -> tuple[int, int]:
    """Repair rows in a JSONL file. Returns (repaired, unresolved)."""
    if not path.exists():
        return (0, 0)
    rows: list[dict[str, Any]] = []
    repaired = unresolved = 0
    changed = False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _needs_repair(row.get(key)):
            cid = _row_candidate_id(row)
            tf = index.get(cid)
            if tf:
                row[key] = tf
                repaired += 1
                changed = True
            else:
                unresolved += 1
        rows.append(row)
    if changed and not dry_run:
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
        )
    return (repaired, unresolved)


def _repair_registry(private_root: Path, index: dict[str, str], *, dry_run: bool) -> tuple[int, int]:
    path = private_root / "candidate-registry" / "candidates.jsonl"
    return _repair_jsonl(path, index, key="timeframe", dry_run=dry_run)


def repair_metadata(private_root: Path, *, dry_run: bool = True) -> dict[str, Any]:
    """Backfill timeframe across hard-validation + setup-library artifacts.

    Returns a per-artifact summary of repaired/unresolved counts.
    """
    index = build_timeframe_index(private_root)
    summary: dict[str, Any] = {"dry_run": dry_run, "index_size": len(index), "artifacts": {}}

    reg_rep, reg_unres = _repair_registry(private_root, index, dry_run=dry_run)
    summary["artifacts"]["candidate_registry"] = {"repaired": reg_rep, "unresolved": reg_unres}

    hv = private_root / "hard_validation"
    for sub in ("requests", "reports", "verdicts"):
        d = hv / sub
        rep = unres = 0
        if d.exists():
            for path in sorted(d.glob("*.json")):
                tf = _repair_json_file(path, index, dry_run=dry_run)
                if tf:
                    rep += 1
                else:
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        if isinstance(data, dict) and _needs_repair(data.get("timeframe")):
                            unres += 1
                    except (OSError, json.JSONDecodeError):
                        pass
        summary["artifacts"][f"hard_validation/{sub}"] = {"repaired": rep, "unresolved": unres}

    fb_rep, fb_unres = _repair_jsonl(hv / "feedback" / "feedback.jsonl", index, key="timeframe", dry_run=dry_run)
    summary["artifacts"]["hard_validation/feedback"] = {"repaired": fb_rep, "unresolved": fb_unres}

    lib = private_root / "setup_library"
    cards_rep = cards_unres = 0
    cards_dir = lib / "cards"
    if cards_dir.exists():
        for path in sorted(cards_dir.glob("*.json")):
            tf = _repair_json_file(path, index, dry_run=dry_run)
            if tf:
                cards_rep += 1
            else:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and _needs_repair(data.get("timeframe")):
                        cards_unres += 1
                except (OSError, json.JSONDecodeError):
                    pass
    summary["artifacts"]["setup_library/cards"] = {"repaired": cards_rep, "unresolved": cards_unres}

    idx_rep, idx_unres = _repair_jsonl(lib / "setup_index.jsonl", index, key="timeframe", dry_run=dry_run)
    summary["artifacts"]["setup_library/setup_index"] = {"repaired": idx_rep, "unresolved": idx_unres}

    summary["total_repaired"] = sum(a["repaired"] for a in summary["artifacts"].values())
    summary["total_unresolved"] = sum(a["unresolved"] for a in summary["artifacts"].values())
    return summary
