# -*- coding: utf-8 -*-
"""Event-cluster foundation: turn historical moves into research events.

An EventCluster marks a single historical move on one anchor symbol and the
related symbols around it. It is the entry point for the next architecture
(event -> related assets -> timeframe profile -> setup -> entry timing).

This module only detects and labels moves. It does NOT compute pre-move features
itself; `pre_move_candles` returns bars strictly before the move so any feature
built later cannot accidentally use information from inside or after the move.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from src.research_lab.universe import Universe

Candle = dict[str, Any]

DEFAULT_WINDOW = 5
DEFAULT_MIN_MOVE_PCT = 8.0


@dataclass(frozen=True)
class EventCluster:
    event_id: str
    anchor_symbol: str
    related_symbols: tuple[str, ...]
    timeframe: str
    move_start_idx: int
    move_end_idx: int
    move_start_ts: int
    move_end_ts: int
    direction: str  # "up" | "down"
    move_pct: float
    pre_windows: tuple[int, ...]
    post_window: int
    source_reason: str


def detect_move_events(
    candles: list[Candle],
    *,
    anchor_symbol: str,
    timeframe: str,
    window: int = DEFAULT_WINDOW,
    min_move_pct: float = DEFAULT_MIN_MOVE_PCT,
    max_events: int = 0,
    pre_windows: tuple[int, ...] = (3, 5),
    post_window: int = 5,
) -> list[EventCluster]:
    """Detect non-overlapping close-to-close moves >= min_move_pct over `window`."""
    window = max(1, int(window))
    n = len(candles)
    events: list[EventCluster] = []
    i = window
    while i < n:
        start_idx = i - window
        start_close = float(candles[start_idx]["close"])
        end_close = float(candles[i]["close"])
        move_pct = (end_close / start_close - 1.0) * 100 if start_close > 0 else 0.0
        if abs(move_pct) >= min_move_pct:
            events.append(
                _build_event(
                    candles, anchor_symbol, timeframe, start_idx, i, move_pct,
                    pre_windows, post_window,
                )
            )
            if max_events and len(events) >= max_events:
                break
            i = i + window  # skip ahead so detected moves never overlap
            continue
        i += 1
    return events


def _build_event(
    candles: list[Candle],
    anchor_symbol: str,
    timeframe: str,
    start_idx: int,
    end_idx: int,
    move_pct: float,
    pre_windows: tuple[int, ...],
    post_window: int,
) -> EventCluster:
    start_ts = int(candles[start_idx]["ts"])
    end_ts = int(candles[end_idx]["ts"])
    direction = "up" if move_pct >= 0 else "down"
    return EventCluster(
        event_id=_event_id(anchor_symbol, timeframe, start_ts, end_ts, direction),
        anchor_symbol=anchor_symbol,
        related_symbols=(),
        timeframe=timeframe,
        move_start_idx=start_idx,
        move_end_idx=end_idx,
        move_start_ts=start_ts,
        move_end_ts=end_ts,
        direction=direction,
        move_pct=round(move_pct, 4),
        pre_windows=tuple(int(w) for w in pre_windows),
        post_window=int(post_window),
        source_reason=f"close_to_close_move>={DEFAULT_MIN_MOVE_PCT}",
    )


def attach_related(
    event: EventCluster,
    universe: Universe,
    *,
    relation: str | None = None,
    max_related: int | None = None,
) -> EventCluster:
    """Return a copy of the event with related symbols from the universe."""
    related = universe.related(event.anchor_symbol, relation=relation)
    if max_related is not None:
        related = related[: max(0, int(max_related))]
    return replace(event, related_symbols=related)


def pre_move_candles(candles: list[Candle], event: EventCluster, lookback: int) -> list[Candle]:
    """Bars strictly before the move start — safe for pre-move features (no lookahead)."""
    lookback = max(0, int(lookback))
    start = max(0, event.move_start_idx - lookback)
    return candles[start:event.move_start_idx]


def _event_id(anchor: str, timeframe: str, start_ts: int, end_ts: int, direction: str) -> str:
    raw = json.dumps(
        {"a": anchor, "tf": timeframe, "s": start_ts, "e": end_ts, "d": direction},
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
