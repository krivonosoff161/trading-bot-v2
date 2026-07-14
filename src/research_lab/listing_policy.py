# -*- coding: utf-8 -*-
"""Deterministic history policy for young and unavailable instruments."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from src.research_lab.candle_store import normalize_timeframe

_TF_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}
_PENDING_STATES = {"preopen", "prelive", "test"}
_TERMINAL_STATES = {"suspend", "delisted", "expired"}


@dataclass(frozen=True)
class ListingWindow:
    status: str
    start_ts: int
    end_ts: int
    available_bars: int
    minimum_bars: int
    eligible_at_ms: int | None
    reason: str

    @property
    def may_fetch(self) -> bool:
        return self.status in {"eligible", "fresh_listing_pending"}

    @property
    def may_full_validate(self) -> bool:
        return self.status == "eligible"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_listing_window(
    timeframe: str,
    desired_start_ts: int,
    end_ts: int,
    *,
    minimum_bars: int,
    list_time_ms: int | None = None,
    state: str | None = None,
) -> ListingWindow:
    tf = normalize_timeframe(timeframe)
    interval = _TF_MS[tf]
    desired_start = int(desired_start_ts)
    end = int(end_ts)
    minimum = max(1, int(minimum_bars))
    token = str(state or "live").strip().lower()
    listed = int(list_time_ms) if list_time_ms is not None else None

    if token in _PENDING_STATES:
        return ListingWindow(
            "preopen", end, end, 0, minimum,
            (listed + (minimum - 1) * interval) if listed is not None else None,
            f"instrument_state:{token}",
        )
    if token in _TERMINAL_STATES:
        return ListingWindow(
            "invalid_instrument", end, end, 0, minimum, None,
            f"instrument_state:{token}",
        )
    if listed is not None and listed > end:
        return ListingWindow(
            "preopen", end, end, 0, minimum,
            listed + (minimum - 1) * interval,
            "list_time_is_in_future",
        )

    start = max(desired_start, listed) if listed is not None else desired_start
    available = max(0, (end - start) // interval + 1) if end >= start else 0
    eligible_at = (listed + (minimum - 1) * interval) if listed is not None else None
    if listed is not None and available < minimum:
        return ListingWindow(
            "fresh_listing_pending", start, end, available, minimum,
            eligible_at, f"available_bars:{available}<{minimum}",
        )
    return ListingWindow(
        "eligible", start, end, available, minimum, eligible_at,
        "history_window_available",
    )
