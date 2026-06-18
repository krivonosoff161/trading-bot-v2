# -*- coding: utf-8 -*-
"""Universe-driven refill planner — the farm does not wait for news.

Pure planning (no DB, no network, no disk): turn the universe groups into a
deterministic, round-robin work-list of (group, timeframe, families) units that
systematically grind the whole universe across many families and timeframes. The
loop CLI (scripts/strategy_lab/universe_farm_loop.py) prepares data, queues the
multi-family research plans, and drains the worker.

Direction intent is modeled as families-per-group (the spec's "OI/funding
candidates", "BB/range/fade candidates", "main FAST/SWING candidates" are family
selections, not asset groups). Every family is a research hypothesis gated by
validation — never a proven edge.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.research_lab.data_prepare import MarketDataPrepareItem, timeframe_ms
from src.research_lab.research_plan import compatible_families
from src.research_lab.universe import Universe

MAX_BARS = 2000
DEFAULT_DATA_DAYS = {"15m": 14, "1h": 60, "4h": 180, "1d": 1000}

# Broad default family set (the new rich families); used when a group has no
# specific mapping. All are timeframe-filtered before planning.
FARM_FAMILIES: tuple[str, ...] = (
    "main_fast_swing_regime",
    "range_volume_breakout",
    "volatility_squeeze_breakout_v2",
    "vwap_reclaim_reject",
    "fvg_reclaim_reject",
    "fractal_swing_break_retest",
    "oi_price_quadrant_continuation",
    "oi_price_quadrant_trap_fade",
    "oi_funding_squeeze",
    "bb_volume_fade",
    "pump_dump_scalp",
)

# Direction-aware family preferences per universe asset group.
GROUP_FAMILIES: dict[str, tuple[str, ...]] = {
    "core_market": ("main_fast_swing_regime", "volatility_squeeze_breakout_v2",
                    "fractal_swing_break_retest", "vwap_reclaim_reject",
                    "oi_price_quadrant_continuation",
    "oi_price_quadrant_trap_fade", "oi_funding_squeeze"),
    "btc_eth_tactical": ("main_fast_swing_regime", "vwap_reclaim_reject", "fvg_reclaim_reject",
                         "oi_price_quadrant_continuation",
    "oi_price_quadrant_trap_fade", "oi_funding_squeeze", "fractal_swing_break_retest"),
    "l2_high_beta": ("main_fast_swing_regime", "range_volume_breakout",
                     "volatility_squeeze_breakout_v2", "pump_dump_scalp",
                     "oi_funding_squeeze", "fvg_reclaim_reject"),
    "meme_flow": ("pump_dump_scalp", "range_volume_breakout",
                  "volatility_squeeze_breakout_v2", "bb_volume_fade"),
    "ai_equity_proxy": ("main_fast_swing_regime", "fractal_swing_break_retest",
                        "vwap_reclaim_reject", "range_volume_breakout"),
}


@dataclass(frozen=True)
class RefillUnit:
    group: str
    timeframe: str
    families: tuple[str, ...]

    def key(self) -> str:
        return f"{self.group}::{self.timeframe}"


def families_for_group(group: str, timeframe: str) -> tuple[str, ...]:
    """Preferred families for a group, filtered to those valid on the timeframe."""
    wanted = GROUP_FAMILIES.get(group, FARM_FAMILIES)
    return tuple(compatible_families(timeframe, wanted))


def build_worklist(
    universe: Universe,
    *,
    groups: list[str] | None = None,
    timeframes: list[str] | None = None,
) -> list[RefillUnit]:
    """Round-robin (timeframe-major, group-minor) units with timeframe-valid families.

    Empty-after-filtering units are dropped. Ordering interleaves groups so a few
    units per cycle keep coverage fair across the whole universe over time.
    """
    groups = groups if groups is not None else list(universe.groups)
    timeframes = timeframes if timeframes is not None else ["1h", "4h", "1d", "15m"]
    units: list[RefillUnit] = []
    for timeframe in timeframes:
        for group in groups:
            if not universe.symbols_in(group):
                continue
            fams = families_for_group(group, timeframe)
            if fams:
                units.append(RefillUnit(group=group, timeframe=timeframe, families=fams))
    return units


def rotate_worklist(units: list[RefillUnit], cursor: int, take: int) -> tuple[list[RefillUnit], int]:
    """Pick ``take`` units starting at ``cursor`` (wrapping); return (units, next_cursor)."""
    if not units:
        return [], 0
    take = max(1, min(take, len(units)))
    start = cursor % len(units)
    selected = [units[(start + i) % len(units)] for i in range(take)]
    return selected, (start + take) % len(units)


def prepare_window(timeframe: str, now_ms: int, days_override: int | None = None) -> tuple[int, int]:
    """Bounded [start, end] ms window for a timeframe, capped to MAX_BARS."""
    tf = str(timeframe).lower()
    days = int(days_override) if days_override else DEFAULT_DATA_DAYS.get(tf, 60)
    interval = timeframe_ms(tf)
    end = int(now_ms)
    start = end - days * 86_400_000
    if (end - start) // interval + 1 > MAX_BARS:
        start = end - (MAX_BARS - 1) * interval
    return int(start), end


def prepare_items(
    symbols: list[str], timeframe: str, now_ms: int, days_override: int | None = None
) -> list[MarketDataPrepareItem]:
    start, end = prepare_window(timeframe, now_ms, days_override)
    return [MarketDataPrepareItem(symbol=s, timeframe=timeframe, start_ts=start, end_ts=end) for s in symbols]
