"""Compile bounded calculator advice into auditable sweep-proposal hints.

This bridge does not queue trades and does not change validator verdicts. It only
turns accepted CalculatorAdvice.v1 sweep suggestions into private research hints
that deterministic code can inspect later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import append_jsonl, stable_id, utc_now

SCHEMA = "AdvisorSweepProposal.v1"
ALLOWED_DIMENSIONS = {
    "entry_timing": ("entry", "earlier", "later", "late_entry", "entry_timing"),
    "stop": ("stop", "sl", "stop_loss"),
    "take_profit": ("take", "tp", "take_profit", "target"),
    "hold": ("hold", "max_hold", "duration"),
    "trailing": ("trail", "trailing", "be", "breakeven", "partial"),
    "timeframe": ("tf", "timeframe", "multi_tf"),
    "family": ("family", "setup_family"),
    "regime_filter": (
        "regime",
        "filter",
        "regime_filter",
        "rsi",
        "atr",
        "adx",
        "trend",
        "volatility",
        "volume",
        "liquidity",
        "funding",
        "oi",
        "spike",
        "confirmation",
    ),
}


@dataclass(frozen=True)
class AdvisorSweepProposal:
    proposal_id: str
    advisor_ref: str
    feature_packet_id: str
    dimension: str
    source_text: str
    status: str
    created_at: str
    schema: str = SCHEMA
    paper_only: bool = True
    execution_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "proposal_id": self.proposal_id,
            "advisor_ref": self.advisor_ref,
            "feature_packet_id": self.feature_packet_id,
            "dimension": self.dimension,
            "source_text": self.source_text,
            "status": self.status,
            "created_at": self.created_at,
            "paper_only": self.paper_only,
            "execution_allowed": self.execution_allowed,
        }


def proposal_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "calculator_sweep_proposals.jsonl"


def normalize_dimension(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("dimension") or value.get("name") or value.get("type")
    text = str(value or "").strip().lower()
    if not text:
        return None
    text = text.replace("-", "_").replace(" ", "_")
    for dimension, tokens in ALLOWED_DIMENSIONS.items():
        if text == dimension or any(token in text for token in tokens):
            return dimension
    return None


def compile_sweep_proposals(private_root: Path, advice: Any) -> dict[str, Any]:
    suggestions = []
    if getattr(advice, "accepted", False):
        raw = getattr(advice, "advice", {}) or {}
        if isinstance(raw.get("sweep_suggestions"), list):
            suggestions = raw["sweep_suggestions"]
    rows: list[dict[str, Any]] = []
    rejected: list[str] = []
    for item in suggestions:
        extracted = list(_iter_suggestion_dimensions(item))
        if not extracted:
            rejected.append(str(item or "unknown"))
            continue
        for dimension, source_text in extracted:
            payload = {
                "advisor_ref": advice.advisor_ref,
                "feature_packet_id": advice.feature_packet_id,
                "dimension": dimension,
                "source_text": source_text,
            }
            rows.append(
                AdvisorSweepProposal(
                    proposal_id=stable_id("asp", payload, length=20),
                    advisor_ref=advice.advisor_ref,
                    feature_packet_id=advice.feature_packet_id,
                    dimension=dimension,
                    source_text=source_text,
                    status="needs_deterministic_compile",
                    created_at=utc_now(),
                ).to_dict()
            )
    path = proposal_path(private_root)
    for row in rows:
        append_jsonl(path, row)
    summary = {
        "schema": "AdvisorSweepProposalSummary.v1",
        "rows": len(rows),
        "rejected": len(rejected),
        "rejected_reasons": {"llm_schema_reject": len(rejected)} if rejected else {},
        "proposal_label": "strategy-lab/state/derived/calculator_sweep_proposals.jsonl",
        "dimensions": _counts(row["dimension"] for row in rows),
        "paper_only": True,
        "execution_allowed": False,
    }
    snapshot = Path(private_root) / "state" / "derived" / "calculator_sweep_proposals.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps({**summary, "items": rows[-100:]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _iter_suggestion_dimensions(item: Any):
    if isinstance(item, dict):
        explicit = item.get("dimension") or item.get("name") or item.get("type")
        if explicit:
            dimension = normalize_dimension(explicit)
            source_text = str(explicit)
            if dimension and not _looks_like_numeric_trade_level(source_text):
                yield dimension, source_text
            return
        for key in item:
            dimension = normalize_dimension(key)
            if dimension:
                yield dimension, str(key)
        return

    source_text = str(item)
    dimension = normalize_dimension(source_text)
    if dimension and not _looks_like_numeric_trade_level(source_text):
        yield dimension, source_text


def _looks_like_numeric_trade_level(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not any(ch.isdigit() for ch in text):
        return False
    unsafe_tokens = (
        "set ",
        "entry",
        "stop",
        "sl",
        "take",
        "tp",
        "target",
        "price",
        "leverage",
        "size",
    )
    return any(token in text for token in unsafe_tokens)


def _counts(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = str(value or "")
        if key:
            out[key] = out.get(key, 0) + 1
    return out
