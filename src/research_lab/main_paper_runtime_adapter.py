"""Paper-only runtime queue for main-readable paper instructions.

This adapter is the safe boundary between the farm/PFR paper-watch lane and any
future main-paper runtime. It reads accepted consumer audit rows, rebuilds a
bounded watch queue, and writes private derived artifacts. It never imports the
old main engine, Telegram, exchange clients, credentials, or order execution.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.main_paper_consumer import SUMMARY_SCHEMA as CONSUMER_SCHEMA

SCHEMA = "MainPaperRuntimeQueueItem.v1"
SUMMARY_SCHEMA = "main_paper_runtime_adapter.v1"

FAMILY_PRIORITY = {
    "early_tp_tactical": 0,
    "mean_reversion_fade": 1,
    "reversal_fade": 1,
    "liquidity_sweep_reclaim": 2,
    "momentum_breakout": 3,
    "continuation": 4,
    "pullback_continuation": 4,
}

TIMEFRAME_PRIORITY = {
    "15m": 0,
    "1h": 1,
    "4h": 2,
    "1d": 3,
}


@dataclass(frozen=True)
class MainPaperRuntimeQueueItem:
    runtime_id: str
    consumer_id: str
    instruction_id: str
    source_signal_id: str
    pair: str
    okx_inst_id: str
    timeframe: str
    side: str
    setup_family: str
    entry: float
    stop: float
    take_profit_plan: list[dict[str, Any]]
    max_hold_min: int
    priority: int
    priority_reasons: list[str] = field(default_factory=list)
    runtime_action: str = "watch_paper"
    source_consumer_status: str = "accepted_for_paper_watch"
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError(f"schema must be {SCHEMA}")
        if self.runtime_action != "watch_paper":
            raise ValueError("runtime_action must be watch_paper")
        if self.execution_allowed:
            raise ValueError("runtime queue must never allow execution")
        if not self.paper_only:
            raise ValueError("runtime queue must be paper_only")
        if self.source_consumer_status != "accepted_for_paper_watch":
            raise ValueError("runtime queue accepts only accepted consumer rows")
        if self.entry <= 0 or self.stop <= 0:
            raise ValueError("entry and stop must be positive")
        if self.entry == self.stop:
            raise ValueError("entry and stop must differ")
        if self.max_hold_min <= 0:
            raise ValueError("max_hold_min must be positive")
        if not self.take_profit_plan:
            raise ValueError("take_profit_plan must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _consumer_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_consumed.json"


def _consumer_jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_consumed.jsonl"


def _jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_runtime_queue.jsonl"


def _snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_runtime_queue.json"


def _load_consumer_rows(private_root: Path) -> tuple[list[dict[str, Any]], Path | None]:
    snapshot = _consumer_snapshot_path(private_root)
    if snapshot.exists():
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        return list(items or []), snapshot

    jsonl = _consumer_jsonl_path(private_root)
    if not jsonl.exists():
        return [], None
    rows: list[dict[str, Any]] = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows, jsonl


def _priority(row: dict[str, Any], contract: dict[str, Any]) -> tuple[int, list[str]]:
    reasons: list[str] = []
    family = str(row.get("setup_family") or "")
    timeframe = str(row.get("timeframe") or "")
    score = FAMILY_PRIORITY.get(family, 9) * 100
    reasons.append(f"family={family or 'unknown'}:{FAMILY_PRIORITY.get(family, 9)}")
    score += TIMEFRAME_PRIORITY.get(timeframe, 9) * 10
    reasons.append(f"timeframe={timeframe or 'unknown'}:{TIMEFRAME_PRIORITY.get(timeframe, 9)}")
    entry = float(contract.get("entry") or 0)
    stop = float(contract.get("stop") or 0)
    if entry > 0 and stop > 0:
        risk_pct = abs(entry - stop) / entry * 100
        if risk_pct <= 3:
            score -= 2
            reasons.append("risk<=3pct:-2")
        elif risk_pct > 8:
            score += 50
            reasons.append("risk>8pct:+50")
    return score, reasons


def _item_from_row(row: dict[str, Any]) -> MainPaperRuntimeQueueItem | None:
    if row.get("consumer_status") != "accepted_for_paper_watch":
        return None
    if row.get("paper_only") is not True or row.get("execution_allowed") is not False:
        return None
    contract = dict(row.get("signal_contract") or {})
    meta = dict(contract.get("metadata") or {})
    if meta.get("paper_only") is not True or meta.get("execution_allowed") is not False:
        return None
    exit_rule = dict(contract.get("exit_rule") or {})
    exit_params = dict(exit_rule.get("params") or {})
    take_profit_plan = list(
        row.get("take_profit_plan")
        or meta.get("take_profit_plan")
        or exit_params.get("targets")
        or []
    )
    priority, priority_reasons = _priority(row, contract)
    return MainPaperRuntimeQueueItem(
        runtime_id=f"runtime_{row.get('consumer_id') or row.get('instruction_id')}",
        consumer_id=str(row.get("consumer_id") or ""),
        instruction_id=str(row.get("instruction_id") or ""),
        source_signal_id=str(row.get("source_signal_id") or ""),
        pair=str(row.get("pair") or contract.get("pair") or ""),
        okx_inst_id=str(row.get("okx_inst_id") or contract.get("pair") or ""),
        timeframe=str(row.get("timeframe") or ""),
        side=str(row.get("side") or contract.get("side") or ""),
        setup_family=str(row.get("setup_family") or ""),
        entry=float(contract.get("entry")),
        stop=float(contract.get("stop")),
        take_profit_plan=take_profit_plan,
        max_hold_min=int(contract.get("max_hold_min")),
        priority=priority,
        priority_reasons=priority_reasons,
    )


def build_main_paper_runtime_queue(private_root: Path, *, limit: int = 50) -> dict[str, Any]:
    rows, source_path = _load_consumer_rows(private_root)
    accepted_rows = [row for row in rows if row.get("consumer_status") == "accepted_for_paper_watch"]
    rejected_or_skipped = len(rows) - len(accepted_rows)

    items: list[MainPaperRuntimeQueueItem] = []
    invalid = 0
    for row in accepted_rows:
        try:
            item = _item_from_row(row)
            if item is None:
                rejected_or_skipped += 1
                continue
            items.append(item)
        except (TypeError, ValueError, KeyError):
            invalid += 1

    items.sort(key=lambda item: (item.priority, item.okx_inst_id, item.timeframe, item.source_signal_id))
    if limit >= 0:
        items = items[:limit]

    out_jsonl = _jsonl_path(private_root)
    out_snapshot = _snapshot_path(private_root)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    summary = {
        "schema": SUMMARY_SCHEMA,
        "source_schema": CONSUMER_SCHEMA,
        "source_path": str(source_path) if source_path else "",
        "source_exists": source_path is not None,
        "rows_read": len(rows),
        "accepted_rows": len(accepted_rows),
        "queued": len(items),
        "invalid": invalid,
        "rejected_or_skipped": rejected_or_skipped,
        "limit": limit,
        "paper_only": True,
        "execution_allowed": False,
        "runtime_action": "watch_paper",
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
    }
    out_snapshot.write_text(
        json.dumps({**summary, "items": [item.to_dict() for item in items]},
                   ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
