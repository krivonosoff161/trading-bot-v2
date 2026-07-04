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
from src.research_lab.trade_math import midpoint
from src.strategy.signal_contract import ExitRule, FollowRule, SignalContract

SCHEMA = "MainPaperInstruction.v1"
ACTIVE_STATUSES = ("armed", "opened_paper")
MAIN_READY_VERDICT = "PAPER_FORWARD_READY"


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
    trigger = str(sig.validator_context.get("entry_trigger") or "limit_pullback")
    if trigger == "breakout_stop" and len(sig.entry_zone) == 2:
        return float(sig.entry_zone[1] if sig.side == "long" else sig.entry_zone[0])
    return midpoint(sig.entry_zone)


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
            "boundary_ts": sig.boundary_ts,
            "created_at": sig.created_at,
            "expires_at": sig.expires_at,
            "max_hold_bars": sig.max_hold_bars,
            "data_fingerprint": sig.data_fingerprint,
            "scanner_event_id": sig.scanner_event_id,
            "data_packet_id": sig.data_packet_id,
            "feature_packet_id": sig.feature_packet_id,
            "setup_candidate_id": sig.setup_candidate_id,
            "sweep_run_id": sig.sweep_run_id,
            "validation_id": sig.validation_id,
            "llm_interpretation_ref": sig.llm_interpretation_ref,
            "ready_strategy_id": str(sig.validator_context.get("ready_strategy_id") or ""),
            "setup_id": str(sig.validator_context.get("setup_id") or ""),
            "candidate_id": str(sig.validator_context.get("candidate_id") or ""),
            "source_validation_verdict": str(sig.validator_context.get("source_validation_verdict") or ""),
            "entry_trigger": str(sig.validator_context.get("entry_trigger") or "limit_pullback"),
            "pretrigger": bool(sig.validator_context.get("pretrigger")),
            "trigger_gap_pct": sig.validator_context.get("trigger_gap_pct"),
            "dedup_key": sig.dedup_key,
            "mode": sig.mode,
            "exit_mode": sig.exit_mode,
            "reason_now": sig.reason_now,
            "execution_allowed": False,
            "paper_only": True,
        },
    )


def instruction_from_signal(sig: PaperActionSignal) -> MainPaperInstruction | None:
    if sig.status not in ACTIVE_STATUSES:
        return None
    if not is_main_ready_signal(sig):
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


def is_main_ready_signal(sig: PaperActionSignal) -> bool:
    """Return True only for validator-backed signals allowed into main-paper runtime.

    The paper signal lane may create broad research/watch cards. The main-paper runtime
    is stricter: it receives only candidates that came through the hard validator/PFR
    catalog and carry a stable ready strategy identity. This keeps farm exploration out
    of operator/subscriber cards until validation promoted the setup to forward-watch.
    """
    context = sig.validator_context or {}
    ready_strategy_id = str(context.get("ready_strategy_id") or "").strip()
    verdict = str(context.get("source_validation_verdict") or "").strip()
    return bool(ready_strategy_id) and verdict == MAIN_READY_VERDICT


def _jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_instructions.jsonl"


def _snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_instructions.json"


def export_main_paper_instructions(private_root: Path) -> dict[str, Any]:
    signals = load_signals(private_root)
    active = [sig for sig in signals if sig.status in ACTIVE_STATUSES]
    instructions = [item for sig in active if (item := instruction_from_signal(sig)) is not None]
    skipped_unvalidated = len(active) - len(instructions)
    skip_reasons: dict[str, int] = {}
    skipped_examples: list[dict[str, Any]] = []
    for sig in active:
        if instruction_from_signal(sig) is not None:
            continue
        context = sig.validator_context or {}
        ready_strategy_id = str(context.get("ready_strategy_id") or "").strip()
        verdict = str(context.get("source_validation_verdict") or "").strip()
        if not ready_strategy_id:
            reason = "missing_ready_strategy_id"
        elif verdict != MAIN_READY_VERDICT:
            reason = f"verdict_not_{MAIN_READY_VERDICT}"
        else:
            reason = "not_main_ready"
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
        if len(skipped_examples) < 5:
            skipped_examples.append({
                "signal_id": sig.signal_id,
                "symbol": sig.symbol,
                "timeframe": sig.timeframe,
                "family": sig.setup_family,
                "source": sig.source,
                "status": sig.status,
                "reason": reason,
            })
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
        "active_source_signals": len(active),
        "skipped_unvalidated": skipped_unvalidated,
        "skip_reasons": skip_reasons,
        "skipped_examples": skipped_examples,
        "required_validator_verdict": MAIN_READY_VERDICT,
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
