# -*- coding: utf-8 -*-
"""1m event-microscope data path — read-only, opt-in, and capped.

The microscope inspects 1m candles around a single detected move on a few
high-volatility symbols. It NEVER downloads data and NEVER runs a full-universe
1m sweep: it only locates existing local 1m files and reports a clear status, so a
missing file is a clean SKIPPED reason, not a crash.

All limits come from the 1m timeframe profile (trigger-only) and the resource
policy. Output is public-safe: file names only, never absolute paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.research_lab.data_inventory import inspect_file
from src.research_lab.experiment import choose_symbol_file
from src.research_lab.resource_policy import ResourcePolicy
from src.research_lab.timeframes import TimeframeProfiles

MICROSCOPE_TIMEFRAME = "1m"
DEFAULT_MAX_EVENT_WINDOWS = 3
MIN_BARS_FOR_MICROSCOPE = 120  # need at least ~2h of 1m bars to frame one event window

# Default 1m glob: a 1m-specific pattern so the daily archive does not match it.
# There is no downloader; if nothing matches, every symbol is reported missing.
DEFAULT_1M_GLOB = "scripts/analysis/research/_okxhist/ai_scanner_feasibility/{symbol}_*_1m*.json"


@dataclass(frozen=True)
class MicroscopeLimits:
    timeframe: str
    max_symbols: int
    max_event_windows: int
    max_bars_per_window: int
    max_variants: int
    enabled: bool
    disabled_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "max_symbols": self.max_symbols,
            "max_event_windows": self.max_event_windows,
            "max_bars_per_window": self.max_bars_per_window,
            "max_variants": self.max_variants,
            "enabled": self.enabled,
            "disabled_reason": self.disabled_reason,
        }


@dataclass(frozen=True)
class MicroscopeData:
    symbol: str
    status: str  # available | missing | too_short | not_1m
    rows: int
    timeframe: str
    file_label: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "rows": self.rows,
            "timeframe": self.timeframe,
            "file_label": self.file_label,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MicroscopePlan:
    limits: MicroscopeLimits
    data: tuple[MicroscopeData, ...]
    skipped_symbols: tuple[tuple[str, str], ...]

    def availability_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.data:
            counts[row.status] = counts.get(row.status, 0) + 1
        return counts

    def available(self) -> tuple[MicroscopeData, ...]:
        return tuple(d for d in self.data if d.status == "available")

    def to_summary(self) -> dict[str, Any]:
        """Public-safe summary (file labels only, no absolute paths)."""
        return {
            "timeframe": self.limits.timeframe,
            "enabled": self.limits.enabled,
            "disabled_reason": self.limits.disabled_reason,
            "limits": self.limits.to_dict(),
            "availability_counts": self.availability_counts(),
            "available_symbols": [d.symbol for d in self.available()],
            "skipped_reasons": [{"symbol": d.symbol, "reason": d.reason} for d in self.data if d.status != "available"],
            "over_symbol_cap": [s for s, _ in self.skipped_symbols],
        }


def derive_limits(
    profiles: TimeframeProfiles,
    policy: ResourcePolicy,
    *,
    timeframe: str = MICROSCOPE_TIMEFRAME,
) -> MicroscopeLimits:
    """Build microscope limits from the trigger-only timeframe profile and policy."""
    profile = profiles.get(timeframe)
    enabled, reason = True, ""
    if not profile.trigger_only:
        enabled, reason = False, "timeframe_not_trigger_only"
    elif not policy.allows_1m("event_1m"):
        enabled, reason = False, "resource_policy_blocks_1m"
    window_hours = profile.max_window_hours if profile.max_window_hours is not None else 6
    event_windows = profile.max_event_windows if profile.max_event_windows is not None else DEFAULT_MAX_EVENT_WINDOWS
    return MicroscopeLimits(
        timeframe=timeframe,
        max_symbols=int(profile.max_symbols_per_cycle),
        max_event_windows=int(event_windows),
        max_bars_per_window=int(window_hours) * 60,
        max_variants=int(profile.max_variants_per_setup),
        enabled=enabled,
        disabled_reason=reason,
    )


def locate_microscope_data(
    data_glob: str,
    symbol: str,
    *,
    min_bars: int = MIN_BARS_FOR_MICROSCOPE,
) -> MicroscopeData:
    """Locate one symbol's 1m file. Missing/short/non-1m all return a clear status."""
    path = choose_symbol_file(data_glob, symbol)
    if path is None:
        return MicroscopeData(symbol, "missing", 0, "unknown", "", "no_1m_file")
    info = inspect_file(path)
    rows = int(info.get("rows") or 0)
    tf = str(info.get("timeframe") or "unknown")
    label = str(info.get("source_file_label") or path.name)
    if tf != MICROSCOPE_TIMEFRAME:
        return MicroscopeData(symbol, "not_1m", rows, tf, label, f"timeframe_is_{tf}")
    if rows < int(min_bars):
        return MicroscopeData(symbol, "too_short", rows, tf, label, f"need_at_least_{int(min_bars)}_bars")
    return MicroscopeData(symbol, "available", rows, tf, label, "")


def plan_microscope(
    symbols: list[str],
    *,
    data_glob: str = DEFAULT_1M_GLOB,
    profiles: TimeframeProfiles,
    policy: ResourcePolicy,
    timeframe: str = MICROSCOPE_TIMEFRAME,
    min_bars: int = MIN_BARS_FOR_MICROSCOPE,
) -> MicroscopePlan:
    """Cap symbols to the profile limit, then locate each one's 1m data (read-only)."""
    limits = derive_limits(profiles, policy, timeframe=timeframe)
    selected = list(symbols)[: limits.max_symbols]
    skipped = tuple((s, "over_symbol_cap") for s in list(symbols)[limits.max_symbols:])
    data = tuple(locate_microscope_data(data_glob, s, min_bars=min_bars) for s in selected)
    return MicroscopePlan(limits=limits, data=data, skipped_symbols=skipped)
