"""Paper-trade ledger derived from the safe main-paper runtime.

The runtime observer answers "what happened to this queued idea?".  This module
turns that into a trade-like ledger for analysis/training: one row per paper
trade lifecycle, with entry/stop/targets, validation lineage, current status,
outcome and review.  It never imports the legacy live main engine, Telegram,
exchange clients, credentials, or order execution.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import stable_id, utc_now

SCHEMA = "MainPaperTrade.v1"
SUMMARY_SCHEMA = "main_paper_trade_ledger.v1"


@dataclass(frozen=True)
class MainPaperTrade:
    paper_trade_id: str
    runtime_id: str
    instruction_id: str
    source_signal_id: str
    ready_strategy_id: str
    source_validation_verdict: str
    okx_inst_id: str
    timeframe: str
    side: str
    setup_family: str
    entry: float
    entry_zone: list[float]
    stop: float
    take_profit_plan: list[dict[str, Any]]
    max_hold_min: int
    max_hold_bars: int
    status: str
    signal_status: str = ""
    outcome: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    adaptive_policy_id: str = ""
    adaptive_execution_profile: str = ""
    adaptive_entry_profile: str = ""
    adaptive_exit_profile: str = ""
    adaptive_stop_profile: str = ""
    adaptive_max_hold_profile: str = ""
    adaptive_regime_hint: str = ""
    adaptive_policy_confidence: float = 0.0
    adaptive_policy_reasons: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError(f"schema must be {SCHEMA}")
        if self.execution_allowed:
            raise ValueError("paper trade ledger must never allow execution")
        if not self.paper_only:
            raise ValueError("paper trade ledger must be paper_only")
        if not self.ready_strategy_id:
            raise ValueError("paper trade requires ready_strategy_id")
        if self.source_validation_verdict != "PAPER_FORWARD_READY":
            raise ValueError("paper trade requires PAPER_FORWARD_READY source verdict")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _queue_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_runtime_queue.json"


def _observation_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_runtime_observation.json"


def _jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_trades.jsonl"


def _snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_trades.json"


def _load_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("items") or [])


def _ledger_status(queue_item: dict[str, Any], observed: dict[str, Any] | None) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    if observed is None:
        return "queued", "", {}, {}
    status = str(observed.get("status") or "")
    signal_status = str(observed.get("signal_status") or "")
    outcome = dict(observed.get("outcome") or {})
    review = dict(observed.get("review") or {})
    if status in {"invalid", "provider_error", "no_data", "pending_clock"}:
        return status, signal_status, outcome, review
    if signal_status == "opened_paper":
        return "opened_paper", signal_status, outcome, review
    if signal_status in {"closed_paper", "expired", "reviewed"}:
        result = str(outcome.get("result") or signal_status)
        return f"closed_{result}", signal_status, outcome, review
    if signal_status == "armed":
        return "armed", signal_status, outcome, review
    return status or "observed", signal_status, outcome, review


def _trade_from_queue(queue_item: dict[str, Any], observed: dict[str, Any] | None) -> MainPaperTrade:
    status, signal_status, outcome, review = _ledger_status(queue_item, observed)
    paper_trade_id = stable_id(
        "papertrade",
        {
            "runtime_id": queue_item.get("runtime_id"),
            "ready_strategy_id": queue_item.get("ready_strategy_id"),
            "source_signal_id": queue_item.get("source_signal_id"),
        },
        length=20,
    )
    return MainPaperTrade(
        paper_trade_id=paper_trade_id,
        runtime_id=str(queue_item.get("runtime_id") or ""),
        instruction_id=str(queue_item.get("instruction_id") or ""),
        source_signal_id=str(queue_item.get("source_signal_id") or ""),
        ready_strategy_id=str(queue_item.get("ready_strategy_id") or ""),
        source_validation_verdict=str(queue_item.get("source_validation_verdict") or ""),
        okx_inst_id=str(queue_item.get("okx_inst_id") or ""),
        timeframe=str(queue_item.get("timeframe") or ""),
        side=str(queue_item.get("side") or ""),
        setup_family=str(queue_item.get("setup_family") or ""),
        entry=float(queue_item.get("entry") or 0.0),
        entry_zone=[float(v) for v in list(queue_item.get("entry_zone") or [])[:2]],
        stop=float(queue_item.get("stop") or 0.0),
        take_profit_plan=list(queue_item.get("take_profit_plan") or []),
        max_hold_min=int(queue_item.get("max_hold_min") or 0),
        max_hold_bars=int(queue_item.get("max_hold_bars") or 0),
        status=status,
        signal_status=signal_status,
        outcome=outcome,
        review=review,
        adaptive_policy_id=str(queue_item.get("adaptive_policy_id") or ""),
        adaptive_execution_profile=str(queue_item.get("adaptive_execution_profile") or ""),
        adaptive_entry_profile=str(queue_item.get("adaptive_entry_profile") or ""),
        adaptive_exit_profile=str(queue_item.get("adaptive_exit_profile") or ""),
        adaptive_stop_profile=str(queue_item.get("adaptive_stop_profile") or ""),
        adaptive_max_hold_profile=str(queue_item.get("adaptive_max_hold_profile") or ""),
        adaptive_regime_hint=str(queue_item.get("adaptive_regime_hint") or ""),
        adaptive_policy_confidence=float(queue_item.get("adaptive_policy_confidence") or 0.0),
        adaptive_policy_reasons=list(queue_item.get("adaptive_policy_reasons") or []),
    )


def build_main_paper_trade_ledger(private_root: Path) -> dict[str, Any]:
    private_root = Path(private_root)
    queue_items = _load_items(_queue_snapshot_path(private_root))
    observation_items = _load_items(_observation_snapshot_path(private_root))
    observations = {str(item.get("runtime_id") or ""): item for item in observation_items}

    trades: list[MainPaperTrade] = []
    invalid = 0
    invalid_reasons: dict[str, int] = {}
    for item in queue_items:
        try:
            trades.append(_trade_from_queue(item, observations.get(str(item.get("runtime_id") or ""))))
        except (TypeError, ValueError, KeyError) as exc:
            invalid += 1
            reason = str(exc) or type(exc).__name__
            invalid_reasons[reason] = invalid_reasons.get(reason, 0) + 1

    out_jsonl = _jsonl_path(private_root)
    out_snapshot = _snapshot_path(private_root)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for trade in trades:
            fh.write(json.dumps(trade.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    by_status: dict[str, int] = {}
    by_family: dict[str, int] = {}
    for trade in trades:
        by_status[trade.status] = by_status.get(trade.status, 0) + 1
        by_family[trade.setup_family] = by_family.get(trade.setup_family, 0) + 1

    summary = {
        "schema": SUMMARY_SCHEMA,
        "row_schema": SCHEMA,
        "queue_rows": len(queue_items),
        "observation_rows": len(observation_items),
        "trades": len(trades),
        "invalid": invalid,
        "invalid_reasons": invalid_reasons,
        "by_status": by_status,
        "by_family": by_family,
        "paper_only": True,
        "execution_allowed": False,
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
        "items": [trade.to_dict() for trade in trades],
    }
    out_snapshot.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary

