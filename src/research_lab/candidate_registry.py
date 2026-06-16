# -*- coding: utf-8 -*-
"""Private candidate registry: one JSONL file of everything the lab has graded.

The registry lives in the private research workspace. Public code defines the
schema and upsert mechanics only; entries themselves never leave the private
root.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

SCHEMA = "strategy_lab_candidate_registry.v1"
NEXT_REVIEW_DAYS = {
    "REJECT": 0,            # no scheduled review
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
) -> dict[str, Any]:
    """Build a registry entry from a RunResult-like object."""
    created = created_at or dt.datetime.now(dt.timezone.utc).isoformat()
    status = getattr(result, "validation_status", "") or "OBSERVE"
    return {
        "schema": SCHEMA,
        "candidate_id": result.run_id,
        "experiment_id": experiment_id,
        "symbol": result.symbol,
        "strategy_id": result.family,
        "params": result.params,
        "timeframe": (
            getattr(spec, "timeframe", "")
            or str((getattr(result, "metrics", {}) or {}).get("data_file_timeframe") or "")
        ),
        "filters": dict(getattr(spec, "filters", {}) or {}),
        "fees_bps": float(getattr(spec, "fees_bps", 7.0)) if spec is not None else 7.0,
        "slippage_bps": float(getattr(spec, "slippage_bps", 3.0)) if spec is not None else 3.0,
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


def upsert_entries(path: Path, entries: list[dict[str, Any]]) -> dict[str, int]:
    """Insert or update entries keyed by (experiment_id, candidate_id).

    Existing `created_at` is preserved so repeated smoke runs stay stable.
    The file is rewritten sorted by key, which keeps diffs deterministic.
    """
    existing = load_entries(path)
    index = {(e.get("experiment_id"), e.get("candidate_id")): e for e in existing}
    stats = {"added": 0, "updated": 0}
    for entry in entries:
        key = (entry.get("experiment_id"), entry.get("candidate_id"))
        old = index.get(key)
        if old:
            entry = {**entry, "created_at": old.get("created_at") or entry["created_at"]}
            entry["next_review"] = _next_review(entry["created_at"], entry["validation_status"])
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


def load_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            entries.append(row)
    return entries


def registry_summary(path: Path) -> dict[str, Any]:
    """Public-safe summary: counts and label only, no entry payloads."""
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
        "exists": path.exists(),
        "entries": len(entries),
        "unique_candidates": len(unique_candidate_ids),
        "by_validation_status": by_status,
    }


def _metrics_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "n_trades", "win_rate", "avg_net_pct", "total_net_pct", "profit_factor",
        "max_drawdown_pct", "train_avg_net_pct", "test_avg_net_pct", "test_trades",
        "best_trade_share", "stress_avg_net_pct",
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
