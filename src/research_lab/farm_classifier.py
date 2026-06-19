# -*- coding: utf-8 -*-
"""Classify a completed run into unique-candidate rows (symbol+tf+family+params+fingerprint).

Reads a finished run's ``metrics.json`` (written by the worker under the private root)
and turns each result into a ``unique_candidates`` row keyed by
``symbol::timeframe::family::params_hash::data_fingerprint``. The classification labels
(REJECT / OBSERVE / FORWARD_PAPER / NEEDS_*_DATA …) are produced upstream by the
grader/validator; this only KEYS them so the operator sees the freshest unique result
per logical candidate without duplicates. Read-only over artifacts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.data_fingerprint import params_hash

# Lite statuses worth handing to hard validation (gate is enforced at export time too).
VALIDATION_ELIGIBLE = {"FORWARD_PAPER", "REGIME_SPECIFIC"}


def _safe(symbol: str) -> str:
    return str(symbol).replace("-", "_").replace("/", "_").upper()


def classify_run(
    private_root: Path, run_dir_label: str, *, timeframe: str,
    data_fingerprint: str | None, task_id: int | None = None,
) -> list[dict[str, Any]]:
    """Read a completed run dir -> unique_candidate dicts (one per result row)."""
    run_dir = Path(private_root) / run_dir_label
    metrics_path = run_dir / "metrics.json"
    try:
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []
    fp = data_fingerprint or "nofp"
    out: list[dict[str, Any]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        symbol = _safe(row.get("symbol") or "")
        family = str(row.get("family") or "")
        ph = params_hash(row.get("params") or {})
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        out.append({
            "uc_key": f"{symbol}::{timeframe}::{family}::{ph}::{fp}",
            "symbol": symbol, "timeframe": timeframe, "family": family,
            "params_hash": ph, "data_fingerprint": fp,
            "decision": str(row.get("decision") or "UNKNOWN"),
            "validation_status": str(row.get("validation_status") or ""),
            "hard_status": "",
            "n_trades": int(metrics.get("n_trades") or 0),
            "avg_net_pct": float(metrics.get("avg_net_pct") or 0.0),
            "candidate_id": str(row.get("run_id") or row.get("candidate_id") or ""),
            "params": dict(row.get("params") or {}),
            "run_dir_label": run_dir_label, "task_id": task_id,
        })
    return out
