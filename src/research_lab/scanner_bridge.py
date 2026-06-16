# -*- coding: utf-8 -*-
"""Scanner-to-farm bridge: turn scanner WATCH/GO events into bounded sweep specs.

The news scanner emits WATCH/GO rows into ``logs/scout/watch_queue.jsonl`` (NO_GO
is never queued). This module converts one such scanner watch into a single,
tightly bounded :class:`SweepSpec` that asks a research question: *given that the
scanner flagged this asset on an event, is there a tradeable technical setup on
its history?*

The scanner only chooses WHICH symbol to research. The sweep then explores a
small parameter grid on that symbol's candles using the normal no-lookahead
simulator (decision on bar idx-1, entry at open of idx). The news trigger is NOT
fed into the simulator as a price anchor -- it is recorded as provenance only.

This module is pure: it builds specs and provenance dicts. It never:
    * opens trades or touches the order engine,
    * makes paid LLM calls,
    * reads or writes the private root,
    * generates an unbounded sweep (every spec is capped here).

Reading the queue, checking data availability, compiling and queueing is done by
the CLI (``scripts/strategy_lab/generate_event_sweeps.py --from-scanner``),
dry-run by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.research_lab.research_plan import compatible_families
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.sweep_spec import SweepSpec

# Event-reactive families only. A flagged news event is a momentum/breakout
# context far more often than a mean-reversion one, so we probe breakouts.
DEFAULT_FAMILIES: tuple[str, ...] = ("momentum_breakout", "donchian_breakout", "breakout_retest")

# Hard caps so a scanner burst can never expand into a large farm load.
MAX_VARIANTS_CAP = 12
MAX_SYMBOLS_CAP = 8

# Bridge result statuses.
STATUS_OK = "ok"
STATUS_NO_ASSET = "no_asset"
STATUS_NO_FAMILY = "no_compatible_family"


@dataclass(frozen=True)
class BridgeResult:
    """Outcome of converting one scanner watch into a sweep request."""

    status: str
    symbol: str
    sweep: SweepSpec | None
    context: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK and self.sweep is not None


def _trimmed(value: Any, limit: int = 240) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def scanner_trigger_context(watch: dict[str, Any]) -> dict[str, Any]:
    """Public-safe provenance for a scanner-driven sweep (no profitability claim)."""
    scanner = watch.get("scanner") or {}
    asset = watch.get("asset") or {}
    trigger = watch.get("trigger") or {}
    return {
        "origin": "scanner_watch",
        "watch_id": watch.get("watch_id"),
        "card_id": watch.get("card_id"),
        "event_key": watch.get("event_key"),
        "verdict": scanner.get("verdict"),
        "layer": scanner.get("layer"),
        "scanner_side": scanner.get("normalized_side") or scanner.get("side"),
        "event_type": scanner.get("event_type"),
        "lead_class": scanner.get("lead_class"),
        "symbol": asset.get("symbol"),
        "okx_inst": asset.get("okx_inst"),
        "source": _trimmed(trigger.get("source"), 80),
        "headline": _trimmed(trigger.get("headline")),
        "created_at": watch.get("created_at"),
        "note": "scanner picked the symbol; sweep runs no-lookahead history; not a profitability claim",
    }


def _resolve_symbol(watch: dict[str, Any]) -> str:
    """Tradeable instrument to research: prefer the resolved OKX perp instrument."""
    asset = watch.get("asset") or {}
    inst = str(asset.get("okx_inst") or "").strip()
    if inst:
        return inst
    symbol = str(asset.get("symbol") or "").strip()
    return symbol


def watch_to_sweep(
    watch: dict[str, Any],
    *,
    timeframe: str = "1d",
    families: tuple[str, ...] = DEFAULT_FAMILIES,
    max_variants: int = 8,
    backend: str = "auto",
) -> BridgeResult:
    """Build one bounded SweepSpec from a single scanner watch row.

    Returns a :class:`BridgeResult`. On any structural problem (no asset, no
    family compatible with the timeframe) it returns a non-``ok`` result with a
    reason in ``status`` instead of raising -- callers must not crash on one bad
    row.
    """
    context = scanner_trigger_context(watch)
    symbol = _resolve_symbol(watch)
    if not symbol:
        return BridgeResult(STATUS_NO_ASSET, "", None, context)

    usable = compatible_families(timeframe, families)
    if not usable:
        return BridgeResult(STATUS_NO_FAMILY, symbol, None, context)
    family = usable[0]

    defaults = dict(get_strategy(family).parameter_defaults)
    base_lookback = int(defaults.get("lookback", 20))
    base_hold = int(defaults.get("hold_bars", 5))
    setup_grid = {"lookback": sorted({max(2, base_lookback // 2), base_lookback})}
    exit_grid = {"hold_bars": sorted({base_hold, max(2, base_hold // 2)})}

    capped_variants = min(max(1, int(max_variants)), MAX_VARIANTS_CAP)
    watch_id = str(watch.get("watch_id") or context.get("card_id") or symbol).replace("/", "_")
    sweep = SweepSpec(
        sweep_id=f"scan_{watch_id}_{family}",
        anchor_symbol=symbol,
        related_symbols=(),  # scanner watch is single-asset; baseline is for outcome only
        timeframe=timeframe,
        setup_family=family,
        setup_grid=setup_grid,
        exit_grid=exit_grid,
        max_variants=capped_variants,
        backend=backend,
        resource_class="normal",
        private_output_policy="private_only",
    )
    return BridgeResult(STATUS_OK, symbol, sweep, context)


def watches_to_sweeps(
    watches: list[dict[str, Any]],
    *,
    timeframe: str = "1d",
    families: tuple[str, ...] = DEFAULT_FAMILIES,
    max_variants: int = 8,
    max_symbols: int = MAX_SYMBOLS_CAP,
    backend: str = "auto",
) -> list[BridgeResult]:
    """Convert many scanner watches into bounded sweep results, capped by count.

    De-duplicates by resolved symbol (the first watch for a symbol wins) and caps
    the number of distinct symbols so a scanner burst cannot flood the farm.
    """
    symbol_cap = min(max(1, int(max_symbols)), MAX_SYMBOLS_CAP)
    results: list[BridgeResult] = []
    seen: set[str] = set()
    for watch in watches:
        result = watch_to_sweep(
            watch, timeframe=timeframe, families=families, max_variants=max_variants,
            backend=backend,
        )
        key = result.symbol.upper()
        if result.ok:
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > symbol_cap:
                seen.discard(key)
                break
        results.append(result)
    return results
