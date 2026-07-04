"""Paper-product trade ledger for broad farm paper candidates.

This ledger is separate from ``main_paper_trade_ledger``.  The main-paper
ledger stays strict and live-ready-shaped; this one gives the subscriber-facing
paper product a trade-like lifecycle for every visible paper candidate without
granting execution authority.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import stable_id, utc_now
from src.research_lab.main_paper_bridge import MAIN_READY_VERDICT
from src.research_lab.paper_signals import store
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.trade_math import midpoint

SCHEMA = "PaperProductTrade.v1"
SUMMARY_SCHEMA = "paper_product_trade_ledger.v1"
PRODUCT_STATUSES = {"armed", "opened_paper", "closed_paper", "expired", "invalidated", "reviewed"}


@dataclass(frozen=True)
class PaperProductTrade:
    paper_trade_id: str
    paper_product_trade_id: str
    source_signal_id: str
    ready_strategy_id: str
    source_validation_verdict: str
    live_ready: bool
    live_block_reason: str
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
    signal_status: str
    source: str
    outcome: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    reason_now: str = ""
    risk_pct: float = 0.0
    created_at: str = field(default_factory=utc_now)
    product_lane: str = "paper_product_candidate"
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError(f"schema must be {SCHEMA}")
        if self.execution_allowed:
            raise ValueError("paper product ledger must never allow execution")
        if not self.paper_only:
            raise ValueError("paper product ledger must be paper_only")
        if self.status not in PRODUCT_STATUSES:
            raise ValueError(f"unsupported product status {self.status!r}")
        if self.entry <= 0 or self.stop <= 0:
            raise ValueError("entry and stop must be positive")
        if not self.source_signal_id:
            raise ValueError("source_signal_id required")
        if self.live_ready and self.live_block_reason:
            raise ValueError("live-ready rows must not carry a live block reason")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_product_trades.jsonl"


def _snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_product_trades.json"


def _readiness(sig: PaperActionSignal) -> tuple[bool, str, str, str]:
    context = sig.validator_context or {}
    ready_strategy_id = str(context.get("ready_strategy_id") or "").strip()
    verdict = str(context.get("source_validation_verdict") or "").strip()
    if ready_strategy_id and verdict == MAIN_READY_VERDICT:
        return True, "", ready_strategy_id, verdict
    if not ready_strategy_id:
        return False, "missing_ready_strategy_id", ready_strategy_id, verdict
    if verdict != MAIN_READY_VERDICT:
        return False, f"verdict_not_{MAIN_READY_VERDICT}", ready_strategy_id, verdict
    return False, "not_live_ready", ready_strategy_id, verdict


def _trade_from_signal(sig: PaperActionSignal) -> PaperProductTrade:
    live_ready, block_reason, ready_strategy_id, verdict = _readiness(sig)
    trade_id = stable_id(
        "paperproducttrade",
        {
            "signal_id": sig.signal_id,
            "data_fingerprint": sig.data_fingerprint,
            "dedup_key": sig.dedup_key,
        },
        length=20,
    )
    return PaperProductTrade(
        paper_trade_id=trade_id,
        paper_product_trade_id=trade_id,
        source_signal_id=sig.signal_id,
        ready_strategy_id=ready_strategy_id,
        source_validation_verdict=verdict,
        live_ready=live_ready,
        live_block_reason=block_reason,
        okx_inst_id=sig.okx_inst_id,
        timeframe=sig.timeframe,
        side=sig.side,
        setup_family=sig.setup_family,
        entry=midpoint(sig.entry_zone),
        entry_zone=list(sig.entry_zone),
        stop=float(sig.stop_loss),
        take_profit_plan=list(sig.take_profit_plan),
        max_hold_min=int(sig.max_hold_minutes),
        max_hold_bars=int(sig.max_hold_bars),
        status=sig.status,
        signal_status=sig.status,
        source=sig.source,
        outcome=dict(sig.outcome or {}),
        review=dict(sig.review or {}),
        reason_now=sig.reason_now,
        risk_pct=float(sig.risk_pct or 0.0),
    )


def build_paper_product_trade_ledger(private_root: Path) -> dict[str, Any]:
    private_root = Path(private_root)
    signals = [sig for sig in store.load_signals(private_root) if sig.status in PRODUCT_STATUSES]
    trades: list[PaperProductTrade] = []
    invalid = 0
    invalid_reasons: dict[str, int] = {}
    for sig in signals:
        try:
            trades.append(_trade_from_signal(sig))
        except (TypeError, ValueError, KeyError) as exc:
            invalid += 1
            reason = str(exc) or type(exc).__name__
            invalid_reasons[reason] = invalid_reasons.get(reason, 0) + 1

    by_status: dict[str, int] = {}
    by_family: dict[str, int] = {}
    by_live_block: dict[str, int] = {}
    for trade in trades:
        by_status[trade.status] = by_status.get(trade.status, 0) + 1
        by_family[trade.setup_family] = by_family.get(trade.setup_family, 0) + 1
        if trade.live_block_reason:
            by_live_block[trade.live_block_reason] = by_live_block.get(trade.live_block_reason, 0) + 1

    out_jsonl = _jsonl_path(private_root)
    out_snapshot = _snapshot_path(private_root)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for trade in trades:
            fh.write(json.dumps(trade.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    summary = {
        "schema": SUMMARY_SCHEMA,
        "row_schema": SCHEMA,
        "source_schema": "paper_signals.v1",
        "source_rows": len(signals),
        "trades": len(trades),
        "live_ready": sum(1 for trade in trades if trade.live_ready),
        "live_blocked": sum(1 for trade in trades if not trade.live_ready),
        "invalid": invalid,
        "invalid_reasons": invalid_reasons,
        "by_status": by_status,
        "by_family": by_family,
        "by_live_block": by_live_block,
        "paper_only": True,
        "execution_allowed": False,
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
        "items": [trade.to_dict() for trade in trades],
    }
    out_snapshot.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
