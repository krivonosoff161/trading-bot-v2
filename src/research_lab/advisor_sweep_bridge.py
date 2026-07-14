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
from src.research_lab.outcome_retest import paper_to_executable_family
from src.research_lab.strategy_registry import REGISTRY

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


def proposal_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "calculator_sweep_proposals.json"


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
    snapshot = proposal_snapshot_path(private_root)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps({**summary, "items": rows[-100:]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def schedule_advisor_sweep_tasks(private_root: Path, tasks: Any, *, limit: int = 20, now: float | None = None) -> dict[str, Any]:
    """Turn accepted advisor proposals into scheduler tasks backed by active paper signals.

    The advisor only proposes dimensions. This scheduler does not carry numeric LLM
    levels forward; it finds the matching active paper signal and asks the
    coordinator to build a normal bounded SweepSpec for that signal's family.
    """
    proposals = _load_recent_proposals(private_root, limit=limit)
    feature_index = _feature_index(private_root)
    active_signals = _active_paper_signals(private_root)
    scheduled = 0
    deduped = 0
    skipped: dict[str, int] = {}
    for proposal in proposals:
        if not bool(proposal.get("paper_only", True)) or bool(proposal.get("execution_allowed", False)):
            _inc(skipped, "unsafe_proposal")
            continue
        feature_packet_id = str(proposal.get("feature_packet_id") or "")
        feature = feature_index.get(feature_packet_id)
        if not feature:
            _inc(skipped, "missing_feature_packet")
            continue
        signal = _match_active_signal(active_signals, feature_packet_id, feature)
        if not signal:
            _inc(skipped, "missing_active_signal")
            continue
        executable_family = paper_to_executable_family(str(signal.get("setup_family") or ""))
        if executable_family not in REGISTRY:
            _inc(skipped, "unsupported_executable_family")
            continue
        proposal_id = str(proposal.get("proposal_id") or "")
        signal_id = str(signal.get("signal_id") or "")
        key = f"advisor_sweep::{proposal_id}::{signal_id}"
        payload = {
            "origin": "calculator_advisor",
            "proposal": {
                "proposal_id": proposal_id,
                "advisor_ref": str(proposal.get("advisor_ref") or ""),
                "feature_packet_id": feature_packet_id,
                "dimension": str(proposal.get("dimension") or ""),
                "source_text": str(proposal.get("source_text") or ""),
            },
            "feature_packet": {
                "feature_packet_id": feature_packet_id,
                "symbol": str(feature.get("symbol") or ""),
                "instrument": str(feature.get("instrument") or ""),
                "timeframe": str(feature.get("timeframe") or ""),
            },
            "source_signal": {
                "signal_id": signal_id,
                "source": str(signal.get("source") or ""),
                "setup_family": str(signal.get("setup_family") or ""),
                "executable_family": executable_family,
                "data_fingerprint": str(signal.get("data_fingerprint") or ""),
                "status": str(signal.get("status") or ""),
            },
            "paper_only": True,
            "execution_allowed": False,
        }
        _, created = tasks.enqueue_task(
            task_type="schedule_advisor_sweep",
            task_key=key,
            priority=65,
            symbol=str(signal.get("symbol") or feature.get("symbol") or ""),
            timeframe=str(signal.get("timeframe") or feature.get("timeframe") or ""),
            family=str(signal.get("setup_family") or ""),
            source_event_id=proposal_id,
            payload=payload,
            machine_reason="calculator_advisor_dimension",
            now=now,
        )
        if created:
            scheduled += 1
        else:
            deduped += 1
    return {
        "schema": "AdvisorSweepTaskSchedule.v1",
        "loaded": len(proposals),
        "scheduled": scheduled,
        "deduped": deduped,
        "skipped": skipped,
        "paper_only": True,
        "execution_allowed": False,
    }


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


def _load_recent_proposals(private_root: Path, *, limit: int) -> list[dict[str, Any]]:
    path = proposal_path(private_root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)) * 3:]:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("schema") == SCHEMA:
            rows.append(row)
    return rows[-max(1, int(limit)):]


def _feature_index(private_root: Path) -> dict[str, dict[str, Any]]:
    from src.research_lab.feature_packet import packet_index_path
    index = packet_index_path(private_root)
    out: dict[str, dict[str, Any]] = {}
    if not index.exists():
        return out
    for line in index.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        feature_packet_id = str(row.get("feature_packet_id") or "")
        if feature_packet_id:
            out[feature_packet_id] = row
    return out


def _active_paper_signals(private_root: Path) -> list[dict[str, Any]]:
    path = Path(private_root) / "state" / "derived" / "paper_signals.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    active = data.get("active") if isinstance(data, dict) else []
    return [row for row in active if isinstance(row, dict)]


def _match_active_signal(
    signals: list[dict[str, Any]],
    feature_packet_id: str,
    feature: dict[str, Any],
) -> dict[str, Any] | None:
    symbol = str(feature.get("symbol") or "")
    timeframe = str(feature.get("timeframe") or "")
    exact = [
        row for row in signals
        if str(row.get("feature_packet_id") or "") == feature_packet_id
        and str(row.get("setup_family") or "")
    ]
    if exact:
        return _best_signal(exact)
    loose = [
        row for row in signals
        if str(row.get("symbol") or "") == symbol
        and str(row.get("timeframe") or "") == timeframe
        and str(row.get("setup_family") or "")
    ]
    return _best_signal(loose) if loose else None


def _best_signal(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def key(row: dict[str, Any]) -> tuple[int, float]:
        source_rank = 0 if str(row.get("source") or "") == "pfr_farm" else 1
        try:
            created = float(row.get("created_at") or 0.0)
        except (TypeError, ValueError):
            created = 0.0
        return source_rank, -created

    return sorted(rows, key=key)[0]


def _inc(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


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
