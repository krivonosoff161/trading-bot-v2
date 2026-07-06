"""Paper-only exit supervision for active paper signals.

The supervisor observes active paper-watch state and writes bounded exit advice.
It never mutates signals, never sends Telegram, never imports exchange/order
modules, and never grants LLM authority. A future LLM sidecar may comment only
through ``validate_exit_advisor_payload``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

from src.research_lab.paper_signals import store

SCHEMA = "PaperExitSupervisorItem.v1"
SUMMARY_SCHEMA = "paper_exit_supervisor.v1"

FORBIDDEN_ADVISOR_FIELDS = {
    "entry",
    "entry_zone",
    "stop",
    "stop_loss",
    "take_profit",
    "take_profit_plan",
    "tp1",
    "price",
    "order",
    "close",
    "close_order",
    "execution_allowed",
    "leverage",
    "size",
}


@dataclass(frozen=True)
class PaperExitSupervisorItem:
    supervisor_id: str
    source_signal_id: str
    symbol: str
    timeframe: str
    side: str
    setup_family: str
    source_status: str
    deterministic_action: str
    urgency: str
    reason_codes: list[str] = field(default_factory=list)
    llm_advice_status: str = "not_requested"
    llm_advice: dict[str, Any] = field(default_factory=dict)
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sid(parts: list[str]) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"exit_supervisor_{digest}"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in ("", None) else default)
    except (TypeError, ValueError):
        return default


def _deterministic_decision(row: dict[str, Any]) -> tuple[str, str, list[str]]:
    status = str(row.get("status") or "")
    outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
    review = row.get("review") if isinstance(row.get("review"), dict) else {}
    result = str(outcome.get("result") or "")
    mfe = _f(outcome.get("mfe_pct") or review.get("mfe_pct"))
    mae = _f(outcome.get("mae_pct") or review.get("mae_pct"))
    risk_pct = _f(row.get("risk_pct"), 1.0) or 1.0
    bars_held = int(_f(outcome.get("bars_held"), 0))
    max_hold = max(1, int(_f(row.get("max_hold_bars"), 1)))
    partial_done = bool(outcome.get("partial_done"))
    mfe_r = mfe / risk_pct
    mae_r = mae / risk_pct
    reasons = [f"status:{status}", f"result:{result or 'none'}"]

    if status == "armed":
        return "watch_entry", "low", reasons + ["not_opened_yet"]
    if status != "opened_paper":
        return "ignore_non_active", "low", reasons
    if mae_r >= 0.8 and mfe_r < 0.3:
        return "risk_reduce_watch", "high", reasons + ["adverse_move_without_favourable_excursion"]
    if mfe_r >= 1.5 and bars_held >= max(1, max_hold // 2):
        return "consider_paper_close", "high", reasons + ["large_mfe_mid_or_late_hold"]
    if mfe_r >= 1.0 and not partial_done:
        return "lock_profit_watch", "medium", reasons + ["mfe_at_least_one_r_without_partial_lock"]
    if partial_done:
        return "hold_with_breakeven_guard", "medium", reasons + ["partial_or_be_guard_active"]
    if bars_held >= max_hold:
        return "time_stop_watch", "medium", reasons + ["max_hold_reached"]
    return "hold", "low", reasons + ["no_exit_pressure"]


def validate_exit_advisor_payload(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    problems: list[str] = []
    keys = {str(key) for key in payload}
    forbidden = sorted(keys & FORBIDDEN_ADVISOR_FIELDS)
    if forbidden:
        problems.append("forbidden_fields:" + ",".join(forbidden))
    if payload.get("execution_allowed") is True:
        problems.append("execution_allowed_true")
    if payload.get("paper_only") is False:
        problems.append("paper_only_false")
    for key in ("advisor_action", "rationale", "confidence"):
        if key in payload and not isinstance(payload.get(key), (str, int, float)):
            problems.append(f"{key}_bad_type")
    return not problems, problems


def build_exit_supervisor_items(
    private_root: Path,
    *,
    advisor_payloads: dict[str, dict[str, Any]] | None = None,
) -> list[PaperExitSupervisorItem]:
    advisor_payloads = advisor_payloads or {}
    items: list[PaperExitSupervisorItem] = []
    for sig in store.load_signals(Path(private_root)):
        if sig.status not in {"armed", "opened_paper"}:
            continue
        row = sig.to_dict()
        action, urgency, reasons = _deterministic_decision(row)
        raw_advice = advisor_payloads.get(sig.signal_id) or {}
        llm_status = "not_requested"
        advice: dict[str, Any] = {}
        if raw_advice:
            ok, problems = validate_exit_advisor_payload(raw_advice)
            llm_status = "accepted" if ok else "rejected"
            advice = dict(raw_advice) if ok else {"problems": problems}
        items.append(
            PaperExitSupervisorItem(
                supervisor_id=_sid([sig.signal_id, action, urgency]),
                source_signal_id=sig.signal_id,
                symbol=sig.symbol,
                timeframe=sig.timeframe,
                side=sig.side,
                setup_family=sig.setup_family,
                source_status=sig.status,
                deterministic_action=action,
                urgency=urgency,
                reason_codes=reasons,
                llm_advice_status=llm_status,
                llm_advice=advice,
            )
        )
    return items


def write_exit_supervisor(private_root: Path) -> dict[str, Any]:
    private_root = Path(private_root)
    items = build_exit_supervisor_items(private_root)
    out_jsonl = private_root / "state" / "derived" / "paper_exit_supervisor.jsonl"
    out_snapshot = private_root / "state" / "derived" / "paper_exit_supervisor.json"
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    by_action: dict[str, int] = {}
    by_urgency: dict[str, int] = {}
    for item in items:
        by_action[item.deterministic_action] = by_action.get(item.deterministic_action, 0) + 1
        by_urgency[item.urgency] = by_urgency.get(item.urgency, 0) + 1
    summary = {
        "schema": SUMMARY_SCHEMA,
        "row_schema": SCHEMA,
        "items": [item.to_dict() for item in items[:200]],
        "supervised": len(items),
        "by_action": dict(sorted(by_action.items())),
        "by_urgency": dict(sorted(by_urgency.items())),
        "paper_only": True,
        "execution_allowed": False,
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
    }
    out_snapshot.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
