# -*- coding: utf-8 -*-
"""Generate deterministic failure feedback from hard validation reports.

Reads HardValidationReport, produces FailureFeedback objects with
suggested next tests, blocked parameter regions, and required data.
All deterministic — no LLM calls.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from src.research_lab.hard_validation_contract import (
    FailureFeedback,
    HardValidationReport,
)

FEEDBACK_DIR = "hard_validation/feedback"

STATUS_TO_FEEDBACK: dict[str, dict[str, Any]] = {
    "FAILED_COSTS": {
        "suggested": [
            "wider_move_threshold",
            "lower_turnover_strategy",
            "increase_min_hold_bars",
        ],
        "blocked": [{"fee_sensitive": True}],
        "required_data": [],
        "priority": "normal",
    },
    "FAILED_OOS": {
        "suggested": [
            "different_regime_split",
            "walk_forward_with_larger_window",
            "archive_and_collect_more_data",
        ],
        "blocked": [],
        "required_data": ["more_oos_bars"],
        "priority": "normal",
    },
    "FAILED_FRAGILITY": {
        "suggested": [
            "wider_parameter_neighborhood",
            "reject_isolated_spike",
            "test_parameter_plateau",
        ],
        "blocked": [],
        "required_data": [],
        "priority": "normal",
    },
    "FAILED_OVERFIT": {
        "suggested": [
            "reduce_search_space",
            "test_with_fewer_variants",
            "collect_longer_track_record",
        ],
        "blocked": [],
        "required_data": ["longer_history"],
        "priority": "high",
    },
    "FAILED_DATA_QUALITY": {
        "suggested": [
            "verify_source_data",
            "check_for_gaps",
            "rebuild_from_raw_candles",
        ],
        "blocked": [],
        "required_data": ["verified_candles"],
        "priority": "high",
    },
    "REGIME_ONLY": {
        "suggested": [
            "rerun_with_explicit_regime_filter",
            "test_across_multiple_regimes",
        ],
        "blocked": [],
        "required_data": [],
        "priority": "normal",
    },
    "NEEDS_MORE_DATA": {
        "suggested": [
            "collect_more_history",
            "wait_for_forward_period",
        ],
        "blocked": [],
        "required_data": ["more_bars"],
        "priority": "low",
    },
    "HARD_REJECT": {
        "suggested": ["archive_and_forget"],
        "blocked": [],
        "required_data": [],
        "priority": "low",
    },
    "PAPER_FORWARD_READY": {
        "suggested": ["track_in_forward_log"],
        "blocked": [],
        "required_data": [],
        "priority": "normal",
    },
}


def generate_feedback(report: HardValidationReport) -> FailureFeedback | None:
    """Generate a FailureFeedback from a validation report.

    Returns None if the verdict is PAPER_FORWARD_READY (no failure feedback
    needed — it passed).
    """
    verdict = report.verdict
    hard_status = str(verdict.get("hard_status") or "")
    if hard_status == "PAPER_FORWARD_READY":
        return None

    template = STATUS_TO_FEEDBACK.get(hard_status, STATUS_TO_FEEDBACK["HARD_REJECT"])
    failed_checks = list(verdict.get("failed_checks") or [])
    reason_codes = list(verdict.get("reason_codes") or [])

    constraints = list(template["suggested"])
    if "costs" in failed_checks:
        constraints.append("net_after_costs_must_be_positive")
    if "oos_split" in failed_checks:
        constraints.append("test_period_mean_must_be_positive")

    blocked = list(template["blocked"])
    if "robustness" in failed_checks:
        checks = verdict.get("checks") or []
        for c in checks:
            if c.get("check_name") == "robustness":
                details = c.get("details") or {}
                n_pos = details.get("positive_periods", 0)
                n_total = details.get("n_periods", 4)
                if n_pos < n_total * 0.5:
                    blocked.append({"fragile_regime": True})
                break

    return FailureFeedback(
        candidate_id=report.candidate_id,
        symbol=report.symbol,
        timeframe=report.timeframe,
        strategy_id=report.strategy_id,
        hard_status=hard_status,
        failed_checks=failed_checks,
        reason_codes=reason_codes,
        suggested_next_test_constraints=constraints,
        blocked_parameter_regions=blocked,
        required_data=list(template["required_data"]),
        priority=template["priority"],
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    )


def write_feedback(
    private_root: Path,
    feedback: FailureFeedback,
    *,
    dry_run: bool = True,
) -> Path | None:
    """Append a feedback entry to the JSONL queue."""
    if dry_run:
        return None
    feedback_dir = private_root / FEEDBACK_DIR
    feedback_dir.mkdir(parents=True, exist_ok=True)
    path = feedback_dir / "feedback.jsonl"
    rows = {
        (str(row.get("candidate_id") or ""), str(row.get("hard_status") or "")): row
        for row in load_feedback_queue(private_root)
    }
    rows[(feedback.candidate_id, feedback.hard_status)] = feedback.to_dict()
    ordered = [rows[k] for k in sorted(rows)]
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in ordered
        ),
        encoding="utf-8",
    )
    return path


def load_feedback_queue(private_root: Path) -> list[dict[str, Any]]:
    """Read all feedback entries from the queue."""
    path = private_root / FEEDBACK_DIR / "feedback.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            import json
            entries.append(json.loads(line))
        except Exception:
            continue
    return entries
