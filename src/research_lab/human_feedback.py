"""Manual review feedback for paper/research outputs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import append_jsonl, stable_id, utc_now

SCHEMA = "HumanFeedback.v1"
ALLOWED_LABELS = {
    "wrong_entry",
    "wrong_tf",
    "bad_card",
    "good_explanation",
    "chart_mismatch",
    "ignore_setup",
    "needs_more_context",
    "useful_signal",
}


@dataclass(frozen=True)
class HumanFeedback:
    feedback_id: str
    label: str
    source_surface: str
    target_id: str
    note: str = ""
    reviewer: str = "operator"
    scanner_event_id: str = ""
    data_packet_id: str = ""
    feature_packet_id: str = ""
    setup_candidate_id: str = ""
    validation_id: str = ""
    paper_signal_id: str = ""
    telegram_card_id: str = ""
    created_at: str = field(default_factory=utc_now)
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if self.label not in ALLOWED_LABELS:
            raise ValueError(f"unsupported feedback label: {self.label}")
        if self.execution_allowed:
            raise ValueError("feedback cannot allow execution")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def feedback_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "feedback" / "human_feedback.jsonl"


def create_feedback(label: str, source_surface: str, target_id: str, **extra: Any) -> HumanFeedback:
    payload = {"label": label, "surface": source_surface, "target_id": target_id, "extra": extra}
    return HumanFeedback(
        feedback_id=stable_id("feedback", payload),
        label=label,
        source_surface=source_surface,
        target_id=target_id,
        **extra,
    )


def record_feedback(private_root: Path, feedback: HumanFeedback) -> Path:
    return append_jsonl(feedback_path(private_root), feedback.to_dict())


def feedback_summary(private_root: Path) -> dict[str, Any]:
    path = feedback_path(private_root)
    by_label: dict[str, int] = {}
    rows = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows += 1
            try:
                label = str(json.loads(line).get("label") or "")
            except json.JSONDecodeError:
                label = "invalid_json"
            by_label[label] = by_label.get(label, 0) + 1
    return {
        "schema": "HumanFeedbackSummary.v1",
        "rows": rows,
        "by_label": by_label,
        "paper_only": True,
        "execution_allowed": False,
        "label": "strategy-lab/state/feedback/human_feedback.jsonl",
    }
