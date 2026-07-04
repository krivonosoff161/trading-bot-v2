# -*- coding: utf-8 -*-
"""Decode farm lifecycle "tails" into structural, human-readable reasons (read-only, research-only).

farm_status_report shows blocked/deferred tasks as generic markers ("provider_error", "too_short",
"NEEDS_OI_DATA"). That hides WHY. These pure helpers turn a tail row into a structural reason and a
next-action so the operator sees "GEOD = no_instrument_or_delisted" instead of an opaque "provider_error".

No DB writes, no network, no execution path — classification only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_universe_symbols(private_root: Path) -> set[str]:
    """Read the live discovery snapshot and return the set of universe symbols (UPPER, normalized).
    Empty set if no snapshot — callers then treat provider errors as transient, never as delisted."""
    path = Path(private_root) / "discovery" / "live_universe.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - missing/corrupt snapshot is non-fatal
        return set()
    out: set[str] = set()
    for group_rows in (data.get("detail") or {}).values():
        for row in group_rows or []:
            for key in ("symbol", "inst_id"):
                v = row.get(key)
                if isinstance(v, str) and v:
                    out.add(v.replace("-", "_").upper())
    return out

# A 1d/4h sweep window of N days yields ~N (1d) or ~6N (4h) bars; below the readiness minimum it defers
# as "too_short" forever even for BTC/ETH — that is a fetch-window mismatch, NOT a fresh listing.
_LONG_TF = {"1d", "1D", "4h", "4H"}


def decode_provider_error(symbol: str, universe: set[str]) -> dict[str, str]:
    """A provider_error on a symbol absent from the live discovery universe is almost certainly a
    delisted/unknown instrument (no candles will ever come); otherwise it is a transient fetch failure."""
    sym = (symbol or "").upper()
    if sym and universe and sym not in universe:
        return {"reason": "no_instrument_or_delisted",
                "next_action": "drop_or_park_permanently (not on the live universe; no candles will arrive)"}
    return {"reason": "provider_fetch_failed_transient",
            "next_action": "bounded_backoff_then_retry (network/provider hiccup; self-heals)"}


def decode_too_short(symbol: str, timeframe: str, attempts: int) -> dict[str, str]:
    """Distinguish a fetch-window-too-small (long TF on a short window) from a genuinely fresh listing."""
    tf = (timeframe or "")
    if tf in _LONG_TF:
        return {"reason": "window_too_short_for_timeframe",
                "next_action": "widen_fetch_window_for_long_tf (e.g. 1d needs >= readiness_min days)"}
    if int(attempts or 0) <= 0:
        return {"reason": "fresh_listing_pending",
                "next_action": "deferred_until_more_bars (new listing; recheck on schedule)"}
    return {"reason": "data_incomplete_after_prepare",
            "next_action": "recheck_or_widen_window (prepare returned < readiness minimum)"}


def decode_needs_oi(count: int, *, oi_families_planned: bool) -> dict[str, Any]:
    """The NEEDS_OI_DATA orphans are unowned when the loop plans no OI families; mark them honestly so
    they stop reading as an eternal 'need OI' instead of a measured-or-parked terminal state."""
    if not oi_families_planned:
        return {"count": int(count), "reason": "oi_unmeasured_no_oi_families_planned",
                "next_action": "park_as_oi_unmeasured (enable OI lane only where 1h/4h OI is dense)"}
    return {"count": int(count), "reason": "oi_backfill_pending",
            "next_action": "bounded_oi_enrichment_then_unblock"}


def summarize_tails(blocked: list[dict[str, Any]], deferred: list[dict[str, Any]],
                    universe: set[str], needs_oi: int, *, oi_families_planned: bool = False) -> dict[str, Any]:
    """Turn raw blocked/deferred task rows into structural-reason buckets for the status report."""
    prov: dict[str, list[str]] = {}
    for t in blocked:
        if "provider_error" in str(t.get("machine_reason") or ""):
            d = decode_provider_error(str(t.get("symbol") or ""), universe)
            prov.setdefault(d["reason"], []).append(str(t.get("symbol") or "?"))
    short: dict[str, list[str]] = {}
    for t in deferred:
        if "too_short" in str(t.get("machine_reason") or ""):
            d = decode_too_short(str(t.get("symbol") or ""), str(t.get("timeframe") or ""),
                                 int(t.get("attempts") or 0))
            short.setdefault(d["reason"], []).append(f"{t.get('symbol')}/{t.get('timeframe')}")
    return {"provider_error": {k: sorted(set(v)) for k, v in prov.items()},
            "too_short": {k: sorted(set(v)) for k, v in short.items()},
            "needs_oi": decode_needs_oi(needs_oi, oi_families_planned=oi_families_planned)}
