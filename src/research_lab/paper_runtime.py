# -*- coding: utf-8 -*-
"""Minimal paper runtime over validated setup cards and local candles.

This module is paper/research only. It accepts only SetupCards that pass
``plan_from_setup_card`` and reads only local prepared candle files from the private
research root. It does not import the old main engine, order clients, Telegram, or .env.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.research_lab.experiment import (
    choose_symbol_file,
    finalize_trade,
    generate_signals,
    load_candles,
)
from src.research_lab.hard_validation_contract import SetupCard
from src.research_lab.paper_contract import (
    PaperPlanError,
    PaperRuntimeState,
    PaperTradeOutcome,
    PaperTradePlan,
    plan_from_setup_card,
)
from src.research_lab.paper_journal import append_paper_outcome, load_seen_trade_ids
from src.research_lab.paper_readiness import summarize_paper_readiness
from src.research_lab.paths import market_data_glob


@dataclass(frozen=True)
class PaperRunResult:
    setup_id: str
    status: str
    reason: str = ""
    trade_id: str = ""
    outcome: PaperTradeOutcome | None = None


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_ready_setup_cards(private_root: Path, *, limit: int = 50) -> list[SetupCard]:
    """Load paper-forward-ready setup cards from the private setup library."""
    cards_dir = Path(private_root) / "setup_library" / "cards"
    if not cards_dir.exists():
        return []
    cards: list[SetupCard] = []
    for path in sorted(cards_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            card = SetupCard.from_dict(payload)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        if card.paper_forward_ready:
            cards.append(card)
        if len(cards) >= limit:
            break
    return cards


def local_candles_for_plan(private_root: Path, plan: PaperTradePlan) -> list[dict[str, Any]]:
    glob_pattern = market_data_glob(private_root, plan.timeframe)
    path = choose_symbol_file(glob_pattern, plan.symbol, timeframe=plan.timeframe)
    if path is None:
        return []
    return load_candles(path)


def _candidate_signals_no_lookahead(
    candles: list[dict[str, Any]], plan: PaperTradePlan
) -> list[dict[str, Any]]:
    """Generate entry signals incrementally so future bars cannot create the entry."""
    found: dict[int, dict[str, Any]] = {}
    for end in range(1, len(candles) + 1):
        visible = candles[:end]
        for sig in generate_signals(visible, plan.family, plan.params):
            try:
                idx = int(sig["idx"])
            except (KeyError, TypeError, ValueError):
                continue
            if idx != end - 1:
                continue
            if str(sig.get("side") or "").lower() != plan.direction:
                continue
            found[idx] = dict(sig)
    return [found[i] for i in sorted(found)]


def _decide_trade(
    candles: list[dict[str, Any]], plan: PaperTradePlan, sig: dict[str, Any]
) -> dict[str, Any] | None:
    idx = int(sig["idx"])
    hold_bars = int(plan.max_hold["bars"])
    exit_idx = min(idx + hold_bars, len(candles) - 1)
    if idx >= len(candles) or exit_idx <= idx:
        return None
    side = str(sig["side"])
    entry = float(candles[idx]["open"])
    if entry <= 0:
        return None
    stop_pct = float(plan.stop_loss["value"])
    take_pct = float(plan.take_profit[0]["value"])
    exit_price = float(candles[exit_idx]["close"])
    outcome = "time_exit"
    actual_exit_idx = exit_idx
    for j in range(idx, exit_idx + 1):
        high = float(candles[j]["high"])
        low = float(candles[j]["low"])
        if side == "long":
            stop = entry * (1 - stop_pct / 100) if stop_pct > 0 else None
            take = entry * (1 + take_pct / 100) if take_pct > 0 else None
            if stop and low <= stop:
                exit_price, outcome, actual_exit_idx = stop, "stop", j
                break
            if take and high >= take:
                exit_price, outcome, actual_exit_idx = take, "take", j
                break
        else:
            stop = entry * (1 + stop_pct / 100) if stop_pct > 0 else None
            take = entry * (1 - take_pct / 100) if take_pct > 0 else None
            if stop and high >= stop:
                exit_price, outcome, actual_exit_idx = stop, "stop", j
                break
            if take and low <= take:
                exit_price, outcome, actual_exit_idx = take, "take", j
                break
    cost_pct = (float(plan.fees_bps) + float(plan.slippage_bps)) / 10000.0
    return finalize_trade(
        candles, idx, actual_exit_idx, side, entry, exit_price, outcome, sig, cost_pct
    )


def _trade_id(plan: PaperTradePlan, trade: dict[str, Any]) -> str:
    payload = {
        "setup_id": plan.setup_id,
        "candidate_id": plan.candidate_id,
        "params_hash": plan.params_hash,
        "data_fingerprint": plan.data_fingerprint,
        "entry_ts": trade.get("entry_ts"),
        "side": trade.get("side"),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "paper_" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _state_for_outcome(outcome: str) -> PaperRuntimeState:
    if outcome == "take":
        return PaperRuntimeState.CLOSED_TP
    if outcome == "stop":
        return PaperRuntimeState.CLOSED_SL
    return PaperRuntimeState.CLOSED_TIMEOUT


def execute_plan_once(plan: PaperTradePlan, candles: list[dict[str, Any]]) -> PaperRunResult:
    """Execute the most recent eligible signal for a plan against local candles."""
    if len(candles) < 2:
        return PaperRunResult(plan.setup_id, "skipped", "missing_data")
    signals = _candidate_signals_no_lookahead(candles, plan)
    if not signals:
        return PaperRunResult(plan.setup_id, "skipped", "no_signal")
    for sig in reversed(signals):
        trade = _decide_trade(candles, plan, sig)
        if trade is None:
            continue
        trade_id = _trade_id(plan, trade)
        stop_pct = float(plan.stop_loss["value"])
        net_pct = float(trade.get("net_pct") or 0.0)
        r_multiple = round(net_pct / stop_pct, 4) if stop_pct > 0 else 0.0
        outcome = PaperTradeOutcome.from_plan(
            plan,
            trade_id=trade_id,
            state=_state_for_outcome(str(trade.get("outcome") or "")),
            reason=str(trade.get("reason") or trade.get("outcome") or ""),
            outcome=str(trade.get("outcome") or ""),
            opened_at=str(trade.get("entry_ts") or ""),
            closed_at=str(trade.get("exit_ts") or ""),
            planned_entry=float(trade.get("entry") or 0.0),
            actual_sim_entry=float(trade.get("entry") or 0.0),
            exit_price=float(trade.get("exit") or 0.0),
            fees_pct=float(plan.fees_bps) / 10000.0 * 100,
            slippage_pct=float(plan.slippage_bps) / 10000.0 * 100,
            mae_pct=float(trade.get("mae_pct") or 0.0),
            mfe_pct=float(trade.get("mfe_pct") or 0.0),
            r_multiple=r_multiple,
            net_pct=net_pct,
            pnl_paper_pct=net_pct,
            recorded_at=utc_now(),
        )
        return PaperRunResult(plan.setup_id, "completed", trade_id=trade_id, outcome=outcome)
    return PaperRunResult(plan.setup_id, "skipped", "no_exit_window")


def run_paper_cycle(
    private_root: Path,
    *,
    apply: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Run one bounded paper cycle. Writes outcomes only when apply=True."""
    private_root = Path(private_root)
    readiness = summarize_paper_readiness(private_root)
    seen = load_seen_trade_ids(private_root)
    counters = {
        "cards": 0,
        "planned": 0,
        "completed": 0,
        "already_recorded": 0,
        "skipped": 0,
        "errors": 0,
        "written": 0,
    }
    results: list[dict[str, Any]] = []
    for card in load_ready_setup_cards(private_root, limit=limit):
        counters["cards"] += 1
        try:
            plan = plan_from_setup_card(card)
            counters["planned"] += 1
            candles = local_candles_for_plan(private_root, plan)
            result = execute_plan_once(plan, candles)
        except (PaperPlanError, ValueError, OSError, KeyError, TypeError) as exc:
            counters["errors"] += 1
            results.append({"setup_id": card.setup_id, "status": "error", "reason": str(exc)})
            continue
        if result.outcome is not None and result.trade_id in seen:
            counters["already_recorded"] += 1
            results.append({
                "setup_id": result.setup_id,
                "status": "already_recorded",
                "trade_id": result.trade_id,
            })
            continue
        if result.outcome is None:
            counters["skipped"] += 1
            results.append({
                "setup_id": result.setup_id,
                "status": result.status,
                "reason": result.reason,
            })
            continue
        counters["completed"] += 1
        row = result.outcome.to_dict()
        if apply:
            append_paper_outcome(private_root, result.outcome)
            seen.add(result.trade_id)
            counters["written"] += 1
        results.append({
            "setup_id": result.setup_id,
            "status": "written" if apply else "would_write",
            "trade_id": result.trade_id,
            "outcome": row.get("outcome"),
            "net_pct": row.get("net_pct"),
            "r_multiple": row.get("r_multiple"),
        })
    return {"apply": apply, "counters": counters, "readiness": readiness, "results": results}
