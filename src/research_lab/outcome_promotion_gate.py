"""Promotion-gate view for outcome-learning artifacts.

This module is deliberately conservative. It does not promote anything, does not
enqueue work, and does not call providers. It reads the existing learning,
shadow-forward, true-forward, and ready-catalog artifacts and explains what the
next non-execution stage is for each accepted outcome review.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable

from src.research_lab.outcome_learning import load_outcome_reviews, load_training_rows
from src.research_lab.ready_strategy_catalog import catalog_snapshot_path

SCHEMA = "OutcomePromotionGate.v1"

REVIEW_ONLY = "review_only"
NEEDS_RETEST = "needs_retest"
NEEDS_SHADOW = "needs_shadow"
NEEDS_TRUE_FORWARD = "needs_true_forward"
COLLECT_TRUE_FORWARD = "collect_true_forward"
OPERATOR_REVIEW_ONLY = "operator_review_only"
ELIGIBLE_FOR_OPERATOR_REVIEW = "eligible_for_operator_review"

_RETEST_ACTIONS = {"retest_exit_or_capture", "retest_entry_timing", "compare_breakeven_policy"}
_REVIEW_ACTIONS = {"cluster_before_retest", "observe_more"}
_PRESERVE_ACTIONS = {"preserve_pattern"}


@dataclass(frozen=True)
class PromotionGateVerdict:
    review_id: str
    source_ref: str
    candidate_id: str
    symbol: str
    timeframe: str
    family: str
    gate_stage: str
    reasons: list[str] = field(default_factory=list)
    evidence_refs: dict[str, str] = field(default_factory=dict)
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = "OutcomePromotionGateVerdict.v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _items_by_candidate(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = snapshot.get("items") if isinstance(snapshot.get("items"), list) else []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id:
            out[candidate_id] = row
    return out


def _registry_by_uc(private_root: Path, name: str) -> dict[str, dict[str, Any]]:
    data = _read_json(Path(private_root) / "state" / "derived" / name)
    rows = data.get("by_uc_key") if isinstance(data.get("by_uc_key"), dict) else {}
    return {str(key): value for key, value in rows.items() if isinstance(value, dict)}


def _candidate_keys(row: dict[str, Any]) -> set[str]:
    keys = {
        str(row.get("candidate_id") or ""),
        str(row.get("setup_candidate_id") or ""),
        str(row.get("validation_id") or ""),
        str(row.get("uc_key") or ""),
    }
    return {key for key in keys if key}


def _lookup_any(index: dict[str, dict[str, Any]], keys: Iterable[str]) -> tuple[str, dict[str, Any]]:
    for key in keys:
        row = index.get(key)
        if row is not None:
            return key, row
    return "", {}


def _review_payload(review: dict[str, Any]) -> dict[str, Any]:
    payload = review.get("payload")
    return payload if isinstance(payload, dict) else {}


def _gate_stage(
    *,
    actionability: str,
    shadow: dict[str, Any],
    true_forward: dict[str, Any],
    ready: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    true_status = str(true_forward.get("status") or "")
    if true_status == "matured":
        reasons.append("true_forward_matured_is_evidence_not_edge")
        if ready.get("status") == "ready_for_paper_runtime":
            reasons.append("hard_ready_catalog_exists")
            return ELIGIBLE_FOR_OPERATOR_REVIEW, reasons
        return OPERATOR_REVIEW_ONLY, reasons
    if true_forward:
        reasons.append(f"true_forward_status:{true_status or 'unknown'}")
        return COLLECT_TRUE_FORWARD, reasons
    if shadow:
        reasons.append("shadow_forward_registered")
        return NEEDS_TRUE_FORWARD, reasons
    if actionability in _RETEST_ACTIONS:
        reasons.append(f"accepted_review_requires_retest:{actionability}")
        return NEEDS_RETEST, reasons
    if actionability in _PRESERVE_ACTIONS:
        reasons.append("positive_pattern_needs_forward_watch")
        return NEEDS_SHADOW, reasons
    if actionability in _REVIEW_ACTIONS:
        reasons.append(f"review_only_actionability:{actionability}")
        return REVIEW_ONLY, reasons
    reasons.append(f"unknown_actionability:{actionability or 'empty'}")
    return REVIEW_ONLY, reasons


def build_gate_verdicts(
    training_rows: Iterable[dict[str, Any]],
    outcome_reviews: Iterable[dict[str, Any]],
    *,
    shadow_index: dict[str, dict[str, Any]] | None = None,
    true_forward_index: dict[str, dict[str, Any]] | None = None,
    ready_index: dict[str, dict[str, Any]] | None = None,
) -> list[PromotionGateVerdict]:
    rows_by_ref = {str(row.get("training_row_id") or ""): row for row in training_rows}
    shadow_index = shadow_index or {}
    true_forward_index = true_forward_index or {}
    ready_index = ready_index or {}
    verdicts: list[PromotionGateVerdict] = []
    for review in outcome_reviews:
        if str(review.get("role_id") or "") != "outcome_reviewer" or not bool(review.get("accepted")):
            continue
        source_ref = str(review.get("source_ref") or "")
        row = rows_by_ref.get(source_ref, {})
        payload = _review_payload(review)
        keys = _candidate_keys(row)
        candidate_id = str(row.get("candidate_id") or "")
        shadow_key, shadow = _lookup_any(shadow_index, keys)
        true_key, true_forward = _lookup_any(true_forward_index, keys)
        ready = ready_index.get(candidate_id, {}) if candidate_id else {}
        stage, reasons = _gate_stage(
            actionability=str(payload.get("actionability") or ""),
            shadow=shadow,
            true_forward=true_forward,
            ready=ready,
        )
        verdicts.append(PromotionGateVerdict(
            review_id=str(review.get("review_id") or ""),
            source_ref=source_ref,
            candidate_id=candidate_id,
            symbol=str(row.get("symbol") or ""),
            timeframe=str(row.get("timeframe") or ""),
            family=str(row.get("family") or ""),
            gate_stage=stage,
            reasons=reasons,
            evidence_refs={
                "shadow_uc_key": shadow_key,
                "true_forward_uc_key": true_key,
                "ready_strategy_id": str(ready.get("ready_strategy_id") or ""),
            },
        ))
    return verdicts


def summarize_gate(verdicts: Iterable[PromotionGateVerdict]) -> dict[str, Any]:
    items = list(verdicts)
    by_stage: dict[str, int] = {}
    for item in items:
        by_stage[item.gate_stage] = by_stage.get(item.gate_stage, 0) + 1
    return {
        "schema": SCHEMA,
        "verdicts": len(items),
        "by_stage": dict(sorted(by_stage.items())),
        "paper_only": True,
        "execution_allowed": False,
    }


def build_outcome_promotion_gate(private_root: Path) -> dict[str, Any]:
    private_root = Path(private_root)
    verdicts = build_gate_verdicts(
        load_training_rows(private_root),
        load_outcome_reviews(private_root),
        shadow_index=_registry_by_uc(private_root, "shadow_forward.json"),
        true_forward_index=_registry_by_uc(private_root, "true_forward.json"),
        ready_index=_items_by_candidate(_read_json(catalog_snapshot_path(private_root))),
    )
    return {
        **summarize_gate(verdicts),
        "items": [verdict.to_dict() for verdict in verdicts],
    }
