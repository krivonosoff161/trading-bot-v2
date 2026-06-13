# -*- coding: utf-8 -*-
"""Event-driven sweep generator: turn historical moves into bounded research specs.

For each strong historical move (event cluster) we build a small SweepSpec that
asks a research question: could the entry have been caught earlier, and which
filters only "work" with hindsight? The event is purely historical; the sweep
runs the normal no-lookahead simulator (decision on bar idx-1, entry at open of
idx). Pre-move windows end strictly before the move-confirmation bar.

This module is pure: it detects events and emits SweepSpecs. Writing proposals to
the private root and queueing is done by the CLI, dry-run by default.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.event_cluster import EventCluster, attach_related, detect_move_events
from src.research_lab.research_plan import compatible_families
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.sweep_spec import SweepSpec
from src.research_lab.universe import Universe

Candle = dict[str, Any]
DEFAULT_FAMILIES = ("momentum_breakout", "donchian_breakout", "breakout_retest")


def event_to_sweep(
    event: EventCluster,
    *,
    setup_family: str,
    max_variants: int = 8,
) -> SweepSpec:
    """Build a bounded SweepSpec that probes earlier-entry params around one event."""
    defaults = dict(get_strategy(setup_family).parameter_defaults)
    base_lookback = int(defaults.get("lookback", 20))
    setup_grid = {"lookback": sorted({max(2, base_lookback // 2), base_lookback})}
    exit_grid = {"hold_bars": sorted({int(defaults.get("hold_bars", 5)), max(2, int(defaults.get("hold_bars", 5)) // 2)})}
    return SweepSpec(
        sweep_id=f"evt_{event.event_id}_{setup_family}",
        anchor_symbol=event.anchor_symbol,
        related_symbols=event.related_symbols,
        timeframe=event.timeframe,
        setup_family=setup_family,
        setup_grid=setup_grid,
        exit_grid=exit_grid,
        max_variants=max_variants,
        backend="cpu",
        resource_class="normal",
        private_output_policy="private_only",
    )


def build_event_sweeps(
    candles: list[Candle],
    *,
    anchor_symbol: str,
    timeframe: str,
    universe: Universe,
    window: int = 5,
    min_move_pct: float = 8.0,
    max_events: int = 5,
    max_related: int = 3,
    setup_families: tuple[str, ...] = DEFAULT_FAMILIES,
) -> list[tuple[EventCluster, SweepSpec]]:
    """Detect events on the anchor and emit one bounded sweep per event."""
    families = compatible_families(timeframe, setup_families)
    if not families:
        return []
    family = families[0]
    events = detect_move_events(
        candles, anchor_symbol=anchor_symbol, timeframe=timeframe,
        window=window, min_move_pct=min_move_pct, max_events=max_events,
    )
    out: list[tuple[EventCluster, SweepSpec]] = []
    for event in events:
        enriched = attach_related(event, universe, max_related=max_related)
        out.append((enriched, event_to_sweep(enriched, setup_family=family)))
    return out


def sweep_proposal_dict(event: EventCluster, sweep: SweepSpec) -> dict[str, Any]:
    """Public-safe proposal summary (no private data, no profitability claim)."""
    return {
        "sweep_id": sweep.sweep_id,
        "event_id": event.event_id,
        "anchor_symbol": event.anchor_symbol,
        "related_symbols": list(event.related_symbols),
        "timeframe": event.timeframe,
        "direction": event.direction,
        "move_pct": event.move_pct,
        "setup_family": sweep.setup_family,
        "pre_windows": list(event.pre_windows),
        "research_question": "could entry be earlier; which filters only work in hindsight",
        "note": "historical event; sweep runs the no-lookahead simulator; not a profitability claim",
    }
