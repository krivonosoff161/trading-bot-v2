"""Paper-only bridge from farm/PFR paper-watch signals to the main signal contract.

This module creates a rebuildable, main-readable instruction view. It does not import
the old WS main runtime, Telegram, auto-execution, exchange clients, or credentials.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import load_signals
from src.strategy.signal_contract import ExitRule, FollowRule, SignalContract

SCHEMA = "MainPaperInstruction.v1"
ACTIVE_STATUSES = ("armed", "opened_paper")


@dataclass(frozen=True)
class MainPaperInstruction:
    instruction_id: str
    source_signal_id: str
    pair: str
    okx_inst_id: str
    timeframe: str
    side: str
    entry: float
    stop: float
    take_profit_plan: list[dict[str, Any]]
    max_hold_min: int
    setup_family: str
    source_status: str
    signal_contract: dict[str, Any]
    validator_context: dict[str, Any] = field(default_factory=dict)
    execution_allowed: bool = False
    paper_only: bool = True
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError(f"schema must be {SCHEMA}")
        if self.execution_allowed:
            raise ValueError("main paper instructions must never allow execution")
        if not self.paper_only:
            raise ValueError("main paper instructions must be paper_only")
        if self.source_status not in ACTIVE_STATUSES:
            raise ValueError(f"source status must be active, got {self.source_status!r}")
        if self.entry <= 0 or self.stop <= 0:
            raise ValueError("entry and stop must be positive")
        if not self.take_profit_plan:
            raise ValueError("take_profit_plan required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _iso_from_epoch(ts: float) -> str:
    if ts <= 0:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _entry_midpoint(sig: PaperActionSignal) -> float:
    lo, hi = sig.entry_zone
    return round((float(lo) + float(hi)) / 2.0, 10)


def _contract_from_signal(sig: PaperActionSignal, entry: float) -> SignalContract:
    targets = [
        {
            "label": str(tp.get("label", "tp")),
            "price": float(tp["price"]),
            "size_frac": float(tp.get("size_frac", 1.0)),
        }
        for tp in sig.take_profit_plan
    ]
    exit_rule = ExitRule(
        type="scaled" if len(targets) > 1 else "fade",
        params={
            "targets": targets,
            "exit_mode": sig.exit_mode,
            "invalidation_rule": sig.invalidation_rule,
        },
    )
    follow = FollowRule(be_at_R=1.0 if sig.exit_mode == "partial_be" else None)
    regime = str(sig.validator_context.get("regime") or sig.setup_family or "paper_watch")
    return SignalContract(
        pair=sig.okx_inst_id,
        side=sig.side,
        entry=entry,
        stop=float(sig.stop_loss),
        exit_rule=exit_rule,
        max_hold_min=int(sig.max_hold_minutes),
        follow=follow,
        regime=regime,
        analyzer_id=f"paper_signals.{sig.setup_family}",
        snapshot_id=sig.data_fingerprint or sig.signal_id,
        ts=_iso_from_epoch(sig.created_at),
        metadata={
            "source": sig.source,
            "source_signal_id": sig.signal_id,
            "timeframe": sig.timeframe,
            "entry_zone": list(sig.entry_zone),
            "risk_pct": sig.risk_pct,
            "reason_now": sig.reason_now,
            "execution_allowed": False,
            "paper_only": True,
        },
    )


def instruction_from_signal(sig: PaperActionSignal) -> MainPaperInstruction | None:
    if sig.status not in ACTIVE_STATUSES:
        return None
    entry = _entry_midpoint(sig)
    contract = _contract_from_signal(sig, entry)
    return MainPaperInstruction(
        instruction_id=f"mainpaper_{sig.signal_id}",
        source_signal_id=sig.signal_id,
        pair=sig.symbol,
        okx_inst_id=sig.okx_inst_id,
        timeframe=sig.timeframe,
        side=sig.side,
        entry=entry,
        stop=float(sig.stop_loss),
        take_profit_plan=list(sig.take_profit_plan),
        max_hold_min=int(sig.max_hold_minutes),
        setup_family=sig.setup_family,
        source_status=sig.status,
        signal_contract=contract.to_dict(),
        validator_context=dict(sig.validator_context),
    )


def _jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_instructions.jsonl"


def _snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_instructions.json"


def export_main_paper_instructions(private_root: Path) -> dict[str, Any]:
    signals = load_signals(private_root)
    instructions = [item for sig in signals if (item := instruction_from_signal(sig)) is not None]
    out_jsonl = _jsonl_path(private_root)
    out_snapshot = _snapshot_path(private_root)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for item in instructions:
            fh.write(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    summary = {
        "schema": "main_paper_bridge.v1",
        "source_schema": "paper_signals.v1",
        "instructions": len(instructions),
        "active_source_statuses": list(ACTIVE_STATUSES),
        "execution_allowed": False,
        "paper_only": True,
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
    }
    out_snapshot.write_text(
        json.dumps({**summary, "items": [item.to_dict() for item in instructions]},
                   ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
