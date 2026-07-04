# -*- coding: utf-8 -*-
"""Bounded optional funding enrichment for the universe farm loop.

For each (symbol, timeframe) that has a prepared candle file, optionally fetch the
public OKX funding-rate-history and forward-fill it onto the file (adding a
``funding`` field the flow families read). Bounded by a per-cycle cap, a success TTL
(don't re-fetch a file enriched recently), and a per-file failure backoff (don't
hammer OKX on a file that keeps failing). Dry-run never calls the provider.

Persisted status per (symbol, timeframe, data_file): would_enrich (dry-run only,
not persisted) / enriched / no_points / fetch_failed. skipped_cap,
already_enriched_recently and skipped_backoff are per-cycle outcomes. Paper/research
only; public funding endpoint only; no keys, no orders, no private endpoints.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.experiment import choose_symbol_file
from src.research_lab.flow_merge import merge_funding
from src.research_lab.paths import market_data_glob, resolve_private_root

DEFAULT_TTL_SECONDS = 12 * 3600
DEFAULT_COOLDOWN_SECONDS = 6 * 3600
SMOKE_SYMBOLS = 3
COUNTER_KEYS = ("would_enrich", "enriched", "no_points", "fetch_failed",
                "skipped_cap", "already_enriched_recently", "skipped_backoff")
_PERSISTED_RECENT = {"enriched", "no_points"}


@dataclass
class FlowEnrichState:
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "FlowEnrichState":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(entries=dict(data.get("entries") or {}))
        except (OSError, json.JSONDecodeError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"entries": self.entries}, ensure_ascii=False), encoding="utf-8")

    def recent_success(self, key: str, now_ms: int, ttl_seconds: int) -> bool:
        e = self.entries.get(key)
        return bool(e and e.get("status") in _PERSISTED_RECENT
                    and now_ms - int(e.get("ts_ms") or 0) < ttl_seconds * 1000)

    def backed_off(self, key: str, now_ms: int, max_attempts: int, cooldown_seconds: int) -> bool:
        e = self.entries.get(key)
        return bool(e and e.get("status") == "fetch_failed"
                    and int(e.get("attempts") or 0) >= max_attempts
                    and now_ms - int(e.get("ts_ms") or 0) < cooldown_seconds * 1000)

    def record(self, key: str, status: str, points: int, now_ms: int, *, fail: bool = False) -> None:
        prev = int(self.entries.get(key, {}).get("attempts") or 0)
        self.entries[key] = {"status": status, "funding_points": int(points),
                             "ts_ms": int(now_ms), "attempts": prev + 1 if fail else 0}


def _symbol_cap(profile: Any, full: bool) -> int:
    return profile.max_symbols_per_cycle if full else min(profile.max_symbols_per_cycle, SMOKE_SYMBOLS)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = [r for r in data if isinstance(r, dict) and "ts" in r] if isinstance(data, list) else []
    return sorted(rows, key=lambda r: int(r["ts"]))


def _enrich_one(path: Path, key: str, symbol: str, *, provider, state, now_ms, ttl_seconds,
                cooldown_seconds, max_attempts, budget, counters) -> int:
    if state.recent_success(key, now_ms, ttl_seconds):
        counters["already_enriched_recently"] += 1
        return budget
    if state.backed_off(key, now_ms, max_attempts, cooldown_seconds):
        counters["skipped_backoff"] += 1
        return budget
    if budget <= 0:
        counters["skipped_cap"] += 1
        return budget
    rows = _read_rows(path)
    if not rows:
        counters["fetch_failed"] += 1
        state.record(key, "fetch_failed", 0, now_ms, fail=True)
        return budget
    try:
        points = provider.fetch_funding(symbol, int(rows[0]["ts"]), int(rows[-1]["ts"]))
    except Exception:  # noqa: BLE001 - provider/network error must never crash the loop
        counters["fetch_failed"] += 1
        state.record(key, "fetch_failed", 0, now_ms, fail=True)
        return budget
    if not points:
        counters["no_points"] += 1
        state.record(key, "no_points", 0, now_ms)
        return budget
    path.write_text(json.dumps(merge_funding(rows, points), ensure_ascii=False), encoding="utf-8")
    counters["enriched"] += 1
    state.record(key, "enriched", len(points), now_ms)
    return budget - 1


DEFAULT_OI_MIN_COVERAGE = 0.5


def enrich_oi_one(
    path: Path, symbol: str, timeframe: str, *, provider, now_ms: int,
    min_coverage: float = DEFAULT_OI_MIN_COVERAGE,
) -> tuple[str, int]:
    """Fetch public OI history and forward-fill an ``oi`` field onto a candle file.

    Mirrors funding enrichment but for open interest (public, keyless). Returns
    (status, points):

      * ``enriched``            — OI merged with at least ``min_coverage`` of candles covered;
      * ``no_candles``          — the candle file is empty/too short;
      * ``fetch_failed``        — provider/network error (never raised);
      * ``no_points``           — provider returned no OI for this instrument;
      * ``not_enough_coverage`` — OI points exist but cover < ``min_coverage`` of the window.

    Critically, OI is written onto the file ONLY when coverage is sufficient, so a sweep
    is never marked OI-ready with near-zero merged coverage. OI is optional context — a
    fetch failure is reported, never raised, so the loop proceeds.
    """
    from src.research_lab.flow_merge import coverage as _coverage
    from src.research_lab.flow_merge import merge_oi
    rows = _read_rows(path)
    if not rows:
        return "no_candles", 0
    try:
        points = provider.fetch_open_interest(symbol, timeframe, int(rows[0]["ts"]), int(rows[-1]["ts"]))
    except Exception:  # noqa: BLE001 - provider/network error must never crash the loop
        return "fetch_failed", 0
    if not points:
        return "no_points", 0
    merged = merge_oi(rows, points)
    covered_frac = _coverage(merged, "oi")["coverage_pct"] / 100.0
    if covered_frac < min_coverage:
        # Do NOT write a barely-covered OI field: keep the sweep honestly blocked
        # rather than marking it OI-ready with near-zero merged coverage.
        return "not_enough_coverage", len(points)
    path.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    return "enriched", len(points)


def run_flow_enrich(
    units: list,
    *,
    universe,
    profiles,
    private_root: Path,
    provider,
    state: FlowEnrichState,
    apply: bool,
    now_ms: int,
    max_enrich: int = 4,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    max_attempts: int = 3,
    full: bool = False,
    allow_public_output: bool = False,
) -> dict[str, Any]:
    """Enrich prepared files for the given units' symbols with funding (bounded)."""
    private_root = resolve_private_root(Path(private_root), allow_public_output=allow_public_output) \
        if apply else Path(private_root)
    counters = {k: 0 for k in COUNTER_KEYS}
    budget = max_enrich
    seen: set[str] = set()
    for unit in units:
        cap = _symbol_cap(profiles.get(unit.timeframe), full)
        glob = market_data_glob(private_root, unit.timeframe)
        for symbol in list(universe.symbols_in(unit.group))[:cap]:
            path = choose_symbol_file(glob, symbol, timeframe=unit.timeframe)
            if not path:
                continue
            key = f"{symbol}::{unit.timeframe}::{path.name}"
            if key in seen:
                continue
            seen.add(key)
            if not apply:
                counters["would_enrich"] += 1
                continue
            budget = _enrich_one(path, key, symbol, provider=provider, state=state, now_ms=now_ms,
                                 ttl_seconds=ttl_seconds, cooldown_seconds=cooldown_seconds,
                                 max_attempts=max_attempts, budget=budget, counters=counters)
    return {"counters": counters}
