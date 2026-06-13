# -*- coding: utf-8 -*-
"""Derive the 1m data slices the lab actually needs — bounded and explicit.

A DataRequirement is one capped 1m window for one symbol, derived from an event
context (event sweeps / queued specs) or from an explicit manual request. This
module only *describes* what data is needed; it never downloads and never decides
trades (no look-ahead — it is pure data preparation). All timestamps are UTC ms.

Resource policy caps (via MicroscopeLimits) bound the result: at most
`max_event_windows` distinct windows and `max_symbols` distinct symbols; anything
beyond is marked `outside_policy` so the operator can see what was dropped.
"""

from __future__ import annotations

import datetime as dt
import glob
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.research_lab.event_microscope import MicroscopeLimits

MINUTE_MS = 60_000
DEFAULT_PAD_BARS = 30  # ~30 1m bars of context on each side of the event, before clipping

# Status candidates
CANDIDATE = "candidate"
PRESENT = "present"
MISSING = "missing"
TOO_SHORT = "too_short"
MALFORMED = "malformed"
OUTSIDE_POLICY = "outside_policy"

# Source types
SRC_EVENT_SWEEP = "event_sweep"
SRC_QUEUED_JOB = "queued_job"
SRC_MANUAL = "manual"

# Spec dirs (relative to the private root) that may carry event_context.
_SPEC_DIRS = ("plans/event_specs", "plans/specs", "proposals/queued_specs")


@dataclass(frozen=True)
class DataRequirement:
    symbol: str
    timeframe: str  # always "1m" here
    start_ts: int   # UTC ms, minute-aligned
    end_ts: int     # UTC ms, minute-aligned
    reason: str
    source_type: str
    max_bars: int
    status: str = CANDIDATE

    def bar_count(self) -> int:
        return (self.end_ts - self.start_ts) // MINUTE_MS + 1

    def key(self) -> tuple[str, int, int]:
        return (self.symbol, self.start_ts, self.end_ts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "start_utc": _ms_to_iso(self.start_ts),
            "end_utc": _ms_to_iso(self.end_ts),
            "bars": self.bar_count(),
            "reason": self.reason,
            "source_type": self.source_type,
            "max_bars": self.max_bars,
            "status": self.status,
        }


def parse_utc_to_ms(value: str) -> int:
    """Parse an ISO datetime (UTC assumed when no tz) to epoch ms."""
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp() * 1000)


def _ms_to_iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(int(ms) / 1000, tz=dt.timezone.utc).isoformat()


def _floor_minute(ms: int) -> int:
    return int(ms) - (int(ms) % MINUTE_MS)


def _bounded_window(start_ts: int, end_ts: int, max_bars: int, *, pad_bars: int) -> tuple[int, int]:
    """Pad a [start,end] window then clip it to max_bars 1m bars, minute-aligned."""
    win_start = _floor_minute(start_ts - pad_bars * MINUTE_MS)
    win_end = _floor_minute(end_ts + pad_bars * MINUTE_MS)
    if win_end < win_start:
        win_end = win_start
    cap = max(1, int(max_bars))
    if (win_end - win_start) // MINUTE_MS + 1 > cap:
        win_end = win_start + (cap - 1) * MINUTE_MS
    return win_start, win_end


def requirement_from_event(
    event_context: dict[str, Any],
    *,
    limits: MicroscopeLimits,
    reason: str,
    source_type: str,
    symbol: str | None = None,
    pad_bars: int = DEFAULT_PAD_BARS,
) -> DataRequirement | None:
    """Build one bounded 1m requirement from an event context, or None if unusable."""
    try:
        start = int(event_context["move_start_ts"])
        end = int(event_context["move_end_ts"])
    except (KeyError, TypeError, ValueError):
        return None
    sym = str(symbol or event_context.get("anchor_symbol") or "").strip()
    if not sym or end < start:
        return None
    max_bars = max(1, int(limits.max_bars_per_window))
    win_start, win_end = _bounded_window(start, end, max_bars, pad_bars=pad_bars)
    return DataRequirement(
        symbol=sym, timeframe="1m", start_ts=win_start, end_ts=win_end,
        reason=reason, source_type=source_type, max_bars=max_bars, status=CANDIDATE,
    )


def manual_requirement(
    symbol: str,
    start: str,
    end: str,
    *,
    limits: MicroscopeLimits,
) -> DataRequirement:
    """Build a bounded 1m requirement from explicit CLI args (UTC)."""
    start_ms = parse_utc_to_ms(start)
    end_ms = parse_utc_to_ms(end)
    if end_ms < start_ms:
        raise ValueError("end is before start")
    max_bars = max(1, int(limits.max_bars_per_window))
    win_start, win_end = _bounded_window(start_ms, end_ms, max_bars, pad_bars=0)
    return DataRequirement(
        symbol=str(symbol).strip(), timeframe="1m", start_ts=win_start, end_ts=win_end,
        reason="manual_request", source_type=SRC_MANUAL, max_bars=max_bars, status=CANDIDATE,
    )


def _load_spec_event_requirements(private_root: Path, limits: MicroscopeLimits) -> list[DataRequirement]:
    out: list[DataRequirement] = []
    for rel in _SPEC_DIRS:
        for path in sorted(glob.glob(str(private_root / rel / "*.json"))):
            try:
                spec = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            ctx = spec.get("event_context") if isinstance(spec, dict) else None
            if not isinstance(ctx, dict) or not ctx:
                continue
            reason = f"event:{spec.get('experiment_id', Path(path).stem)}"
            symbols = spec.get("symbols") or [ctx.get("anchor_symbol")]
            for sym in symbols:
                req = requirement_from_event(
                    ctx, limits=limits, reason=reason, source_type=SRC_EVENT_SWEEP, symbol=sym,
                )
                if req is not None:
                    out.append(req)
    return out


def apply_policy_caps(reqs: list[DataRequirement], limits: MicroscopeLimits) -> list[DataRequirement]:
    """Dedup and mark requirements beyond the window/symbol caps as outside_policy."""
    seen: set[tuple[str, int, int]] = set()
    deduped: list[DataRequirement] = []
    for req in reqs:
        if req.key() in seen:
            continue
        seen.add(req.key())
        deduped.append(req)

    max_windows = max(0, int(limits.max_event_windows))
    max_symbols = max(0, int(limits.max_symbols))
    kept_windows: list[tuple[int, int]] = []
    kept_symbols: list[str] = []
    out: list[DataRequirement] = []
    for req in deduped:
        window = (req.start_ts, req.end_ts)
        if window not in kept_windows:
            if len(kept_windows) >= max_windows:
                out.append(replace(req, status=OUTSIDE_POLICY))
                continue
            kept_windows.append(window)
        if req.symbol not in kept_symbols:
            if len(kept_symbols) >= max_symbols:
                out.append(replace(req, status=OUTSIDE_POLICY))
                continue
            kept_symbols.append(req.symbol)
        out.append(req)
    return out


def collect_requirements(
    private_root: Path,
    *,
    limits: MicroscopeLimits,
    manual: DataRequirement | None = None,
) -> list[DataRequirement]:
    """Gather 1m requirements from event specs (+ optional manual), capped by policy."""
    reqs: list[DataRequirement] = []
    if manual is not None:
        reqs.append(manual)
    reqs.extend(_load_spec_event_requirements(private_root, limits))
    return apply_policy_caps(reqs, limits)
