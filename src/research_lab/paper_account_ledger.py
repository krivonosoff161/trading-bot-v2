"""Deterministic, paper-only account ledger for main-paper trade theses.

The broad research lane may contain several geometry variants for one market
observation.  This ledger allocates capital to one primary thesis per scenario
and keeps the remaining variants counterfactual.  It has no exchange, env,
provider, Telegram, or order imports.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import stable_id
from src.research_lab.paper_money_model import PaperMoneyModel, default_paper_money_model

EVENT_SCHEMA = "PaperAccountEvent.v1"
SUMMARY_SCHEMA = "paper_account_ledger.v1"
TERMINAL_SIGNAL_STATUSES = {"closed_paper", "expired", "reviewed", "invalidated"}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def _events_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_account_events.jsonl"


def _snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_account.json"


def _load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        if row.get("schema") != EVENT_SCHEMA:
            raise ValueError("unexpected paper account event schema")
        if row.get("paper_only") is not True or row.get("execution_allowed") is not False:
            raise ValueError("paper account event crossed the execution boundary")
        rows.append(row)
    return rows


def _scenario_id(trade: dict[str, Any]) -> str:
    explicit = str(trade.get("scenario_id") or "").strip()
    if explicit:
        return explicit
    return stable_id(
        "paperscenario",
        {
            "instrument": trade.get("okx_inst_id"),
            "timeframe": trade.get("timeframe"),
            "side": trade.get("side"),
            "boundary_ts": _int(trade.get("boundary_ts")),
        },
        length=20,
    )


def _opened_ts(trade: dict[str, Any]) -> int:
    outcome = trade.get("outcome") if isinstance(trade.get("outcome"), dict) else {}
    return _int(outcome.get("opened_at_bar_ts") or trade.get("boundary_ts"))


def _closed_ts(trade: dict[str, Any]) -> int:
    outcome = trade.get("outcome") if isinstance(trade.get("outcome"), dict) else {}
    return _int(outcome.get("last_observed_bar_ts") or _opened_ts(trade))


def _has_opened(trade: dict[str, Any]) -> bool:
    outcome = trade.get("outcome") if isinstance(trade.get("outcome"), dict) else {}
    return bool(
        outcome.get("opened_at_bar_ts")
        or trade.get("signal_status") == "opened_paper"
        or outcome.get("net_pct") not in (None, "")
    )


def _is_terminal(trade: dict[str, Any]) -> bool:
    outcome = trade.get("outcome") if isinstance(trade.get("outcome"), dict) else {}
    return bool(
        trade.get("signal_status") in TERMINAL_SIGNAL_STATUSES
        or str(trade.get("status") or "").startswith("closed_")
        or outcome.get("net_pct") not in (None, "")
    )


def _priority(trade: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if trade.get("validation_tier") == "validated_pfr" else 1,
        -_float(trade.get("adaptive_policy_confidence")),
        _int(trade.get("boundary_ts")),
        str(trade.get("paper_trade_id") or ""),
    )


def _event(
    event_type: str,
    trade: dict[str, Any],
    *,
    event_ts: int,
    model: PaperMoneyModel,
    reason: str,
) -> dict[str, Any]:
    trade_id = str(trade.get("paper_trade_id") or "")
    scenario_id = _scenario_id(trade)
    outcome = trade.get("outcome") if isinstance(trade.get("outcome"), dict) else {}
    net_pct = _float(outcome.get("net_pct")) if outcome.get("net_pct") not in (None, "") else None
    fees_bps = _float(outcome.get("fees_bps_round_trip"))
    slippage_bps = _float(outcome.get("slippage_bps_round_trip"))
    pnl_usdt = round(model.notional_usdt * net_pct / 100.0, 6) if net_pct is not None else None
    return {
        "schema": EVENT_SCHEMA,
        "event_id": stable_id(
            "paperaccountevent",
            {"event_type": event_type, "paper_trade_id": trade_id, "scenario_id": scenario_id},
            length=24,
        ),
        "event_type": event_type,
        "event_ts": event_ts,
        "paper_trade_id": trade_id,
        "source_signal_id": str(trade.get("source_signal_id") or ""),
        "scenario_id": scenario_id,
        "validation_tier": str(trade.get("validation_tier") or ""),
        "position_margin_usdt": model.position_margin_usdt,
        "leverage": model.leverage,
        "notional_usdt": model.notional_usdt,
        "net_pct": net_pct,
        "pnl_usdt": pnl_usdt,
        "fees_usdt": round(model.notional_usdt * fees_bps / 10_000.0, 6),
        "slippage_usdt": round(model.notional_usdt * slippage_bps / 10_000.0, 6),
        "reason": reason,
        "paper_only": True,
        "execution_allowed": False,
    }


def _replay(events: list[dict[str, Any]], model: PaperMoneyModel) -> dict[str, Any]:
    balance = model.deposit_usdt
    reserved: dict[str, float] = {}
    opened: set[str] = set()
    closed: set[str] = set()
    rejected = 0
    counterfactual = 0
    terminal = 0
    wins = 0
    losses = 0
    total_fees = 0.0
    total_slippage = 0.0

    order = {"position_opened": 0, "position_closed": 1, "allocation_rejected": 2, "counterfactual_excluded": 3}
    for row in sorted(events, key=lambda item: (_int(item.get("event_ts")), order.get(str(item.get("event_type")), 9), str(item.get("event_id")))):
        event_type = str(row.get("event_type") or "")
        trade_id = str(row.get("paper_trade_id") or "")
        if event_type == "position_opened" and trade_id not in opened:
            reserved[trade_id] = _float(row.get("position_margin_usdt"))
            opened.add(trade_id)
        elif event_type == "position_closed" and trade_id in opened and trade_id not in closed:
            pnl = _float(row.get("pnl_usdt"))
            balance += pnl
            reserved.pop(trade_id, None)
            closed.add(trade_id)
            terminal += 1
            wins += int(pnl > 0)
            losses += int(pnl < 0)
            total_fees += _float(row.get("fees_usdt"))
            total_slippage += _float(row.get("slippage_usdt"))
        elif event_type == "allocation_rejected":
            rejected += 1
        elif event_type == "counterfactual_excluded":
            counterfactual += 1

    reserved_margin = round(sum(reserved.values()), 6)
    return {
        "balance_usdt": round(balance, 6),
        "equity_usdt": round(balance, 6),
        "reserved_margin_usdt": reserved_margin,
        "available_margin_usdt": round(balance - reserved_margin, 6),
        "active_positions": len(reserved),
        "active_trade_ids": sorted(reserved),
        "terminal_trades": terminal,
        "wins": wins,
        "losses": losses,
        "total_pnl_usdt": round(balance - model.deposit_usdt, 6),
        "total_fees_usdt": round(total_fees, 6),
        "total_slippage_usdt": round(total_slippage, 6),
        "allocation_rejections": rejected,
        "counterfactual_exclusions": counterfactual,
    }


def build_paper_account_ledger(
    private_root: Path,
    trades: list[dict[str, Any]],
    *,
    model: PaperMoneyModel | None = None,
) -> dict[str, Any]:
    """Reconcile append-only paper account events from strict main trades."""
    private_root = Path(private_root)
    model = model or default_paper_money_model()
    events_path = _events_path(private_root)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events = _load_events(events_path)
    event_ids = {str(row.get("event_id") or "") for row in events}
    scenario_owner = {
        str(row.get("scenario_id") or ""): str(row.get("paper_trade_id") or "")
        for row in events
        if row.get("event_type") == "position_opened"
    }

    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        if not _has_opened(trade):
            continue
        grouped.setdefault(_scenario_id(trade), []).append(trade)

    planned: list[dict[str, Any]] = []
    for scenario_id, candidates in grouped.items():
        owner_id = scenario_owner.get(scenario_id)
        if not owner_id:
            owner_id = str(min(candidates, key=_priority).get("paper_trade_id") or "")
        for trade in candidates:
            trade_id = str(trade.get("paper_trade_id") or "")
            if trade_id != owner_id:
                planned.append(_event("counterfactual_excluded", trade, event_ts=_opened_ts(trade), model=model, reason="non_primary_scenario_variant"))
                continue
            planned.append(_event("position_opened", trade, event_ts=_opened_ts(trade), model=model, reason="primary_scenario_thesis"))
            if _is_terminal(trade):
                planned.append(_event("position_closed", trade, event_ts=_closed_ts(trade), model=model, reason="observed_terminal_outcome"))

    added: list[dict[str, Any]] = []
    for candidate in sorted(planned, key=lambda row: (_int(row.get("event_ts")), 0 if row.get("event_type") == "position_opened" else 1, str(row.get("event_id")))):
        if candidate["event_id"] in event_ids:
            continue
        if candidate["event_type"] == "position_opened":
            state = _replay(events + added, model)
            if state["available_margin_usdt"] < model.position_margin_usdt:
                candidate = {**candidate, "event_type": "allocation_rejected", "reason": "insufficient_available_margin"}
                candidate["event_id"] = stable_id(
                    "paperaccountevent",
                    {"event_type": "allocation_rejected", "paper_trade_id": candidate["paper_trade_id"], "scenario_id": candidate["scenario_id"]},
                    length=24,
                )
        elif candidate["event_type"] == "position_closed":
            state = _replay(events + added, model)
            if candidate["paper_trade_id"] not in state["active_trade_ids"]:
                continue
        if candidate["event_id"] not in event_ids:
            added.append(candidate)
            event_ids.add(candidate["event_id"])

    if added:
        with events_path.open("a", encoding="utf-8") as fh:
            for row in added:
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    events.extend(added)
    account = _replay(events, model)
    summary = {
        "schema": SUMMARY_SCHEMA,
        "event_schema": EVENT_SCHEMA,
        "model": model.to_dict(),
        **account,
        "events": len(events),
        "events_added": len(added),
        "event_log_path": str(events_path),
        "snapshot_path": str(_snapshot_path(private_root)),
        "paper_only": True,
        "execution_allowed": False,
    }
    _snapshot_path(private_root).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def audit_paper_account_ledger(private_root: Path, *, model: PaperMoneyModel | None = None) -> dict[str, Any]:
    """Independently replay the append-only account log and compare its snapshot."""
    private_root = Path(private_root)
    model = model or default_paper_money_model()
    try:
        events = _load_events(_events_path(private_root))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "reason": f"event_log_unreadable:{type(exc).__name__}",
            "paper_only": True,
            "execution_allowed": False,
        }
    replay = _replay(events, model)
    try:
        snapshot = json.loads(_snapshot_path(private_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        snapshot = {}
    fields = (
        "balance_usdt",
        "reserved_margin_usdt",
        "available_margin_usdt",
        "active_positions",
        "terminal_trades",
        "wins",
        "losses",
        "total_pnl_usdt",
    )
    mismatches = {
        field: {"snapshot": snapshot.get(field), "replay": replay.get(field)}
        for field in fields
        if snapshot.get(field) != replay.get(field)
    }
    return {
        "valid": bool(snapshot) and not mismatches,
        "events": len(events),
        "mismatches": mismatches,
        "replay": replay,
        "paper_only": True,
        "execution_allowed": False,
    }
