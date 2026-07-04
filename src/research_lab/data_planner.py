# -*- coding: utf-8 -*-
"""Data Planner — decide, BEFORE compute, what work a symbol actually needs.

Pure planning over an injected ``data_state(symbol, tf)`` probe (so it is fully
testable with no filesystem). For each eligible (timeframe, family) it returns a
machine-readable decision instead of blindly preparing/queueing:

  * timeframe not allowed for the asset class            -> skip
  * candles missing                                       -> prepare_data
  * candles present but too short / fresh listing         -> defer (until a time)
  * funding family without a funding field                -> enrich_funding (+ gated run_sweep)
  * OI / microstructure family without a data slot        -> run_sweep BLOCKED (NEEDS_*_DATA)
  * everything ready                                      -> run_sweep (carries data_fingerprint)

The coordinator turns these decisions into farm_tasks (wiring depends_on / blocked
state). No fetch, no compute, no order path here.
"""
from __future__ import annotations

from typing import Any, Callable

from src.research_lab.data_readiness_selector import min_rows_for, timeframes_for_asset_class
from src.research_lab.research_plan import compatible_families
from src.research_lab.strategy_registry import get_strategy

# Seconds per bar for the sweepable timeframes (1m is trigger-only, never swept).
_TF_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
_FARM_TIMEFRAMES = ("15m", "1h", "4h", "1d")
_MIN_DEFER = 3600          # never recheck a too-short symbol more than hourly
_MAX_DEFER = 7 * 86400     # but always recheck within a week

# Decision is a plain dict; these are the action vocab + machine reasons.
DataState = Callable[[str, str], dict[str, Any]]


def eligible_farm_timeframes(asset_class: str | None) -> list[str]:
    """Asset-class timeframes intersected with the farm-sweepable set (config-driven)."""
    allowed = timeframes_for_asset_class(asset_class)
    return [tf for tf in allowed if tf.lower() in _FARM_TIMEFRAMES]


def _defer_until(now: float, timeframe: str, rows: int) -> float:
    """When enough fresh bars should exist to retry a too-short symbol."""
    need = min_rows_for(timeframe)
    interval = _TF_SECONDS.get(timeframe.lower(), 3600)
    wait = max(_MIN_DEFER, (need - max(0, rows)) * interval)
    return now + min(_MAX_DEFER, wait)


def _decision(action: str, *, symbol: str, timeframe: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"action": action, "symbol": symbol, "timeframe": timeframe, "reason": reason, **extra}


def _family_decision(symbol: str, tf: str, fam: str, state: dict[str, Any]) -> dict[str, Any]:
    """One ready (timeframe, family): run_sweep, enrich-first, or blocked on a data slot."""
    if not compatible_families(tf, (fam,)):
        return _decision("skip", symbol=symbol, timeframe=tf, family=fam, reason="tf_family_incompatible")
    required = set(get_strategy(fam).required_data)
    enrichment = set(state.get("enrichment") or ())
    if "microstructure" in required:
        return _decision("run_sweep", symbol=symbol, timeframe=tf, family=fam,
                         reason="NEEDS_MICRO_DATA", block=True)
    if "oi" in required and "oi" not in enrichment:
        # OI history is keyless-public: block the sweep AND request an enrich task that
        # forward-fills the oi field onto the candles, which then unblocks it.
        return _decision("run_sweep", symbol=symbol, timeframe=tf, family=fam,
                         reason="NEEDS_OI_DATA", block=True, needs_enrich="oi")
    if "funding" in required and "funding" not in enrichment:
        # Funding is keyless-public: block the sweep AND request an enrich task that
        # unblocks it once the funding field is forward-filled onto the file.
        return _decision("run_sweep", symbol=symbol, timeframe=tf, family=fam,
                         reason="NEEDS_FLOW_DATA", block=True, needs_enrich="funding")
    return _decision("run_sweep", symbol=symbol, timeframe=tf, family=fam,
                     reason="data_ready", data_fingerprint=state.get("fingerprint"))


def plan_symbol(
    symbol: str,
    asset_class: str | None,
    families: tuple[str, ...],
    *,
    data_state: DataState,
    now: float,
) -> list[dict[str, Any]]:
    """Return ordered decisions for one symbol across its eligible timeframes."""
    tfs = eligible_farm_timeframes(asset_class)
    if not tfs:
        return [_decision("skip", symbol=symbol, timeframe="", reason="no_eligible_timeframe")]
    decisions: list[dict[str, Any]] = []
    for tf in tfs:
        state = data_state(symbol, tf)
        status = str(state.get("status") or "missing")
        if status == "missing":
            decisions.append(_decision("prepare_data", symbol=symbol, timeframe=tf, reason="data_missing"))
            continue
        if status in ("too_short", "fresh_listing_pending"):
            decisions.append(_decision("defer", symbol=symbol, timeframe=tf, reason=status,
                                       deferred_until=_defer_until(now, tf, int(state.get("rows") or 0))))
            continue
        if status == "provider_error":
            decisions.append(_decision("defer", symbol=symbol, timeframe=tf, reason="provider_error",
                                       deferred_until=now + _MIN_DEFER))
            continue
        for fam in families:
            decisions.append(_family_decision(symbol, tf, fam, state))
    return decisions
