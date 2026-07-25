# -*- coding: utf-8 -*-
"""Read-only bridge between manual chart analysis and active farm/PFR paper signals.

The Telegram "Analysis" button uses the legacy chart analyzer. The farm/PFR
paper loop can already be tracking the same symbol, so a manual "no trade"
answer without that context looks like a contradiction. This module only reads
paper signal state and renders a short explanatory note; it never sends
Telegram messages and never touches execution/order surfaces.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
from src.research_lab.paper_signals import store
from src.research_lab.paper_signals.contract import PaperActionSignal

ACTIVE_STATUSES = {"armed", "opened_paper"}
FARM_SOURCES = {"farm", "pfr_farm"}


@dataclass(frozen=True)
class ManualFarmContextItem:
    source_signal_id: str
    source: str
    status: str
    symbol: str
    okx_inst_id: str
    timeframe: str
    side: str
    setup_family: str
    entry_zone: list[float]
    stop_loss: float
    take_profit_plan: list[dict]
    ready_strategy_id: str
    reason_now: str


def private_root_from_env() -> Path:
    raw = (
        os.getenv("STRATEGY_LAB_PRIVATE_ROOT", "").strip()
        or os.getenv("TRADING_BOT_RESEARCH_ROOT", "").strip()
    )
    return Path(raw) if raw else DEFAULT_PRIVATE_ROOT


def normalize_symbol_key(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if not raw:
        return ""
    cleaned = raw.replace("/", "-").replace("_", "-")
    parts = [p for p in cleaned.split("-") if p]
    if len(parts) >= 2:
        base = parts[0]
        quote = parts[1]
        suffix = "SWAP" if "SWAP" in parts[2:] else ""
        return "_".join([p for p in (base, quote, suffix) if p])
    return cleaned.replace("-", "_")


def _signal_keys(sig: PaperActionSignal) -> set[str]:
    return {
        normalize_symbol_key(sig.symbol),
        normalize_symbol_key(sig.okx_inst_id),
    }


def _item(sig: PaperActionSignal) -> ManualFarmContextItem:
    context = sig.validator_context or {}
    return ManualFarmContextItem(
        source_signal_id=sig.signal_id,
        source=sig.source,
        status=sig.status,
        symbol=sig.symbol,
        okx_inst_id=sig.okx_inst_id,
        timeframe=sig.timeframe,
        side=sig.side,
        setup_family=sig.setup_family,
        entry_zone=list(sig.entry_zone or []),
        stop_loss=float(sig.stop_loss or 0.0),
        take_profit_plan=list(sig.take_profit_plan or []),
        ready_strategy_id=str(context.get("ready_strategy_id") or ""),
        reason_now=str(sig.reason_now or ""),
    )


def active_farm_context_for_symbol(
    symbol: str,
    *,
    private_root: str | Path | None = None,
    limit: int = 3,
) -> list[ManualFarmContextItem]:
    wanted = normalize_symbol_key(symbol)
    if not wanted:
        return []
    root = Path(private_root) if private_root is not None else private_root_from_env()
    try:
        signals = store.load_signals(root)
    except (OSError, ValueError):
        return []

    matches: list[PaperActionSignal] = []
    for sig in signals:
        if sig.status not in ACTIVE_STATUSES:
            continue
        if sig.source not in FARM_SOURCES:
            continue
        keys = _signal_keys(sig)
        if wanted in keys or wanted.replace("_SWAP", "") in {
            k.replace("_SWAP", "") for k in keys
        }:
            matches.append(sig)

    matches.sort(
        key=lambda sig: (
            0 if sig.source == "pfr_farm" else 1,
            0 if sig.status == "opened_paper" else 1,
            -float(sig.created_at or 0.0),
        )
    )
    return [_item(sig) for sig in matches[: max(0, limit)]]


def _price(value: Any) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{num:.8g}"


def _zone(entry_zone: list[float]) -> str:
    if len(entry_zone) != 2:
        return "n/a"
    return f"{_price(entry_zone[0])}-{_price(entry_zone[1])}"


def _tp1(plan: list[dict]) -> str:
    if not plan:
        return "n/a"
    first = plan[0] if isinstance(plan[0], dict) else {}
    return _price(first.get("price"))


def render_manual_farm_context(items: list[ManualFarmContextItem]) -> str:
    if not items:
        return ""
    lines = [
        "Контекст paper-фермы:",
    ]
    for item in items:
        readiness = "PFR" if item.ready_strategy_id else "farm"
        lines.append(
            "• "
            f"{readiness}/{item.source}: {item.okx_inst_id or item.symbol} · "
            f"{item.timeframe} · {item.side.upper()} · {item.status}; "
            f"вход {_zone(item.entry_zone)}, стоп {_price(item.stop_loss)}, tp1 {_tp1(item.take_profit_plan)}."
        )
    lines.extend(
        [
            "",
            "Важно: ручной анализ выше — отдельный снимок текущего рынка. "
            "Если он пишет, что нового входа нет, это не отменяет уже заведенную paper-идею farm/PFR; "
            "ее жизненный цикл и исход ведутся отдельно.",
            "Это paper-контекст, не приказ к сделке.",
        ]
    )
    return "\n".join(lines)


def manual_farm_context_text(
    symbol: str,
    *,
    private_root: str | Path | None = None,
    limit: int = 3,
) -> str:
    return render_manual_farm_context(
        active_farm_context_for_symbol(symbol, private_root=private_root, limit=limit)
    )
