# -*- coding: utf-8 -*-
"""PaperActionSignal — the operational paper-watch contract (research-only, never an order).

A signal is a human- and machine-readable WATCH card with everything needed to act on paper and to
review the outcome later. It is NOT an order and NOT a live trade. `validate_signal` is the deterministic
gate every signal must pass before it is stored or delivered: an LLM (or any non-deterministic source)
can propose context, but it can never mint a final signal that skips these checks.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SOURCES = ("farm", "scanner", "main", "manual", "tactical")
SIDES = ("long", "short")
STATUSES = ("candidate", "armed", "invalidated", "opened_paper", "closed_paper", "expired", "reviewed")


@dataclass
class PaperActionSignal:
    signal_id: str
    source: str
    symbol: str
    okx_inst_id: str
    timeframe: str
    side: str
    setup_family: str
    entry_zone: list[float]           # [low, high] price band to act in
    stop_loss: float                  # invalidation price
    invalidation_rule: str            # human text: what cancels the thesis
    take_profit_plan: list[dict[str, Any]]   # [{"price": p, "size_frac": f, "label": "tp1"}...]
    max_hold_bars: int
    max_hold_minutes: int
    reason_now: str                   # why this is actionable NOW
    risk_notes: str = ""
    validator_context: dict[str, Any] = field(default_factory=dict)
    outcome_memory_context: dict[str, Any] = field(default_factory=dict)
    chart_context_ref: str = ""
    status: str = "candidate"
    created_at: float = 0.0
    expires_at: float = 0.0
    ref_price: float = 0.0            # price at creation (for entry-zone framing)
    risk_pct: float = 0.0             # entry->stop distance as % of price (1R, for sizing/diagnosis)
    boundary_ts: int = 0              # last bar ts at creation; outcome is measured on bars after this
    data_fingerprint: str = ""        # hash of the decision window -> new bars => new fingerprint
    dedup_key: str = ""               # symbol|tf|family -> identity across re-runs (no duplicate spam)
    mode: str = "live"                # "live" (boundary=now, forward) | "replay" (historical-tail diagnostic)
    outcome: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PaperActionSignal":
        known = {f for f in cls.__dataclass_fields__}  # noqa: F821 - dataclass attr
        return cls(**{k: v for k, v in d.items() if k in known})


def validate_signal(sig: PaperActionSignal) -> tuple[bool, list[str]]:
    """Deterministic gate. Returns (ok, problems). A signal that fails is never stored/delivered.
    This is the wall an LLM cannot get around: the numbers must be internally consistent."""
    p: list[str] = []
    if sig.source not in SOURCES:
        p.append(f"bad source {sig.source!r}")
    if sig.side not in SIDES:
        p.append(f"bad side {sig.side!r}")
    if sig.status not in STATUSES:
        p.append(f"bad status {sig.status!r}")
    if not (sig.symbol and sig.okx_inst_id and sig.timeframe and sig.setup_family):
        p.append("missing identity (symbol/inst/timeframe/family)")
    lo, hi = (sig.entry_zone + [0, 0])[:2] if sig.entry_zone else (0, 0)
    if not sig.entry_zone or len(sig.entry_zone) != 2 or lo <= 0 or hi <= 0 or lo > hi:
        p.append(f"bad entry_zone {sig.entry_zone}")
    if sig.stop_loss <= 0:
        p.append("stop_loss must be > 0")
    # stop must sit on the LOSS side of the entry zone (below for long, above for short)
    if sig.side == "long" and sig.stop_loss >= lo:
        p.append("long stop must be below entry zone")
    if sig.side == "short" and sig.stop_loss <= hi:
        p.append("short stop must be above entry zone")
    if not sig.take_profit_plan:
        p.append("empty take_profit_plan")
    for tp in sig.take_profit_plan:
        tp_price = float(tp.get("price", 0))
        if sig.side == "long" and tp_price <= hi:
            p.append(f"long TP {tp_price} must be above entry zone")
        if sig.side == "short" and tp_price >= lo:
            p.append(f"short TP {tp_price} must be below entry zone")
    if sig.max_hold_bars <= 0:
        p.append("max_hold_bars must be > 0")
    if sig.expires_at and sig.created_at and sig.expires_at <= sig.created_at:
        p.append("expires_at must be after created_at")
    if not sig.reason_now:
        p.append("reason_now required (why now)")
    if not sig.invalidation_rule:
        p.append("invalidation_rule required (what cancels it)")
    return (not p), p


def render_card(sig: PaperActionSignal) -> str:
    """Human-readable paper-watch card (the format the trader asked for). Pure string; no side effects."""
    tps = " · ".join(f"{t.get('label', 'tp')}={t.get('price')}" for t in sig.take_profit_plan)
    zone = f"{sig.entry_zone[0]}-{sig.entry_zone[1]}" if sig.entry_zone else "?"
    lines = [
        f"{sig.okx_inst_id} · {sig.timeframe} · {sig.side.upper()} · PAPER WATCH ({sig.status})",
        f"Reason now: {sig.reason_now}",
        f"Entry zone: {zone}",
        f"Invalidation: {sig.invalidation_rule}",
        f"TP plan: {tps}",
        f"Stop: {sig.stop_loss}   Max hold: {sig.max_hold_bars} bars (~{sig.max_hold_minutes} min)",
        f"Setup: {sig.setup_family}   Source: {sig.source}",
    ]
    if sig.risk_notes:
        lines.append(f"Risk: {sig.risk_notes}")
    if sig.outcome:
        lines.append(f"Outcome: {sig.outcome.get('result', 'pending')} "
                     f"net%={sig.outcome.get('net_pct', '-')}")
    if sig.review.get("diagnosis"):
        lines.append(f"Review: {sig.review['diagnosis']}")
    lines.append("NOTE: paper/research-only — NOT an order, NOT a live trade.")
    return "\n".join(lines)
