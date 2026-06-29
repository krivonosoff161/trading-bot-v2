"""Validator outcome taxonomy for dashboard/status summaries.

The hard validator keeps its existing statuses. This module only maps existing
research memory labels into the product-level reason classes requested by the
paper/research pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA = "ValidatorTaxonomy.v1"

OUTCOME_TO_CLASS = {
    "CONFIRMED_BAD": "confirmed_bad",
    "WRONG_EXIT": "wrong_exit",
    "EXIT_RECOVERED": "wrong_exit",
    "WRONG_TIMEFRAME": "data_issue",
    "NEEDS_OI_DATA": "data_issue",
    "INSUFFICIENT_DATA": "underpowered",
    "TACTICAL_1_2_TRADE": "tactical_candidate",
    "THIN_BUT_PROMISING": "underpowered",
    "STATISTICAL_CANDIDATE": "underpowered",
    "POSITIVE_VALIDATED": "forward_watch_candidate",
    "COST_SENSITIVE": "cost_sensitive",
    "UNCHARACTERIZED": "manual_review_required",
}

VALIDATOR_STATUS_TO_CLASS = {
    "REJECT": "validator_reject",
    "OBSERVE": "underpowered",
    "REGIME_SPECIFIC": "regime_only",
    "FORWARD_PAPER": "forward_watch_candidate",
}

REQUIRED_CLASSES = (
    "confirmed_bad",
    "wrong_exit",
    "no_event",
    "underpowered",
    "tactical_candidate",
    "regime_only",
    "data_issue",
    "cost_sensitive",
    "forward_watch_candidate",
    "manual_review_required",
    "validator_reject",
)


def classify_record(record: dict[str, Any]) -> str:
    outcome_class = str(record.get("outcome_class") or "")
    if outcome_class in OUTCOME_TO_CLASS:
        return OUTCOME_TO_CLASS[outcome_class]
    status = str(record.get("lite_status") or record.get("validation_status") or "")
    return VALIDATOR_STATUS_TO_CLASS.get(status, "manual_review_required")


def taxonomy_summary(private_root: Path) -> dict[str, Any]:
    records = _load_memory_records(private_root)
    by_class = {key: 0 for key in REQUIRED_CLASSES}
    for record in records:
        key = classify_record(record)
        by_class[key] = by_class.get(key, 0) + 1
    return {
        "schema": SCHEMA,
        "rows": len(records),
        "by_class": by_class,
        "source_label": "strategy-lab/state/derived/setup_outcome_memory.json",
        "hard_gates_unchanged": True,
        "tactical_route_separate": True,
        "paper_only": True,
        "execution_allowed": False,
    }


def _load_memory_records(private_root: Path) -> list[dict[str, Any]]:
    path = Path(private_root) / "state" / "derived" / "setup_outcome_memory.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [row for row in (data.get("items") or data.get("records") or []) if isinstance(row, dict)]
