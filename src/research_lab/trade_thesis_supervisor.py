"""Paper-only trade thesis supervisor.

The supervisor groups visible paper-product candidates into symbol-level
trading theses. It is deliberately downstream of the paper ledger and upstream
of previews/reports: it can describe scenario context, but it never mutates
signals, opens orders, imports exchange clients, or grants execution authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import stable_id, utc_now

THESIS_SCHEMA = "TradeThesis.v1"
EVENT_SCHEMA = "TradeThesisEvent.v1"
SUMMARY_SCHEMA = "trade_thesis_supervisor.v1"
ACTIVE_STATUSES = {"armed", "opened_paper"}
TIMEFRAME_RANK = {
    "1m": 1,
    "3m": 2,
    "5m": 3,
    "15m": 4,
    "30m": 5,
    "1h": 6,
    "2h": 7,
    "4h": 8,
    "1d": 9,
}


@dataclass(frozen=True)
class TradeThesis:
    thesis_id: str
    symbol: str
    side: str
    primary_signal_id: str
    primary_timeframe: str
    primary_family: str
    primary_status: str
    primary_source: str
    primary_validation_tier: str
    active_signals: int
    created_at: str
    state_hash: str = ""
    updated_at: str = ""
    closed_at: str = ""
    close_reason: str = ""
    status: str = "active"
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = THESIS_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TradeThesisEvent:
    event_id: str
    thesis_id: str
    symbol: str
    source_signal_id: str
    signal_timeframe: str
    signal_side: str
    signal_family: str
    signal_status: str
    event_type: str
    supervisor_action: str
    reason_codes: list[str] = field(default_factory=list)
    event_ts: str = ""
    state_hash: str = ""
    terminal_result: str = ""
    terminal_net_pct: float | None = None
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = EVENT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _derived(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived"


def _ledger_path(private_root: Path) -> Path:
    return _derived(private_root) / "paper_product_trades.json"


def _summary_path(private_root: Path) -> Path:
    return _derived(private_root) / "trade_thesis_supervisor.json"


def _thesis_jsonl_path(private_root: Path) -> Path:
    return _derived(private_root) / "trade_theses.jsonl"


def _event_jsonl_path(private_root: Path) -> Path:
    return _derived(private_root) / "trade_thesis_events.jsonl"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("items")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _tf_rank(value: Any) -> int:
    return TIMEFRAME_RANK.get(str(value or "").strip().lower(), 0)


def _created_sort_key(row: dict[str, Any]) -> str:
    raw = row.get("source_created_at") or row.get("created_at")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
    return str(raw or "")


def _validation_tier(row: dict[str, Any]) -> str:
    if bool(row.get("live_ready")):
        return "validated_pfr"
    if row.get("ready_strategy_id"):
        return "validator_context_present"
    return "farm_calculated"


def _leader_score(row: dict[str, Any]) -> tuple[int, int, int, str]:
    live = 1 if bool(row.get("live_ready")) else 0
    opened = 1 if str(row.get("status") or "") == "opened_paper" else 0
    source_bonus = 1 if str(row.get("source") or "") == "pfr_farm" else 0
    return (
        _tf_rank(row.get("timeframe")),
        live,
        opened + source_bonus,
        _created_sort_key(row),
    )


def _state_hash(symbol: str, leader: dict[str, Any], group: list[dict[str, Any]]) -> str:
    return stable_id(
        "thesisstate",
        {
            "symbol": symbol,
            "primary_signal_id": leader.get("source_signal_id") or leader.get("paper_product_trade_id"),
            "signals": [
                {
                    "id": row.get("source_signal_id") or row.get("paper_product_trade_id"),
                    "side": row.get("side"),
                    "timeframe": row.get("timeframe"),
                    "status": row.get("status"),
                    "entry": row.get("entry"),
                    "stop": row.get("stop"),
                    "targets": row.get("take_profit_plan"),
                }
                for row in sorted(group, key=lambda item: str(item.get("source_signal_id") or ""))
            ],
        },
        length=20,
    )


def _thesis_from_leader(
    leader: dict[str, Any],
    group: list[dict[str, Any]],
    previous: dict[str, Any] | None,
) -> TradeThesis:
    symbol = str(leader.get("okx_inst_id") or leader.get("symbol") or "")
    signal_id = str(leader.get("source_signal_id") or leader.get("paper_product_trade_id") or "")
    started_at = str((previous or {}).get("created_at") or min(_created_sort_key(row) for row in group))
    thesis_id = str((previous or {}).get("thesis_id") or "") or stable_id(
        "thesis", {"symbol": symbol, "started_at": started_at}, length=20
    )
    now = utc_now()
    return TradeThesis(
        thesis_id=thesis_id,
        symbol=symbol,
        side=str(leader.get("side") or ""),
        primary_signal_id=signal_id,
        primary_timeframe=str(leader.get("timeframe") or ""),
        primary_family=str(leader.get("setup_family") or ""),
        primary_status=str(leader.get("status") or ""),
        primary_source=str(leader.get("source") or ""),
        primary_validation_tier=_validation_tier(leader),
        active_signals=len(group),
        created_at=started_at,
        state_hash=_state_hash(symbol, leader, group),
        updated_at=now,
    )


def _classify_signal(thesis: TradeThesis, row: dict[str, Any]) -> tuple[str, str, list[str]]:
    side = str(row.get("side") or "")
    signal_id = str(row.get("source_signal_id") or row.get("paper_product_trade_id") or "")
    signal_rank = _tf_rank(row.get("timeframe"))
    thesis_rank = _tf_rank(thesis.primary_timeframe)
    reasons = [
        f"thesis_side:{thesis.side}",
        f"signal_side:{side}",
        f"thesis_tf:{thesis.primary_timeframe}",
        f"signal_tf:{row.get('timeframe') or ''}",
        f"validation:{_validation_tier(row)}",
    ]
    if signal_id == thesis.primary_signal_id:
        return "primary_thesis", "track_primary", reasons + ["primary_signal"]
    if side == thesis.side:
        if signal_rank >= thesis_rank:
            return "confirmation", "hold_or_add_watch", reasons + ["same_side_equal_or_higher_tf"]
        return "lower_tf_confirmation", "add_watch", reasons + ["same_side_lower_tf"]
    if signal_rank < thesis_rank:
        return "countertrend_bounce", "hold_primary_tighten_watch", reasons + ["opposite_lower_tf"]
    if signal_rank == thesis_rank:
        return "invalidation_warning", "tighten_or_flip_watch", reasons + ["opposite_equal_tf"]
    return "higher_tf_conflict", "flip_watch", reasons + ["opposite_higher_tf"]


def build_trade_thesis_supervisor(private_root: Path) -> dict[str, Any]:
    private_root = Path(private_root)
    ledger = _read_json(_ledger_path(private_root))
    rows = _items(ledger)
    previous_summary = _read_json(_summary_path(private_root))
    previous_active = {
        str(item.get("symbol") or ""): item
        for item in _items(previous_summary)
        if str(item.get("status") or "") == "active" and str(item.get("symbol") or "")
    }
    existing_events = _read_jsonl(_event_jsonl_path(private_root))
    existing_event_ids = {str(item.get("event_id") or "") for item in existing_events}
    active_rows = [row for row in rows if str(row.get("status") or "") in ACTIVE_STATUSES]
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in active_rows:
        symbol = str(row.get("okx_inst_id") or row.get("symbol") or "")
        if symbol:
            by_symbol.setdefault(symbol, []).append(row)

    theses: list[TradeThesis] = []
    new_events: list[TradeThesisEvent] = []
    for symbol, group in sorted(by_symbol.items()):
        previous = previous_active.get(symbol)
        previous_primary = str((previous or {}).get("primary_signal_id") or "")
        leader = next(
            (
                row
                for row in group
                if str(row.get("source_signal_id") or row.get("paper_product_trade_id") or "")
                == previous_primary
            ),
            None,
        ) or max(group, key=_leader_score)
        thesis = _thesis_from_leader(leader, group, previous)
        theses.append(thesis)
        if previous is None:
            event_id = stable_id(
                "thesis_event",
                {"thesis_id": thesis.thesis_id, "event_type": "scenario_opened"},
                length=20,
            )
            new_events.append(
                TradeThesisEvent(
                    event_id=event_id,
                    thesis_id=thesis.thesis_id,
                    symbol=symbol,
                    source_signal_id=thesis.primary_signal_id,
                    signal_timeframe=thesis.primary_timeframe,
                    signal_side=thesis.side,
                    signal_family=thesis.primary_family,
                    signal_status=thesis.primary_status,
                    event_type="scenario_opened",
                    supervisor_action="start_watch",
                    reason_codes=["first_active_signal"],
                    event_ts=thesis.created_at,
                    state_hash=thesis.state_hash,
                )
            )
        elif str(previous.get("state_hash") or "") != thesis.state_hash:
            event_id = stable_id(
                "thesis_event",
                {"thesis_id": thesis.thesis_id, "event_type": "scenario_updated", "state_hash": thesis.state_hash},
                length=20,
            )
            reasons = ["active_state_changed"]
            if previous_primary != thesis.primary_signal_id:
                reasons.append("primary_signal_changed")
            new_events.append(
                TradeThesisEvent(
                    event_id=event_id,
                    thesis_id=thesis.thesis_id,
                    symbol=symbol,
                    source_signal_id=thesis.primary_signal_id,
                    signal_timeframe=thesis.primary_timeframe,
                    signal_side=thesis.side,
                    signal_family=thesis.primary_family,
                    signal_status=thesis.primary_status,
                    event_type="scenario_updated",
                    supervisor_action="update_watch",
                    reason_codes=reasons,
                    event_ts=thesis.updated_at,
                    state_hash=thesis.state_hash,
                )
            )
        for row in sorted(group, key=lambda item: (_created_sort_key(item), str(item.get("source_signal_id") or ""))):
            event_type, action, reasons = _classify_signal(thesis, row)
            signal_id = str(row.get("source_signal_id") or row.get("paper_product_trade_id") or "")
            event_id = stable_id(
                "thesis_event",
                {
                    "thesis_id": thesis.thesis_id,
                    "source_signal_id": signal_id,
                    "event_type": event_type,
                    "action": action,
                },
                length=20,
            )
            new_events.append(
                TradeThesisEvent(
                    event_id=event_id,
                    thesis_id=thesis.thesis_id,
                    symbol=symbol,
                    source_signal_id=signal_id,
                    signal_timeframe=str(row.get("timeframe") or ""),
                    signal_side=str(row.get("side") or ""),
                    signal_family=str(row.get("setup_family") or ""),
                    signal_status=str(row.get("status") or ""),
                    event_type=event_type,
                    supervisor_action=action,
                    reason_codes=reasons,
                    event_ts=_created_sort_key(row),
                    state_hash=thesis.state_hash,
                )
            )

    rows_by_signal = {
        str(row.get("source_signal_id") or row.get("paper_product_trade_id") or ""): row
        for row in rows
    }
    for symbol, previous in sorted(previous_active.items()):
        if symbol in by_symbol:
            continue
        now = utc_now()
        primary = rows_by_signal.get(str(previous.get("primary_signal_id") or "")) or {}
        outcome = primary.get("outcome") if isinstance(primary.get("outcome"), dict) else {}
        result = str(outcome.get("result") or primary.get("status") or "observation_ended")
        net_pct = outcome.get("net_pct")
        closed = TradeThesis(
            thesis_id=str(previous.get("thesis_id") or ""),
            symbol=symbol,
            side=str(previous.get("side") or ""),
            primary_signal_id=str(previous.get("primary_signal_id") or ""),
            primary_timeframe=str(previous.get("primary_timeframe") or ""),
            primary_family=str(previous.get("primary_family") or ""),
            primary_status=str(primary.get("status") or "closed"),
            primary_source=str(previous.get("primary_source") or ""),
            primary_validation_tier=str(previous.get("primary_validation_tier") or "farm_calculated"),
            active_signals=0,
            created_at=str(previous.get("created_at") or ""),
            state_hash=str(previous.get("state_hash") or ""),
            updated_at=now,
            closed_at=now,
            close_reason=result,
            status="closed",
        )
        theses.append(closed)
        event_id = stable_id(
            "thesis_event",
            {"thesis_id": closed.thesis_id, "event_type": "scenario_closed", "result": result},
            length=20,
        )
        new_events.append(
            TradeThesisEvent(
                event_id=event_id,
                thesis_id=closed.thesis_id,
                symbol=symbol,
                source_signal_id=closed.primary_signal_id,
                signal_timeframe=closed.primary_timeframe,
                signal_side=closed.side,
                signal_family=closed.primary_family,
                signal_status=closed.primary_status,
                event_type="scenario_closed",
                supervisor_action="stop_watch",
                reason_codes=["no_active_signals", f"terminal:{result}"],
                event_ts=now,
                state_hash=closed.state_hash,
                terminal_result=result,
                terminal_net_pct=float(net_pct) if net_pct not in (None, "") else None,
            )
        )

    appended_events = [event for event in new_events if event.event_id not in existing_event_ids]
    event_rows = existing_events + [event.to_dict() for event in appended_events]

    by_event_type: dict[str, int] = {}
    by_action: dict[str, int] = {}
    by_primary_side: dict[str, int] = {}
    for thesis in theses:
        by_primary_side[thesis.side] = by_primary_side.get(thesis.side, 0) + 1
    for event in event_rows:
        event_type = str(event.get("event_type") or "")
        action = str(event.get("supervisor_action") or "")
        by_event_type[event_type] = by_event_type.get(event_type, 0) + 1
        by_action[action] = by_action.get(action, 0) + 1

    return {
        "schema": SUMMARY_SCHEMA,
        "thesis_schema": THESIS_SCHEMA,
        "event_schema": EVENT_SCHEMA,
        "source_schema": ledger.get("schema") or "",
        "source_trades": int(ledger.get("trades") or len(rows)),
        "active_trades": len(active_rows),
        "theses": len(theses),
        "events": len(event_rows),
        "events_added": len(appended_events),
        "by_event_type": dict(sorted(by_event_type.items())),
        "by_action": dict(sorted(by_action.items())),
        "by_primary_side": dict(sorted(by_primary_side.items())),
        "items": [thesis.to_dict() for thesis in theses],
        "event_items": event_rows[-500:],
        "paper_only": True,
        "execution_allowed": False,
    }


def write_trade_thesis_supervisor(private_root: Path) -> dict[str, Any]:
    private_root = Path(private_root)
    summary = build_trade_thesis_supervisor(private_root)
    out_summary = _summary_path(private_root)
    out_theses = _thesis_jsonl_path(private_root)
    out_events = _event_jsonl_path(private_root)
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    theses = summary.pop("items")
    events = summary.pop("event_items")
    prior_thesis_revisions = {
        (str(row.get("thesis_id") or ""), str(row.get("state_hash") or ""), str(row.get("status") or ""))
        for row in _read_jsonl(out_theses)
    }
    with out_theses.open("a", encoding="utf-8") as fh:
        for item in theses:
            revision = (
                str(item.get("thesis_id") or ""),
                str(item.get("state_hash") or ""),
                str(item.get("status") or ""),
            )
            if revision not in prior_thesis_revisions:
                fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
                prior_thesis_revisions.add(revision)
    existing_event_ids = {
        str(row.get("event_id") or "") for row in _read_jsonl(out_events)
    }
    with out_events.open("a", encoding="utf-8") as fh:
        for item in events:
            event_id = str(item.get("event_id") or "")
            if event_id and event_id not in existing_event_ids:
                fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
                existing_event_ids.add(event_id)
    summary["items"] = theses[:200]
    summary["event_items"] = events[:500]
    summary["snapshot_path"] = str(out_summary)
    summary["theses_jsonl_path"] = str(out_theses)
    summary["events_jsonl_path"] = str(out_events)
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
