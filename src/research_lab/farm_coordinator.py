# -*- coding: utf-8 -*-
"""Farm coordinator — the continuous research cycle that never spins on already_queued.

One ``run_coordinator_cycle`` advances the whole lifecycle by deciding, each pass,
the next meaningful step for every symbol and recording it as a typed task with a
machine-readable reason:

  intake -> plan (prepare / enrich / run_sweep / defer / block / skip)
         -> unblock (a NEEDS_*_DATA task flips to queued when its slot appears)
         -> execute (prepare candles, enrich funding, MATERIALIZE run_sweep into the
            proven compute queue)
         -> sync (a finished compute job -> classify_result follow-up)
         -> classify (unique candidates + export_validation follow-up)
         -> pivot (when fresh work is saturated: deferred-eligible, discovery, or an
            explicit "blocked: no eligible tasks" — never an endless already_queued loop)

Side effects (prepare, materialize, worker, validation) happen only in ``apply``.
Paper/research only: no order path, no .env, no AUTO_TRADE, no Telegram.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from src.research_lab import farm_data_state
from src.research_lab.data_planner import plan_symbol
from src.research_lab.farm_classifier import VALIDATION_ELIGIBLE, classify_run
from src.research_lab.farm_sweep_runner import build_sweep_spec, queue_sweep
from src.research_lab.farm_tasks_db import FarmTasksDB
from src.research_lab.intake_adapter import discovery_intake_events
from src.research_lab.paths import market_data_glob, resolve_private_root
from src.research_lab.strategy_registry import get_strategy

DEFAULT_FAMILIES = ("momentum_breakout", "mean_reversion_fade", "bb_volume_fade")
_COUNTER_KEYS = (
    "events_ingested", "events_consumed", "tasks_created", "tasks_deduped",
    "planned_prepare", "planned_run_sweep", "planned_blocked", "planned_deferred",
    "planned_skipped", "prepared_ok", "prepared_deferred", "prepared_blocked",
    "enriched_ok", "enrich_deferred", "unblocked", "sweeps_materialized",
    "sweeps_deduped", "runs_completed", "runs_failed", "classified",
    "unique_upserted", "exports_created",
)


def _norm(symbol: str) -> str:
    return str(symbol).replace("-", "_").replace("/", "_").upper()


def _bump(counters: dict[str, int], key: str, n: int = 1) -> None:
    counters[key] = counters.get(key, 0) + n


def _bump_created(counters: dict[str, int], planned_key: str, created: bool) -> None:
    _bump(counters, planned_key)
    _bump(counters, "tasks_created" if created else "tasks_deduped")


# ── planning ─────────────────────────────────────────────────────────────────
def _create_from_decision(tasks: FarmTasksDB, dec: dict[str, Any], event: dict[str, Any],
                          families: tuple[str, ...], counters: dict[str, int], now: float) -> None:
    sym, tf = _norm(dec["symbol"]), str(dec.get("timeframe") or "")
    action = dec["action"]
    src = event.get("event_id")
    pri = int(event.get("priority") or 100)
    # prepare/defer tasks carry the planning context so completion can re-plan the symbol
    plan_ctx = {"asset_class": event.get("asset_class"), "families": list(families)}
    if action == "skip":
        _bump(counters, "planned_skipped")
        return
    if action == "prepare_data":
        _, created = tasks.enqueue_task(task_type="prepare_data", task_key=f"prepare::{sym}::{tf}",
                                        symbol=sym, timeframe=tf, priority=pri, source_event_id=src,
                                        machine_reason=dec["reason"], payload=plan_ctx, now=now)
        _bump_created(counters, "planned_prepare", created)
        return
    if action == "defer":
        _, created = tasks.enqueue_task(task_type="prepare_data", task_key=f"prepare::{sym}::{tf}",
                                        symbol=sym, timeframe=tf, priority=pri, source_event_id=src,
                                        state="deferred", deferred_until=dec["deferred_until"],
                                        machine_reason=dec["reason"], payload=plan_ctx, now=now)
        _bump_created(counters, "planned_deferred", created)
        return
    if action == "run_sweep":
        _create_run_sweep(tasks, dec, sym, tf, pri, src, counters, now)


def _create_run_sweep(tasks: FarmTasksDB, dec: dict[str, Any], sym: str, tf: str, pri: int,
                      src: str | None, counters: dict[str, int], now: float) -> None:
    fam = dec["family"]
    if dec.get("block"):
        key = f"run_sweep::{sym}::{tf}::{fam}::gate"
        _, created = tasks.enqueue_task(task_type="run_sweep", task_key=key, symbol=sym, timeframe=tf,
                                        family=fam, priority=pri, source_event_id=src, state="blocked",
                                        machine_reason=dec["reason"], now=now)
        _bump_created(counters, "planned_blocked", created)
        if dec.get("needs_enrich") == "funding":
            tasks.enqueue_task(task_type="enrich_funding", task_key=f"enrich_funding::{sym}::{tf}",
                               symbol=sym, timeframe=tf, priority=pri, source_event_id=src, now=now)
        return
    fp = dec.get("data_fingerprint") or "nofp"
    key = f"run_sweep::{sym}::{tf}::{fam}::{fp}"
    _, created = tasks.enqueue_task(task_type="run_sweep", task_key=key, symbol=sym, timeframe=tf,
                                    family=fam, params_hash=None, data_fingerprint=fp, priority=pri,
                                    source_event_id=src, machine_reason="data_ready", now=now)
    _bump_created(counters, "planned_run_sweep", created)


def _plan_events(tasks: FarmTasksDB, events: list[dict[str, Any]], families: tuple[str, ...],
                 data_state_fn: Callable, counters: dict[str, int], now: float) -> int:
    created_before = _count_active(tasks)
    for ev in events:
        decs = plan_symbol(ev["symbol"], ev.get("asset_class"), families,
                           data_state=lambda s, t: data_state_fn(s, t), now=now)
        for dec in decs:
            _create_from_decision(tasks, dec, ev, families, counters, now)
        tasks.mark_event_consumed(ev["event_id"])
        _bump(counters, "events_consumed")
    return _count_active(tasks) - created_before


def _count_active(tasks: FarmTasksDB) -> int:
    return sum(len(tasks.tasks_in_state(s)) for s in ("queued", "deferred", "blocked"))


# ── unblock ──────────────────────────────────────────────────────────────────
def _gate_clear(task: dict[str, Any], data_state_fn: Callable) -> bool:
    """Has a blocked run_sweep's data slot appeared (OI slot / funding field)?"""
    fam = task.get("family")
    if not fam:
        return False
    required = set(get_strategy(fam).required_data) if fam else set()
    state = data_state_fn(task["symbol"], task["timeframe"])
    if "oi" in required:
        return bool(state.get("oi_available"))
    if "funding" in required:
        return "funding" in set(state.get("enrichment") or ())
    if "microstructure" in required:
        return "obi_top5" in set(state.get("enrichment") or ())
    return True


def _unblock(tasks: FarmTasksDB, data_state_fn: Callable, counters: dict[str, int], now: float) -> None:
    for task in tasks.tasks_in_state("blocked", task_type="run_sweep"):
        if _gate_clear(task, data_state_fn):
            tasks.requeue_task(task["task_id"], reason="gate_cleared", now=now)
            _bump(counters, "unblocked")


# ── execution (apply only) ────────────────────────────────────────────────────
def _replan_after_prepare(tasks: FarmTasksDB, task: dict, *, data_state_fn, counters, now) -> None:
    """Data just landed for this symbol -> plan its run_sweep/enrich tasks now (chain prepare->sweep)."""
    import json
    payload = json.loads(task.get("payload_json") or "{}")
    families = tuple(payload.get("families") or DEFAULT_FAMILIES)
    synth = {"event_id": task.get("source_event_id"), "priority": task.get("priority"),
             "asset_class": payload.get("asset_class")}
    for dec in plan_symbol(task["symbol"], payload.get("asset_class"), families,
                           data_state=lambda s, t: data_state_fn(s, t), now=now):
        if dec["action"] in ("run_sweep",):  # only the now-unlocked compute step
            _create_from_decision(tasks, dec, synth, families, counters, now)


def _drain_prepare(tasks: FarmTasksDB, *, private_root, provider, now_ms, data_days,
                   allow_public, limit, counters, now, data_state_fn) -> None:
    from src.research_lab.data_planner import _defer_until
    from src.research_lab.scanner_farm_pipeline import _ensure_local_data
    for _ in range(limit):
        task = tasks.claim_next_task(task_types=("prepare_data",), now=now)
        if task is None:
            break
        if provider is None:
            tasks.defer_task(task["task_id"], until=now + 3600, reason="no_provider_configured", now=now)
            _bump(counters, "prepared_deferred")
            continue
        status, rows = _ensure_local_data(task["symbol"], task["timeframe"], private_root=private_root,
                                          provider=provider, apply=True, now_ms=now_ms, data_days=data_days,
                                          prepares_left=1, allow_public_output=allow_public)
        if status in ("usable", "prepared"):
            tasks.complete_task(task["task_id"], reason=status, now=now)
            _bump(counters, "prepared_ok")
            _replan_after_prepare(tasks, task, data_state_fn=data_state_fn, counters=counters, now=now)
        elif status in ("too_short", "fresh_listing_pending"):
            tasks.defer_task(task["task_id"], until=_defer_until(now, task["timeframe"], rows),
                             reason=status, now=now)
            _bump(counters, "prepared_deferred")
        elif int(task.get("attempts") or 0) >= 3:
            tasks.block_task(task["task_id"], reason=f"prepare_backoff:{status}", now=now)
            _bump(counters, "prepared_blocked")
        else:
            tasks.defer_task(task["task_id"], until=now + 3600, reason=status, now=now)
            _bump(counters, "prepared_deferred")


def _drain_enrich(tasks: FarmTasksDB, *, private_root, flow_provider, now_ms, limit, counters, now) -> None:
    from src.research_lab.experiment import choose_symbol_file
    from src.research_lab.flow_enrich import COUNTER_KEYS, FlowEnrichState, _enrich_one
    for _ in range(limit):
        task = tasks.claim_next_task(task_types=("enrich_funding",), now=now)
        if task is None:
            break
        if flow_provider is None:
            tasks.defer_task(task["task_id"], until=now + 3600, reason="no_funding_provider", now=now)
            _bump(counters, "enrich_deferred")
            continue
        glob = market_data_glob(private_root, task["timeframe"])
        path = choose_symbol_file(glob, task["symbol"], timeframe=task["timeframe"])
        if not path:
            tasks.defer_task(task["task_id"], until=now + 3600, reason="no_prepared_file", now=now)
            _bump(counters, "enrich_deferred")
            continue
        cc = {k: 0 for k in COUNTER_KEYS}
        key = f'{task["symbol"]}::{task["timeframe"]}::{path.name}'
        _enrich_one(path, key, task["symbol"], provider=flow_provider, state=FlowEnrichState(),
                    now_ms=now_ms, ttl_seconds=12 * 3600, cooldown_seconds=6 * 3600, max_attempts=3,
                    budget=1, counters=cc)
        if cc.get("enriched"):
            tasks.complete_task(task["task_id"], reason="enriched", now=now)
            _bump(counters, "enriched_ok")
        else:
            tasks.defer_task(task["task_id"], until=now + 6 * 3600, reason="enrich_no_points", now=now)
            _bump(counters, "enrich_deferred")


def _drain_run_sweep(tasks: FarmTasksDB, *, conn, private_root, profiles, policy, backend,
                     priority_base, limit, counters, now) -> None:
    from src.research_lab.data_fingerprint import fingerprint_for_symbol
    for _ in range(limit):
        task = tasks.claim_next_task(task_types=("run_sweep",), now=now)
        if task is None:
            break
        sym, tf, fam = task["symbol"], task["timeframe"], task["family"]
        glob = market_data_glob(private_root, tf)
        fp = fingerprint_for_symbol(glob, sym, tf) or task.get("data_fingerprint") or "nofp"
        spec = build_sweep_spec(sym, tf, fam, fingerprint=fp, backend=backend)
        exp_id, job_id, created = queue_sweep(conn, spec, private_root=private_root, profiles=profiles,
                                              policy=policy, data_glob=glob, fingerprint=fp,
                                              priority=priority_base + int(task.get("priority") or 100))
        if created:
            tasks.materialize_task(task["task_id"], job_id, now=now)
            _bump(counters, "sweeps_materialized")
        else:
            tasks.complete_task(task["task_id"], reason="compute_deduped",
                                materialized_queue_job_id=job_id, now=now)
            _bump(counters, "sweeps_deduped")


def _sync_completions(tasks: FarmTasksDB, *, conn, counters, now) -> None:
    for task in tasks.tasks_in_state("running", task_type="run_sweep"):
        job_id = task.get("materialized_queue_job_id")
        if not job_id:
            continue
        row = conn.execute("SELECT status, run_dir_label FROM queue WHERE job_id=?", (int(job_id),)).fetchone()
        if row is None:
            continue
        if row["status"] == "completed":
            label = row["run_dir_label"]
            tasks.complete_task(task["task_id"], last_result_ref=label, run_dir_label=label,
                                materialized_queue_job_id=int(job_id), reason="compute_completed", now=now)
            tasks.enqueue_task(task_type="classify_result", task_key=f"classify::{int(job_id)}",
                               symbol=task["symbol"], timeframe=task["timeframe"], family=task["family"],
                               data_fingerprint=task.get("data_fingerprint"),
                               payload={"run_dir_label": label, "timeframe": task["timeframe"],
                                        "data_fingerprint": task.get("data_fingerprint")}, now=now)
            _bump(counters, "runs_completed")
        elif row["status"] == "failed":
            tasks.fail_task(task["task_id"], "compute_failed", now=now)
            _bump(counters, "runs_failed")


def _classify_due(tasks: FarmTasksDB, *, private_root, limit, counters, now) -> None:
    import json
    for _ in range(limit):
        task = tasks.claim_next_task(task_types=("classify_result",), now=now)
        if task is None:
            break
        payload = json.loads(task.get("payload_json") or "{}")
        rows = classify_run(private_root, payload.get("run_dir_label", ""),
                            timeframe=payload.get("timeframe") or task["timeframe"],
                            data_fingerprint=payload.get("data_fingerprint"), task_id=task["task_id"])
        for uc in rows:
            tasks.upsert_unique_candidate(uc, now=now)
            _bump(counters, "unique_upserted")
            if uc["validation_status"] in VALIDATION_ELIGIBLE:
                _, created = tasks.enqueue_task(
                    task_type="export_validation", task_key=f'export::{uc["candidate_id"]}',
                    symbol=uc["symbol"], timeframe=uc["timeframe"], family=uc["family"],
                    params_hash=uc["params_hash"], data_fingerprint=uc["data_fingerprint"],
                    payload={"candidate_id": uc["candidate_id"]}, now=now)
                if created:
                    _bump(counters, "exports_created")
        tasks.complete_task(task["task_id"], reason="classified", now=now)
        _bump(counters, "classified")


def _drain_worker(private_root, max_jobs: int, night_mode: bool) -> None:
    if max_jobs <= 0:
        return
    from scripts.strategy_lab.worker_once import run_worker_once
    for _ in range(max_jobs):
        try:
            status = run_worker_once(private_root, night_mode=night_mode, ignore_cadence=True)
        except Exception:  # noqa: BLE001 - one bad job must not abort the cycle
            break
        if status.get("status") in {"queue_empty", "deferred"}:
            break


# ── pivot ─────────────────────────────────────────────────────────────────────
def _decide_pivot(tasks: FarmTasksDB, *, new_tasks: int, did_work: bool, now: float,
                  snapshot, families, data_state_fn, max_discovery: int, counters: dict) -> str:
    """Never spin on already_queued: report work, or actively pull discovery, or say blocked."""
    if tasks.eligible_count(now) > 0:
        return "work_available"
    if new_tasks > 0 or did_work:
        return "advanced_lifecycle"
    if snapshot:
        covered = tasks.active_symbols() | {
            str(c["symbol"]).upper() for c in tasks.latest_unique_candidates(limit=2000)}
        events = discovery_intake_events(snapshot, covered=covered, now=now, limit=max_discovery)
        created = _plan_events(tasks, [_with_id(tasks, e, now) for e in events], families,
                               data_state_fn, counters, now)
        if created > 0:
            return "discovery_refill"
    return "blocked:no_eligible_tasks"


def _with_id(tasks: FarmTasksDB, event: dict, now: float) -> dict:
    tasks.upsert_intake_event(event, now=now)
    return event


# ── cycle ──────────────────────────────────────────────────────────────────────
def run_coordinator_cycle(
    tasks: FarmTasksDB, *, private_root, profiles, policy,
    intake_events: list[dict] | None = None, families: tuple[str, ...] = DEFAULT_FAMILIES,
    data_state_fn: Callable | None = None, provider=None, flow_provider=None, apply: bool = False,
    now: float | None = None, now_ms: int | None = None, backend: str = "auto",
    data_days: int | None = None, max_plan_events: int = 20, max_prepares: int = 4,
    max_enrich: int = 4, max_sweeps: int = 4, max_classify: int = 8, run_worker: bool = False,
    max_worker_jobs: int = 4, night_mode: bool = False, priority_base: int = 100,
    allow_public_output: bool = False, discovery_snapshot=None, max_discovery: int = 20,
    run_validation: bool = False, max_validations: int = 10,
) -> dict[str, Any]:
    """Advance the research lifecycle by one cycle. Returns counters + pivot + status."""
    now = time.time() if now is None else now
    now_ms = int(now * 1000) if now_ms is None else now_ms
    private_root = resolve_private_root(Path(private_root), allow_public_output=allow_public_output) \
        if apply else Path(private_root)
    if data_state_fn is None:
        def data_state_fn(s, t):  # noqa: E306 - bound to resolved private_root
            return farm_data_state.data_state(private_root, s, t)
    counters = {k: 0 for k in _COUNTER_KEYS}

    for ev in (intake_events or []):
        _, created = tasks.upsert_intake_event(ev, now=now)
        if created:
            _bump(counters, "events_ingested")
    fresh = tasks.unconsumed_events(limit=max_plan_events)
    new_tasks = _plan_events(tasks, fresh, families, data_state_fn, counters, now)
    _unblock(tasks, data_state_fn, counters, now)

    conn = None
    if apply:
        from src.research_lab.state_db import connect, default_db_path, init_db
        conn = connect(default_db_path(private_root))
        init_db(conn)
    try:
        if apply:
            _drain_prepare(tasks, private_root=private_root, provider=provider, now_ms=now_ms,
                           data_days=data_days, allow_public=allow_public_output, limit=max_prepares,
                           counters=counters, now=now, data_state_fn=data_state_fn)
            _drain_enrich(tasks, private_root=private_root, flow_provider=flow_provider, now_ms=now_ms,
                          limit=max_enrich, counters=counters, now=now)
            _drain_run_sweep(tasks, conn=conn, private_root=private_root, profiles=profiles, policy=policy,
                             backend=backend, priority_base=priority_base, limit=max_sweeps,
                             counters=counters, now=now)
            _sync_completions(tasks, conn=conn, counters=counters, now=now)
            if run_worker:
                _drain_worker(private_root, max_worker_jobs, night_mode)
                _sync_completions(tasks, conn=conn, counters=counters, now=now)
            _classify_due(tasks, private_root=private_root, limit=max_classify, counters=counters, now=now)
    finally:
        if conn is not None:
            conn.close()

    if apply and run_validation:
        from src.research_lab.validation_orchestrator import run_due_validations
        counters["validation"] = run_due_validations(tasks, private_root, apply=True,
                                                      limit=max_validations, now=now)

    did_work = any(counters[k] for k in ("prepared_ok", "enriched_ok", "sweeps_materialized",
                                         "runs_completed", "classified", "unblocked"))
    pivot = _decide_pivot(tasks, new_tasks=new_tasks, did_work=did_work, now=now,
                          snapshot=discovery_snapshot, families=families, data_state_fn=data_state_fn,
                          max_discovery=max_discovery, counters=counters)
    return {"counters": counters, "pivot": pivot, "active_tasks": _count_active(tasks),
            "status": tasks.status_counts()}


def main() -> None:  # pragma: no cover - thin CLI shim lives in scripts/
    raise SystemExit("use scripts/strategy_lab/farm_loop.py")
