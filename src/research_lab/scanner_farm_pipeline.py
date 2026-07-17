# -*- coding: utf-8 -*-
"""One cycle of the continuous scanner -> market-data -> farm coordinator.

Given the open scanner watches, this:
  1) plans prioritized, deduped, eligible jobs (farm_scheduler);
  2) auto-materializes the candles each eligible job needs (prepare_market_data,
     into the PRIVATE market_data/<tf>/ root) — not a manual operator step;
  3) compiles a bounded SweepSpec at the scanner-SELECTED timeframe and queues it
     against the private prepared-root glob (so the worker actually finds the data);
  4) records checkpoint + dedup + structured skip reasons + counters in PipelineState.

Pure-ish and injectable: the market-data ``provider`` is passed in (a fake provider
drives the whole prepare->queue path in tests with NO network). Dry-run plans and
classifies but writes nothing (no prepare, no queue, no state). Paper/research only:
no order engine, no private exchange endpoints, no .env, no AUTO_TRADE.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from src.research_lab.candle_store import CandleStore
from src.research_lab.candle_library import load_canonical_candles
from src.research_lab.data_inventory import inspect_file
from src.research_lab.data_prepare import MarketDataPrepareItem, prepare_market_data, timeframe_ms
from src.research_lab.data_readiness_selector import min_rows_for
from src.research_lab.experiment import choose_symbol_file
from src.research_lab.farm_scheduler import plan_jobs
from src.research_lab.paths import market_data_glob, resolve_private_root
from src.research_lab.pipeline_state import PipelineState
from src.research_lab.listing_policy import plan_listing_window
from src.research_lab.scanner_bridge import watch_to_sweep
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.strategy_requirements import derive_requirement

MAX_BARS = 18_000
DEFAULT_DATA_DAYS = {"15m": 30, "1h": 365, "4h": 730, "1d": 3650}
DEFAULT_REFILL_TIMEFRAME = "1h"


def event_spec_dir(private_root: Path) -> Path:
    return Path(private_root) / "plans" / "event_specs"


def _prepare_window(timeframe: str, now_ms: int, days_override: int | None) -> tuple[int, int]:
    tf = str(timeframe).lower()
    days = int(days_override) if days_override else DEFAULT_DATA_DAYS.get(tf, 60)
    interval = timeframe_ms(tf)
    end = int(now_ms)
    start = end - days * 86_400_000
    if (end - start) // interval + 1 > MAX_BARS:
        start = end - (MAX_BARS - 1) * interval
    return int(start), end


def _synth_backlog_watch(symbol: str, timeframe: str) -> dict:
    """A minimal eligible watch for a universe/backlog refill symbol (paper only)."""
    return {
        "watch_id": None,
        "created_at": "",
        "scanner": {"verdict": "WATCH", "event_type": "universe_backlog"},
        "trigger": {"source": "universe_backlog", "headline": f"backlog refill {symbol}"},
        "asset": {"symbol": symbol, "okx_inst": symbol, "okx_resolved": True},
        "farm": {"eligible": True, "selected_timeframe": timeframe, "data_readiness_status": "usable"},
    }


def _ensure_local_data(symbol: str, timeframe: str, *, private_root: Path, provider: Any,
                       apply: bool, now_ms: int, data_days: int | None,
                       prepares_left: int, allow_public_output: bool,
                       minimum_bars: int | None = None) -> tuple[str, int]:
    """Materialize candles if absent. Returns (status, rows).

    status: usable | would_prepare | prepared | too_short | missing_prepared_data
            | fresh_listing_pending | preopen | invalid_instrument | provider_error
            | provider_not_configured | data_prepare_failed | prepare_cap_reached
    """
    glob = market_data_glob(private_root, timeframe)
    desired_start, end = _prepare_window(timeframe, now_ms, data_days)
    store = CandleStore(private_root)
    passport = store.instrument(symbol) or {}
    listing = plan_listing_window(
        timeframe,
        desired_start,
        end,
        minimum_bars=max(min_rows_for(timeframe), int(minimum_bars or 0)),
        list_time_ms=passport.get("list_time_ms"),
        state=passport.get("state") or "live",
    )
    required_rows = max(min_rows_for(timeframe), int(minimum_bars or 0))
    if not listing.may_fetch:
        return listing.status, 0
    selected = load_canonical_candles(
        private_root, symbol, timeframe, fallback_glob=glob,
        purpose="scanner_farm_readiness", coverage_policy="gap_free",
    )
    if len(selected.rows) >= required_rows:
        return "usable", len(selected.rows)
    if not apply:
        return "would_prepare", 0
    if prepares_left <= 0:
        return "prepare_cap_reached", 0
    item = MarketDataPrepareItem(
        symbol=symbol,
        timeframe=timeframe,
        start_ts=listing.start_ts,
        end_ts=listing.end_ts,
        status=listing.status,
    )
    report = prepare_market_data([item], provider=provider, private_root=private_root,
                                 timeframe=timeframe, apply=True, allow_public_output=allow_public_output)
    if report.skipped:
        reason = str(report.skipped[0].get("reason") or "")
        if reason.startswith("provider_error"):
            item = report.items[0] if report.items else {}
            return ("provider_transient" if item.get("provider_error_transient") else "provider_error"), 0
        if reason == "no_data_returned":
            return "missing_prepared_data", 0
        if reason == "provider_not_configured":
            return "provider_not_configured", 0
        return "data_prepare_failed", 0
    selected = load_canonical_candles(
        private_root, symbol, timeframe, fallback_glob=glob,
        purpose="scanner_farm_post_prepare", coverage_policy="gap_free",
    )
    if selected.rows:
        rows = len(selected.rows)
        if not listing.may_full_validate:
            return "fresh_listing_pending", rows
        return (
            "prepared" if rows >= required_rows else "too_short"
        ), rows
    path = choose_symbol_file(glob, symbol, timeframe=timeframe)
    if path:
        rows = int(inspect_file(path).get("rows") or 0)
        if not listing.may_full_validate:
            return "fresh_listing_pending", rows
        return ("prepared" if rows >= required_rows else "too_short"), rows
    return "too_short", 0  # downloaded but not enough confirmed bars to be 'usable'


def _compile_and_queue(res, *, private_root: Path, profiles, policy, data_glob: str,
                       priority: int, conn,
                       data_snapshot_manifest: dict[str, Any]) -> tuple[str, bool]:
    """Compile the sweep at the prepared-root glob and idempotently queue it.

    Returns (experiment_id, created) — created is False when the farm queue already
    holds this spec (queued/running/completed), so the caller does not double-count it.
    """
    import json

    from src.research_lab.state_db import ensure_experiment_queued
    from src.research_lab.sweep_compile import compile_sweep

    exp = compile_sweep(res.sweep, data_glob=data_glob, timeframe_profiles=profiles,
                        resource_policy=policy, event_context=res.context)
    snapshot_id = str(data_snapshot_manifest.get("snapshot_id") or "")
    evidence_hash = str(data_snapshot_manifest.get("evidence_hash") or "")
    if not snapshot_id or not evidence_hash:
        raise ValueError("scanner farm queue requires a bound candle snapshot manifest")
    bound_experiment_id = f"{exp.experiment_id}__{snapshot_id[:16]}"
    out_dir = event_spec_dir(private_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = out_dir / f"{bound_experiment_id}.json"
    payload = {
        "experiment_id": bound_experiment_id, "data_glob": exp.data_glob, "symbols": exp.symbols,
        "timeframe": exp.timeframe, "families": exp.families, "fees_bps": exp.fees_bps,
        "slippage_bps": exp.slippage_bps, "min_trades": exp.min_trades, "split_ratio": exp.split_ratio,
        "max_runs": exp.max_runs, "parameter_grid": exp.parameter_grid, "filters": exp.filters,
        "event_context": exp.event_context, "backend": exp.backend,
        "data_fingerprint": evidence_hash,
        "data_snapshot_id": snapshot_id,
        "data_evidence_hash": evidence_hash,
    }
    spec_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _job_id, created = ensure_experiment_queued(conn, spec_path.resolve(), priority=int(priority))
    return bound_experiment_id, bool(created)


# Counter keys the loop adds on top of plan_jobs' watch-level triage counters.
_LOOP_COUNTERS = ("jobs_queued", "data_prepared", "would_prepare", "would_queue",
                  "already_queued", "prepare_failed", "prepare_deferred")
# prepare statuses that consumed a real download attempt (budget) — config errors do not.
_CONSUMES_PREPARE = {
    "prepared", "provider_error", "provider_transient", "too_short", "missing_prepared_data",
    "data_prepare_failed", "fresh_listing_pending",
}
_RETRYABLE_SKIP_REASONS = {
    "queue_cap_reached",
    "prepare_cap_reached",
    "provider_error",
    "provider_transient",
    "provider_not_configured",
    "missing_prepared_data",
    "data_prepare_failed",
    "too_short",
    "fresh_listing_pending",
    "preopen",
}


def _sweep_minimum_bars(sweep: Any) -> int:
    definition = get_strategy(sweep.setup_family)
    params = dict(definition.parameter_defaults)
    for grid in (sweep.setup_grid, sweep.exit_grid):
        for key, values in (grid or {}).items():
            numeric = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
            if numeric:
                params[key] = max(numeric)
    return derive_requirement(
        sweep.setup_family,
        sweep.anchor_symbol,
        sweep.timeframe,
        params=params,
    ).min_rows


def _process_job(job, *, state, watch_by_id, refill_timeframe, backend, apply, cycle,
                 private_root, provider, now_ms, data_days, prepares_left, profiles, policy,
                 priority_base, allow_public_output, conn) -> dict:
    """Materialize + queue one planned job. Does its own state writes (apply only).

    Returns an effects dict: per-counter deltas, optional decision/skip, and
    ``consumed_prepare`` so the caller can decrement the per-cycle prepare budget.
    """
    eff = {k: 0 for k in _LOOP_COUNTERS}
    eff.update(consumed_prepare=False, decision=None, skip=None)
    watch = watch_by_id.get(job.watch_id) or _synth_backlog_watch(job.symbol, job.timeframe or refill_timeframe)
    res = watch_to_sweep(watch, timeframe=(job.timeframe or refill_timeframe), backend=backend)
    if not res.ok:
        eff["skip"] = {"symbol": job.symbol, "reason": res.status, "timeframe": job.timeframe}
        return eff
    tf, family, inst = res.sweep.timeframe, res.sweep.setup_family, res.symbol
    job_key = f"{inst.upper()}::{tf}::{family}"
    if apply and state.is_job_queued(job_key):
        eff["already_queued"] = 1
        eff["skip"] = {"symbol": inst, "reason": "already_queued", "timeframe": tf}
        return eff
    status, rows = _ensure_local_data(inst, tf, private_root=private_root, provider=provider, apply=apply,
                                      now_ms=now_ms, data_days=data_days, prepares_left=prepares_left,
                                      allow_public_output=allow_public_output,
                                      minimum_bars=_sweep_minimum_bars(res.sweep))
    eff["consumed_prepare"] = status in _CONSUMES_PREPARE
    if status == "would_prepare":
        eff["would_prepare"] = eff["would_queue"] = 1
        eff["decision"] = {"symbol": inst, "timeframe": tf, "action": "would_prepare_and_queue"}
        return eff
    if status == "prepare_cap_reached":
        eff["prepare_deferred"] = 1
        eff["skip"] = {"symbol": inst, "reason": status, "timeframe": tf}
        return eff
    if status == "prepared":
        eff["data_prepared"] = 1
        if apply:
            state.record_prepared(inst, tf, rows=rows, cycle=cycle)
    elif status != "usable":
        eff["prepare_failed"] = 1
        eff["skip"] = {"symbol": inst, "reason": status, "timeframe": tf}
        return eff
    if not apply:
        eff["would_queue"] = 1
        eff["decision"] = {"symbol": inst, "timeframe": tf, "family": family, "action": "would_queue"}
        return eff
    selected = load_canonical_candles(
        private_root, inst, tf, fallback_glob=market_data_glob(private_root, tf),
        purpose="experiment", coverage_policy="gap_free",
    )
    if not selected.rows:
        eff["prepare_failed"] = 1
        eff["skip"] = {"symbol": inst, "reason": "snapshot_missing_before_queue", "timeframe": tf}
        return eff
    exp_id, created = _compile_and_queue(res, private_root=private_root, profiles=profiles, policy=policy,
                                         data_glob=market_data_glob(private_root, tf),
                                         priority=priority_base + job.priority, conn=conn,
                                         data_snapshot_manifest=selected.manifest.to_dict())
    state.mark_job_queued(job_key, symbol=inst, timeframe=tf, family=family, experiment_id=exp_id, cycle=cycle)
    if created:
        eff["jobs_queued"] = 1
        action = "queued"
    else:  # already in the farm queue (queued/running/completed) — re-synced, not double-counted
        eff["already_queued"] = 1
        action = "already_in_farm_queue"
    eff["decision"] = {"symbol": inst, "timeframe": tf, "family": family,
                       "experiment_id": exp_id, "action": action,
                       "data_snapshot_id": selected.manifest.snapshot_id,
                       "data_evidence_hash": selected.manifest.evidence_hash,
                       "data_provenance_status": selected.manifest.provenance_status}
    return eff


def _watch_checkpoint_keys(watch: dict) -> set[str]:
    asset = watch.get("asset") or {}
    return {str(v).upper() for v in (asset.get("okx_inst"), asset.get("symbol")) if v}


def _checkpoint_watches(state: PipelineState, watches: list[dict], cycle: int,
                        *, retry_symbols: set[str] | None = None) -> None:
    retry_symbols = {str(s).upper() for s in (retry_symbols or set()) if s}
    for w in watches:
        wid = w.get("watch_id")
        if _watch_checkpoint_keys(w) & retry_symbols:
            continue
        if wid and not state.is_watch_processed(str(wid)):
            asset, farm = w.get("asset") or {}, w.get("farm") or {}
            state.mark_watch_processed(str(wid), okx_inst=asset.get("okx_inst"),
                                       timeframe=farm.get("selected_timeframe"), cycle=cycle)


def run_cycle(
    state: PipelineState,
    *,
    private_root: Path,
    provider: Any,
    watches: list[dict],
    profiles,
    policy,
    backlog_symbols: list[str] | None = None,
    max_jobs: int = 8,
    max_prepares: int = 8,
    backend: str = "cpu",
    apply: bool = False,
    priority_base: int = 75,
    data_days: int | None = None,
    refill_timeframe: str = DEFAULT_REFILL_TIMEFRAME,
    now_ms: int | None = None,
    allow_public_output: bool = False,
) -> dict:
    """Run one coordinator cycle. Returns {cycle, counters, decisions, skipped}."""
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    private_root = resolve_private_root(Path(private_root), allow_public_output=allow_public_output) \
        if apply else Path(private_root)
    cycle = state.start_cycle() if apply else -1
    fresh = [w for w in watches if not (apply and w.get("watch_id")
                                        and state.is_watch_processed(str(w.get("watch_id"))))]

    plan = plan_jobs(fresh, max_jobs=max_jobs, backlog_symbols=list(backlog_symbols or []),
                     pending_inst=(state.queued_insts() if apply else set()))
    counters = dict(plan["counters"])
    counters["jobs_planned"] = counters.pop("queued", 0)
    counters.update({k: 0 for k in _LOOP_COUNTERS})
    skipped = list(plan["skipped"])
    decisions: list[dict] = []
    watch_by_id = {w.get("watch_id"): w for w in fresh if w.get("watch_id")}
    retry_symbols = {
        str(s.get("symbol") or "").upper()
        for s in skipped
        if str(s.get("reason") or "") in _RETRYABLE_SKIP_REASONS
    }

    if apply:
        for s in plan["skipped"]:
            state.record_skip(cycle, symbol=s.get("symbol"), timeframe=None, reason=str(s.get("reason")))

    conn, prepares_left = None, max_prepares
    if apply:
        from src.research_lab.state_db import connect, default_db_path, init_db
        conn = connect(default_db_path(private_root))
        init_db(conn)
    try:
        for job in plan["jobs"]:
            eff = _process_job(job, state=state, watch_by_id=watch_by_id, refill_timeframe=refill_timeframe,
                               backend=backend, apply=apply, cycle=cycle, private_root=private_root,
                               provider=provider, now_ms=now_ms, data_days=data_days,
                               prepares_left=prepares_left, profiles=profiles, policy=policy,
                               priority_base=priority_base, allow_public_output=allow_public_output, conn=conn)
            for key in _LOOP_COUNTERS:
                counters[key] += eff[key]
            if eff["consumed_prepare"]:
                prepares_left -= 1
            if eff["decision"]:
                decisions.append(eff["decision"])
            if eff["skip"]:
                skipped.append({"symbol": eff["skip"]["symbol"], "reason": eff["skip"]["reason"]})
                if eff["skip"]["reason"] in _RETRYABLE_SKIP_REASONS:
                    retry_symbols.add(str(eff["skip"]["symbol"] or "").upper())
                if apply:
                    state.record_skip(cycle, symbol=eff["skip"]["symbol"],
                                      timeframe=eff["skip"].get("timeframe"), reason=eff["skip"]["reason"])
    finally:
        if conn is not None:
            conn.close()

    if apply:
        _checkpoint_watches(state, fresh, cycle, retry_symbols=retry_symbols)
    counters["skipped_total"] = len(skipped)
    if apply:
        state.finish_cycle(cycle, counters)
    return {"cycle": cycle, "counters": counters, "decisions": decisions, "skipped": skipped}
