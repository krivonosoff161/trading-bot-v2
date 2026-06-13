# -*- coding: utf-8 -*-
"""Aggregate candidate review pack for an LLM advisor — export only, no API call.

Builds a compact, summaries-only pack from the private candidate registry so a
human (or, later, an explicitly opted-in local model) can review the strongest
candidates and propose next *tests*. This module never calls a paid API and never
claims profitability. The pack is written under the private research root.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from src.research_lab.candidate_registry import load_entries, registry_path
from src.research_lab.paths import resolve_private_root

SCHEMA = "strategy_lab_registry_review_pack.v1"
REVIEW_STATUSES = {"FORWARD_PAPER", "REGIME_SPECIFIC", "OBSERVE"}
_RANK = {"FORWARD_PAPER": 3, "REGIME_SPECIFIC": 2, "OBSERVE": 1}
DISCLAIMER = (
    "This is not a profitability claim. Statuses are research labels. "
    "The model may suggest next tests only; it must not declare anything live-tradable."
)
_METRIC_KEYS = (
    "n_trades", "win_rate", "avg_net_pct", "test_avg_net_pct",
    "profit_factor", "max_drawdown_pct", "best_trade_share", "stress_avg_net_pct",
)


def review_pack_dir(private_root: Path) -> Path:
    return private_root / "reports" / "llm_review"


def build_registry_review_pack(entries: list[dict[str, Any]], *, limit: int = 10) -> dict[str, Any]:
    candidates = [e for e in entries if str(e.get("validation_status")) in REVIEW_STATUSES]
    candidates.sort(key=_sort_key)
    selected = candidates[: max(1, limit)]
    return {
        "schema": SCHEMA,
        "disclaimer": DISCLAIMER,
        "instruction": (
            "Review these candidate summaries. Identify likely overfit, weak OOS, "
            "regime dependence, late entries, and fragility. Suggest next tests only — "
            "propose 1-3 next experiment specs as JSON drafts. Do NOT claim "
            "profitability or live-tradability."
        ),
        "candidate_total": len(candidates),
        "candidate_count": len(selected),
        "candidates": [_summarize(e) for e in selected],
        "entry_timing_pain": _entry_timing_pain(selected),
        "top_questions": [
            "Which candidates survive a parameter-neighborhood re-test (not a single lucky best)?",
            "Where is the entry late: low capture ratio or high adverse excursion before it works?",
            "Which results depend on a single regime bucket and collapse otherwise?",
            "What validation gate is still missing before a human looks at this?",
        ],
        "suggested_next_checks": [
            "parameter-neighborhood sweep around each FORWARD_PAPER candidate",
            "regime-filtered re-run for REGIME_SPECIFIC candidates",
            "cost-stress and slippage re-check before any human review",
            "out-of-sample window extension where test_trades is small",
        ],
    }


def _entry_timing_pain(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate entry-timing pain across selected candidates (empty if none)."""
    blocks = []
    for e in entries:
        metrics = e.get("metrics_summary") if isinstance(e.get("metrics_summary"), dict) else {}
        if isinstance(metrics.get("entry_timing"), dict):
            blocks.append(metrics["entry_timing"])
    if not blocks:
        return {}
    out: dict[str, Any] = {}
    for k in ("avg_capture_ratio", "avg_mfe_pct", "avg_mae_pct", "late_entry_rate"):
        vals = [float(b[k]) for b in blocks if b.get(k) is not None]
        if vals:
            out[k] = round(sum(vals) / len(vals), 4)
    return out


def export_review_pack(
    private_root: Path,
    *,
    limit: int = 10,
    allow_public_output: bool = False,
) -> dict[str, Any]:
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    entries = load_entries(registry_path(private_root))
    pack = build_registry_review_pack(entries, limit=limit)
    out_dir = review_pack_dir(private_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"registry_review_pack_{stamp}.json"
    out_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "registry_entries": len(entries),
        "candidate_count": pack["candidate_count"],
        "pack_label": f"reports/llm_review/{out_path.name}",
    }


def _summarize(entry: dict[str, Any]) -> dict[str, Any]:
    metrics = entry.get("metrics_summary") if isinstance(entry.get("metrics_summary"), dict) else {}
    return {
        "candidate_id": entry.get("candidate_id"),
        "symbol": entry.get("symbol"),
        "strategy_id": entry.get("strategy_id"),
        "validation_status": entry.get("validation_status"),
        "params": entry.get("params"),
        "metrics": {k: metrics.get(k) for k in _METRIC_KEYS if k in metrics},
        "entry_timing": metrics.get("entry_timing") if isinstance(metrics.get("entry_timing"), dict) else {},
        "risk_flags": entry.get("risk_flags") or [],
        "validation_reasons": entry.get("validation_reasons") or [],
        "next_action": entry.get("next_action") or "",
        "regime_summary": entry.get("regime_summary") or {},
        "review_due": entry.get("next_review") or "",
        "artifact_label": entry.get("artifact_label") or "",
    }


def _sort_key(entry: dict[str, Any]) -> tuple[int, float, float, int]:
    metrics = entry.get("metrics_summary") if isinstance(entry.get("metrics_summary"), dict) else {}
    return (
        -_RANK.get(str(entry.get("validation_status")), 0),
        -_float(metrics.get("test_avg_net_pct")),
        -_float(metrics.get("avg_net_pct")),
        -int(metrics.get("n_trades") or 0),
    )


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
