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
MARKET_CONTEXT_SCHEMA = "MarketContextSnapshot.v1"
VISUAL_EVIDENCE_SCHEMA = "VisualEvidence.v1"
FSM_SCHEMA = "TraderSupervisorFSM.v1"
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
    fsm_state: str = "active"
    fsm_watermark: str = ""
    fsm_transitions: list[dict[str, Any]] = field(default_factory=list)
    market_context_snapshot: dict[str, Any] = field(default_factory=dict)
    visual_evidence: list[dict[str, Any]] = field(default_factory=list)
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


@dataclass(frozen=True)
class VisualEvidence:
    evidence_id: str
    reference: str
    media_type: str = "chart"
    content_hash: str = ""
    observed_at: str = ""
    schema: str = VISUAL_EVIDENCE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketContextSnapshot:
    snapshot_id: str
    symbol: str
    observed_at: str
    signal_ids: list[str]
    sides: list[str]
    timeframes: list[str]
    visual_evidence_ids: list[str] = field(default_factory=list)
    version: int = 1
    schema: str = MARKET_CONTEXT_SCHEMA
    paper_only: bool = True
    execution_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _visual_evidence(row: dict[str, Any]) -> VisualEvidence | None:
    raw = row.get("visual_evidence")
    raw = raw if isinstance(raw, dict) else {}
    reference = str(raw.get("reference") or row.get("visual_evidence_ref") or "")
    if not reference:
        return None
    content_hash = str(raw.get("content_hash") or "")
    if len(content_hash) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in content_hash):
        return None
    observed_at = str(raw.get("observed_at") or _created_sort_key(row))
    return VisualEvidence(
        evidence_id=str(raw.get("evidence_id") or "") or stable_id(
            "visual",
            {"reference": reference, "content_hash": content_hash, "observed_at": observed_at},
            length=20,
        ),
        reference=reference,
        media_type=str(raw.get("media_type") or "chart"),
        content_hash=content_hash,
        observed_at=observed_at,
    )


def replay_symbol_fsm(
    group: list[dict[str, Any]], previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Replay one symbol's observations without consulting LLM output or wall time."""
    prior = previous or {}
    state = str(prior.get("fsm_state") or "idle")
    if state == "closed":
        state = "idle"
    primary_side = str(prior.get("side") or "")
    primary_timeframe = str(prior.get("primary_timeframe") or "")
    primary_signal_id = str(prior.get("primary_signal_id") or "")
    watermark = str(prior.get("fsm_watermark") or "")
    transitions: list[dict[str, Any]] = list(prior.get("fsm_transitions") or [])
    prior_seen: set[str] = {
        str(item.get("signal_id") or "") for item in transitions if item.get("signal_id")
    }
    current_ids = {
        str(row.get("source_signal_id") or row.get("paper_product_trade_id") or "")
        for row in group
    }
    if primary_signal_id and primary_signal_id not in current_ids:
        transitions.append({
            "signal_id": primary_signal_id,
            "observed_at": watermark,
            "from_state": state,
            "to_state": "idle",
            "event_type": "primary_ended",
            "action": "reselect_watch",
        })
        state = "idle"
        primary_side = ""
        primary_timeframe = ""
        primary_signal_id = ""
        watermark = ""
        prior_seen = set()
    seen: set[str] = set()
    ordered = sorted(
        group,
        key=lambda row: (
            _created_sort_key(row),
            str(row.get("source_signal_id") or row.get("paper_product_trade_id") or ""),
        ),
    )
    for row in ordered:
        signal_id = str(row.get("source_signal_id") or row.get("paper_product_trade_id") or "")
        observed_at = _created_sort_key(row)
        side = str(row.get("side") or "")
        timeframe = str(row.get("timeframe") or "")
        before = state
        degraded = str(row.get("data_quality") or "").lower() in {"degraded", "missing", "invalid"}
        degraded = degraded or bool(row.get("data_quality_flags"))
        if signal_id in prior_seen:
            continue
        if signal_id in seen:
            event_type, action = "duplicate_ignored", "ignore"
        elif watermark and observed_at <= watermark:
            event_type, action = "stale_ignored", "ignore"
        elif degraded:
            state = "data_degraded"
            event_type, action = "data_degraded", "suspend_decision"
        elif state == "idle":
            state = "active"
            primary_side, primary_timeframe, primary_signal_id = side, timeframe, signal_id
            event_type, action = "activated", "start_watch"
        elif side == primary_side:
            state = "active"
            event_type, action = "confirmation", "update_watch"
        elif _tf_rank(timeframe) > _tf_rank(primary_timeframe):
            state = "reversed"
            primary_side, primary_timeframe, primary_signal_id = side, timeframe, signal_id
            event_type, action = "reversal", "flip_watch"
        else:
            state = "contradicted"
            event_type, action = "contradiction", "tighten_watch"
        if event_type not in {"duplicate_ignored", "stale_ignored"}:
            seen.add(signal_id)
            watermark = observed_at
        transitions.append({
            "signal_id": signal_id,
            "observed_at": observed_at,
            "from_state": before,
            "to_state": state,
            "event_type": event_type,
            "action": action,
        })
    return {
        "schema": FSM_SCHEMA,
        "state": state,
        "primary_side": primary_side,
        "primary_timeframe": primary_timeframe,
        "primary_signal_id": primary_signal_id,
        "watermark": watermark,
        "transitions": transitions,
        "paper_only": True,
        "execution_allowed": False,
    }


def _market_context(symbol: str, group: list[dict[str, Any]]) -> tuple[MarketContextSnapshot, list[VisualEvidence]]:
    evidence = [item for row in group if (item := _visual_evidence(row)) is not None]
    signal_ids = [str(row.get("source_signal_id") or row.get("paper_product_trade_id") or "") for row in group]
    observed_at = max((_created_sort_key(row) for row in group), default="")
    payload = {
        "version": 1,
        "symbol": symbol,
        "observed_at": observed_at,
        "signal_ids": signal_ids,
        "sides": [str(row.get("side") or "") for row in group],
        "timeframes": [str(row.get("timeframe") or "") for row in group],
        "visual_evidence_ids": [item.evidence_id for item in evidence],
    }
    return MarketContextSnapshot(
        snapshot_id=stable_id("market_context", payload, length=20),
        symbol=symbol,
        observed_at=observed_at,
        signal_ids=signal_ids,
        sides=payload["sides"],
        timeframes=payload["timeframes"],
        visual_evidence_ids=payload["visual_evidence_ids"],
    ), evidence


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
    fsm = replay_symbol_fsm(group, previous)
    fsm_leader = next(
        (
            row for row in group
            if str(row.get("source_signal_id") or row.get("paper_product_trade_id") or "")
            == str(fsm.get("primary_signal_id") or "")
        ),
        leader,
    )
    context, evidence = _market_context(symbol, group)
    return TradeThesis(
        thesis_id=thesis_id,
        symbol=symbol,
        side=str(fsm_leader.get("side") or ""),
        primary_signal_id=str(fsm.get("primary_signal_id") or signal_id),
        primary_timeframe=str(fsm_leader.get("timeframe") or ""),
        primary_family=str(fsm_leader.get("setup_family") or ""),
        primary_status=str(fsm_leader.get("status") or ""),
        primary_source=str(fsm_leader.get("source") or ""),
        primary_validation_tier=_validation_tier(fsm_leader),
        active_signals=len(group),
        created_at=started_at,
        state_hash=_state_hash(symbol, fsm_leader, group),
        updated_at=now,
        fsm_state=str(fsm["state"]),
        fsm_watermark=str(fsm["watermark"]),
        fsm_transitions=list(fsm["transitions"]),
        market_context_snapshot=context.to_dict(),
        visual_evidence=[item.to_dict() for item in evidence],
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
            fsm_state="closed",
            fsm_watermark=str(previous.get("fsm_watermark") or ""),
            fsm_transitions=list(previous.get("fsm_transitions") or []),
            market_context_snapshot=(previous.get("market_context_snapshot") or {}),
            visual_evidence=(previous.get("visual_evidence") or []),
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
        "market_context_schema": MARKET_CONTEXT_SCHEMA,
        "visual_evidence_schema": VISUAL_EVIDENCE_SCHEMA,
        "fsm_schema": FSM_SCHEMA,
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
    summary["items"] = theses
    summary["event_items"] = events[:500]
    summary["snapshot_path"] = str(out_summary)
    summary["theses_jsonl_path"] = str(out_theses)
    summary["events_jsonl_path"] = str(out_events)
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
