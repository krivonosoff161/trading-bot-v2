# -*- coding: utf-8 -*-
"""Operational PAPER signal lane (research-only, never a live order).

Turns the research farm's candidates + live movers into 3-5 human-readable, actionable PAPER-WATCH
signals (entry zone / stop / TP / invalidation / max-hold / reason-now / expiry), observes them to an
outcome on genuinely new bars, and produces a deterministic visual-review package.

Hard boundary: this lane NEVER places an order, never touches .env/AUTO_TRADE/private endpoints, and an
LLM can never mint a final signal — every signal passes a deterministic schema + checks gate. A
PaperActionSignal is a watch card, not a trade.
"""
from src.research_lab.paper_signals.contract import (
    PaperActionSignal,
    STATUSES,
    render_card,
    validate_signal,
)
from src.research_lab.paper_signals.store import append_signal, load_signals, update_signal

__all__ = [
    "PaperActionSignal", "STATUSES", "render_card", "validate_signal",
    "append_signal", "load_signals", "update_signal",
]
