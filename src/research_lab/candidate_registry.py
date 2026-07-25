# -*- coding: utf-8 -*-
"""Private candidate registry: compact base plus immutable runtime segments.

The registry lives in the private research workspace. Public code defines the
schema and upsert mechanics only; entries themselves never leave the private
root.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any
import uuid

SCHEMA = "strategy_lab_candidate_registry.v1"
NEXT_REVIEW_DAYS = {
    "REJECT": 0,  # no scheduled review
    "OBSERVE": 14,
    "REGIME_SPECIFIC": 7,
    "FORWARD_PAPER": 7,
}


def registry_path(private_root: Path) -> Path:
    return private_root / "candidate-registry" / "candidates.jsonl"


def build_entry(
    experiment_id: str,
    result: Any,
    artifact_label: str,
    created_at: str | None = None,
    spec: Any | None = None,
    search_trial_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a registry entry from a RunResult-like object."""
    created = created_at or dt.datetime.now(dt.timezone.utc).isoformat()
    status = getattr(result, "validation_status", "") or "OBSERVE"
    plan_meta = dict(getattr(spec, "plan_meta", {}) or {}) if spec is not None else {}
    trial: dict[str, Any] = next(
        (
            row
            for row in (search_trial_evidence or {}).get("trials", [])
            if str(row.get("run_id") or "") == str(result.run_id)
        ),
        {},
    )
    return {
        "schema": SCHEMA,
        "candidate_id": result.run_id,
        "experiment_id": experiment_id,
        "symbol": result.symbol,
        "strategy_id": result.family,
        "params": result.params,
        "timeframe": (
            getattr(spec, "timeframe", "")
            or str(
                (getattr(result, "metrics", {}) or {}).get("data_file_timeframe") or ""
            )
        ),
        "plan_group": str(plan_meta.get("group") or ""),
        "plan_meta": plan_meta,
        "search_family_id": str(
            (search_trial_evidence or {}).get("search_family_id") or ""
        ),
        "search_trial_id": str(trial.get("execution_id") or ""),
        "effective_n_trials": int(
            ((search_trial_evidence or {}).get("search_space") or {}).get(
                "effective_n_trials", 0
            )
            or 0
        ),
        "filters": dict(getattr(spec, "filters", {}) or {}),
        "fees_bps": float(getattr(spec, "fees_bps", 7.0)) if spec is not None else 7.0,
        "slippage_bps": float(getattr(spec, "slippage_bps", 3.0))
        if spec is not None
        else 3.0,
        "metrics_summary": _metrics_summary(result.metrics),
        "decision": result.decision,
        "validation_status": status,
        "validation_reasons": list(getattr(result, "validation_reasons", []) or []),
        "risk_flags": list(getattr(result, "risk_flags", []) or []),
        "next_action": getattr(result, "next_action", "") or "",
        "regime_summary": dict(getattr(result, "regime_summary", {}) or {}),
        "artifact_label": artifact_label,
        "created_at": created,
        "next_review": _next_review(created, status),
    }


def _segment_dir(path: Path) -> Path:
    return path.parent / "segments"


def _write_append_only_segment(
    path: Path,
    entries: list[dict[str, Any]],
) -> dict[str, int]:
    """Publish one immutable atomic delta without rewriting the base registry."""

    if not entries:
        return {"appended": 0, "segment_bytes": 0}
    segment_dir = _segment_dir(path)
    segment_dir.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
        for entry in sorted(
            entries,
            key=lambda row: (
                str(row.get("experiment_id") or ""),
                str(row.get("candidate_id") or ""),
            ),
        )
    )
    payload_bytes = payload.encode("utf-8")
    digest = hashlib.sha256(payload_bytes).hexdigest()
    name = f"{time.time_ns()}_{os.getpid()}_{uuid.uuid4().hex}_{digest[:16]}.jsonl"
    target = segment_dir / name
    temporary = target.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    # The immutable segment is a write-ahead record. Append one bounded payload
    # to the historical JSONL path and fsync it; this is O(delta), not O(history),
    # and preserves direct-reader compatibility. If append/cleanup is
    # interrupted, `load_entries` also reads the retained segment and dedupes it.
    needs_newline = False
    if path.exists() and path.stat().st_size:
        with path.open("rb") as handle:
            handle.seek(-1, os.SEEK_END)
            needs_newline = handle.read(1) != b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab", buffering=0) as handle:
        if needs_newline:
            handle.write(b"\n")
        handle.write(payload_bytes)
        os.fsync(handle.fileno())
    try:
        target.unlink()
    except OSError:
        # A retained WAL segment is safe: readers merge by immutable key and a
        # later transactional compactor can remove it.
        pass
    return {
        "appended": len(entries),
        "segment_bytes": len(payload_bytes),
    }


def upsert_entries(
    path: Path,
    entries: list[dict[str, Any]],
    *,
    append_only: bool = False,
) -> dict[str, int]:
    """Insert or update entries keyed by (experiment_id, candidate_id).

    Existing `created_at` is preserved so repeated smoke runs stay stable.
    Manual maintenance keeps the compact deterministic base representation.
    Runtime publication uses an immutable write-ahead segment plus a bounded
    fsynced append so one result cannot rewrite an unbounded historical JSONL
    file in the compute critical path.
    """
    if append_only:
        return _write_append_only_segment(path, entries)
    segment_dir = _segment_dir(path)
    if segment_dir.exists() and any(segment_dir.glob("*.jsonl")):
        raise RuntimeError(
            "candidate registry has immutable runtime segments; "
            "offline compaction requires a dedicated transactional compactor"
        )
    existing = load_entries(path)
    index = {(e.get("experiment_id"), e.get("candidate_id")): e for e in existing}
    stats = {"added": 0, "updated": 0}
    for entry in entries:
        key = (entry.get("experiment_id"), entry.get("candidate_id"))
        old = index.get(key)
        if old:
            entry = {
                **entry,
                "created_at": old.get("created_at") or entry["created_at"],
            }
            entry["next_review"] = _next_review(
                entry["created_at"], entry["validation_status"]
            )
            stats["updated"] += 1
        else:
            stats["added"] += 1
        index[key] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = [index[k] for k in sorted(index, key=lambda k: (str(k[0]), str(k[1])))]
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for entry in ordered:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    stats["total"] = len(ordered)
    return stats


def _registry_files(path: Path) -> list[Path]:
    files = [path] if path.exists() else []
    segment_dir = _segment_dir(path)
    if segment_dir.exists():
        files.extend(sorted(segment_dir.glob("*.jsonl"), key=lambda item: item.name))
    return files


def load_entries(path: Path) -> list[dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for source in _registry_files(path):
        try:
            handle = source.open("r", encoding="utf-8")
        except OSError:
            continue
        with handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                key = (
                    str(row.get("experiment_id") or ""),
                    str(row.get("candidate_id") or ""),
                )
                old = index.get(key)
                if old is not None:
                    created_at = old.get("created_at") or row.get("created_at")
                    row = {**row, "created_at": created_at}
                    status = str(row.get("validation_status") or "OBSERVE")
                    row["next_review"] = _next_review(str(created_at or ""), status)
                index[key] = row
    return [
        index[key]
        for key in sorted(index, key=lambda item: (str(item[0]), str(item[1])))
    ]


def registry_summary(path: Path) -> dict[str, Any]:
    """Public-safe summary: counts and label only, no entry payloads."""
    sources = _registry_files(path)
    entries = load_entries(path)
    by_status: dict[str, int] = {}
    unique_candidate_ids: set[str] = set()
    for entry in entries:
        status = str(entry.get("validation_status") or "UNKNOWN")
        by_status[status] = by_status.get(status, 0) + 1
        candidate_id = str(entry.get("candidate_id") or "")
        if candidate_id:
            unique_candidate_ids.add(candidate_id)
    return {
        "registry_label": "strategy-lab/candidate-registry/candidates.jsonl",
        "exists": bool(sources),
        "base_exists": path.exists(),
        "segment_files": sum(
            1 for source in sources if source.parent == _segment_dir(path)
        ),
        "entries": len(entries),
        "unique_candidates": len(unique_candidate_ids),
        "by_validation_status": by_status,
    }


def _metrics_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "n_trades",
        "win_rate",
        "avg_net_pct",
        "total_net_pct",
        "profit_factor",
        "max_drawdown_pct",
        "train_avg_net_pct",
        "test_avg_net_pct",
        "test_trades",
        "best_trade_share",
        "stress_avg_net_pct",
        "profit_factor_state",
    ]
    summary = {k: metrics.get(k) for k in keys if k in metrics}
    if isinstance(metrics.get("entry_timing"), dict):
        summary["entry_timing"] = dict(metrics["entry_timing"])
    return summary


def _next_review(created_at: str, status: str) -> str:
    days = NEXT_REVIEW_DAYS.get(status, 14)
    if days <= 0:
        return ""
    try:
        base = dt.datetime.fromisoformat(created_at)
    except ValueError:
        base = dt.datetime.now(dt.timezone.utc)
    return (base + dt.timedelta(days=days)).date().isoformat()
