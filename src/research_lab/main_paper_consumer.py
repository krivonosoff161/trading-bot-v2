"""Paper-only consumer audit for main-readable paper instructions.

This is the first safe main-facing consumer layer. It validates the
MainPaperInstruction view, reconstructs the shared SignalContract, and writes an
append-only paper-watch audit artifact. It deliberately does not import the old
main runtime, Telegram, exchange clients, credentials, or order execution code.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.main_paper_bridge import ACTIVE_STATUSES, SCHEMA as INSTRUCTION_SCHEMA
from src.strategy.signal_contract import ExitRule, FollowRule, SignalContract

SCHEMA = "MainPaperConsumerRecord.v1"
SUMMARY_SCHEMA = "main_paper_consumer.v1"


@dataclass(frozen=True)
class MainPaperConsumerRecord:
    consumer_id: str
    instruction_id: str
    source_signal_id: str
    pair: str
    okx_inst_id: str
    timeframe: str
    side: str
    setup_family: str
    source_status: str
    consumer_status: str
    problems: list[str] = field(default_factory=list)
    signal_contract: dict[str, Any] = field(default_factory=dict)
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError(f"schema must be {SCHEMA}")
        if self.execution_allowed:
            raise ValueError("consumer records must never allow execution")
        if not self.paper_only:
            raise ValueError("consumer records must be paper_only")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_consumed.jsonl"


def _snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_consumed.json"


def _instruction_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_instructions.json"


def _instruction_jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "main_paper_instructions.jsonl"


def _contract_from_dict(raw: dict[str, Any]) -> SignalContract:
    exit_raw = raw.get("exit_rule") or {}
    follow_raw = raw.get("follow") or {}
    return SignalContract(
        pair=str(raw.get("pair") or ""),
        side=raw.get("side"),
        entry=float(raw.get("entry")),
        stop=float(raw.get("stop")),
        exit_rule=ExitRule(
            type=exit_raw.get("type"),
            params=dict(exit_raw.get("params") or {}),
        ),
        max_hold_min=int(raw.get("max_hold_min")),
        follow=FollowRule(
            be_at_R=follow_raw.get("be_at_R"),
            trail=dict(follow_raw.get("trail") or {}),
        ),
        regime=str(raw.get("regime") or ""),
        analyzer_id=str(raw.get("analyzer_id") or ""),
        snapshot_id=str(raw.get("snapshot_id") or ""),
        ts=str(raw.get("ts") or ""),
        metadata=dict(raw.get("metadata") or {}),
    )


def _load_instruction_rows(private_root: Path) -> tuple[list[dict[str, Any]], Path | None]:
    snapshot = _instruction_snapshot_path(private_root)
    if snapshot.exists():
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        return list(items or []), snapshot

    jsonl = _instruction_jsonl_path(private_root)
    if not jsonl.exists():
        return [], None
    rows: list[dict[str, Any]] = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows, jsonl


def _validate_instruction(row: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if row.get("schema") != INSTRUCTION_SCHEMA:
        problems.append("bad_schema")
    if row.get("execution_allowed") is not False:
        problems.append("execution_allowed_not_false")
    if row.get("paper_only") is not True:
        problems.append("paper_only_not_true")
    if row.get("source_status") not in ACTIVE_STATUSES:
        problems.append("inactive_source_status")
    if not row.get("instruction_id"):
        problems.append("missing_instruction_id")
    if not row.get("source_signal_id"):
        problems.append("missing_source_signal_id")
    if not row.get("take_profit_plan"):
        problems.append("missing_take_profit_plan")
    try:
        contract = _contract_from_dict(dict(row.get("signal_contract") or {}))
        meta = contract.metadata
        if meta.get("execution_allowed") is not False:
            problems.append("contract_execution_allowed_not_false")
        if meta.get("paper_only") is not True:
            problems.append("contract_paper_only_not_true")
        if contract.pair != row.get("okx_inst_id"):
            problems.append("contract_pair_mismatch")
        if contract.side != row.get("side"):
            problems.append("contract_side_mismatch")
    except (TypeError, ValueError, KeyError):
        problems.append("invalid_signal_contract")
    return problems


def _record_from_instruction(row: dict[str, Any], problems: list[str]) -> MainPaperConsumerRecord:
    status = "accepted_for_paper_watch" if not problems else "rejected_contract"
    return MainPaperConsumerRecord(
        consumer_id=f"consumer_{row.get('instruction_id') or 'missing'}",
        instruction_id=str(row.get("instruction_id") or ""),
        source_signal_id=str(row.get("source_signal_id") or ""),
        pair=str(row.get("pair") or ""),
        okx_inst_id=str(row.get("okx_inst_id") or ""),
        timeframe=str(row.get("timeframe") or ""),
        side=str(row.get("side") or ""),
        setup_family=str(row.get("setup_family") or ""),
        source_status=str(row.get("source_status") or ""),
        consumer_status=status,
        problems=problems,
        signal_contract=dict(row.get("signal_contract") or {}),
    )


def consume_main_paper_instructions(private_root: Path) -> dict[str, Any]:
    rows, source_path = _load_instruction_rows(private_root)
    records = [_record_from_instruction(row, _validate_instruction(row)) for row in rows]

    out_jsonl = _jsonl_path(private_root)
    out_snapshot = _snapshot_path(private_root)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    accepted = sum(1 for record in records if record.consumer_status == "accepted_for_paper_watch")
    rejected = len(records) - accepted
    summary = {
        "schema": SUMMARY_SCHEMA,
        "source_schema": INSTRUCTION_SCHEMA,
        "instructions_read": len(rows),
        "accepted": accepted,
        "rejected": rejected,
        "source_path": str(source_path) if source_path else "",
        "source_exists": source_path is not None,
        "paper_only": True,
        "execution_allowed": False,
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
    }
    out_snapshot.write_text(
        json.dumps({**summary, "items": [record.to_dict() for record in records]},
                   ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
