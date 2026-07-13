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

import json
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable

from src.research_lab import feedback_reader as fr
from src.research_lab import farm_data_state
from src.research_lab.data_planner import plan_symbol
from src.research_lab.farm_classifier import VALIDATION_ELIGIBLE, classify_run
from src.research_lab.farm_sweep_runner import build_sweep_spec, queue_sweep
from src.research_lab.farm_tasks_db import FarmTasksDB
from src.research_lab.farm_priority import priority_value
from src.research_lab.feedback_followup import plan_followup
from src.research_lab.intake_adapter import discovery_intake_events
from src.research_lab.outcome_retest import paper_to_executable_family
from src.research_lab.outcome_retest import write_outcome_retest_specs
from src.research_lab.paths import market_data_glob, resolve_private_root
from src.research_lab.setup_outcome_memory import GateIndex, build_gate_index, lookup
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.sweep_spec import SweepSpec, validate_sweep_spec
from src.research_lab.tail_diagnostics import load_universe_symbols
from src.research_lab.validation_feedback import load_feedback_queue

DEFAULT_FAMILIES = ("momentum_breakout", "mean_reversion_fade", "bb_volume_fade")
_COUNTER_KEYS = (
    "events_ingested", "events_consumed", "tasks_created", "tasks_deduped",
    "planned_prepare", "planned_run_sweep", "planned_blocked", "planned_deferred",
    "planned_skipped", "prepared_ok", "prepared_deferred", "prepared_blocked",
    "enriched_ok", "enrich_deferred", "enriched_oi_ok", "enrich_oi_deferred",
    "oi_marked_unmeasured",
    "prepare_provider_error_parked",
    "unblocked", "sweeps_materialized",
    "sweeps_deduped", "sweeps_skipped_memory", "sweeps_deprioritized",
    "runs_completed", "runs_failed", "classified",
    "unique_upserted", "exports_created", "followups_scheduled",
    "followups_deduped", "followup_notes", "followup_sweeps_planned",
    "followup_invalid", "outcome_retests_cataloged", "outcome_retests_scheduled",
    "outcome_retests_deduped", "outcome_retest_sweeps_planned",
    "outcome_retest_invalid", "outcome_retest_notes",
    "advisor_proposals_loaded", "advisor_sweeps_scheduled",
    "advisor_sweeps_deduped", "advisor_sweep_invalid", "advisor_sweeps_planned",
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
                          families: tuple[str, ...], counters: dict[str, int], now: float,
                          gate_index: GateIndex | None = None) -> None:
    sym, tf = _norm(dec["symbol"]), str(dec.get("timeframe") or "")
    action = dec["action"]
    src = event.get("event_id")
    pri = priority_value(event.get("priority"))
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
        _create_run_sweep(tasks, dec, sym, tf, pri, src, counters, now, gate_index)


def _create_run_sweep(tasks: FarmTasksDB, dec: dict[str, Any], sym: str, tf: str, pri: int,
                      src: str | None, counters: dict[str, int], now: float,
                      gate_index: GateIndex | None = None) -> None:
    fam = dec["family"]
    if dec.get("block"):
        key = f"run_sweep::{sym}::{tf}::{fam}::gate"
        _, created = tasks.enqueue_task(task_type="run_sweep", task_key=key, symbol=sym, timeframe=tf,
                                        family=fam, priority=pri, source_event_id=src, state="blocked",
                                        machine_reason=dec["reason"], now=now)
        _bump_created(counters, "planned_blocked", created)
        enrich = dec.get("needs_enrich")
        if enrich in ("funding", "oi"):
            tasks.enqueue_task(task_type=f"enrich_{enrich}", task_key=f"enrich_{enrich}::{sym}::{tf}",
                               symbol=sym, timeframe=tf, priority=pri, source_event_id=src, now=now)
        return
    fp = dec.get("data_fingerprint") or "nofp"
    machine_reason = "data_ready"
    # Read-through Setup Outcome Memory BEFORE spending compute: identical data already
    # proven dead is skipped; a historically all-rejected family cell is down-ranked.
    if gate_index is not None:
        verdict = lookup(gate_index, symbol=sym, timeframe=tf, family=fam, data_fingerprint=fp)
        if verdict.action == "skip_known_bad":
            _bump(counters, "sweeps_skipped_memory")
            return
        if verdict.action == "deprioritize":
            pri += 50
            machine_reason = "data_ready:memory_deprioritized"
            _bump(counters, "sweeps_deprioritized")
    key = f"run_sweep::{sym}::{tf}::{fam}::{fp}"
    _, created = tasks.enqueue_task(task_type="run_sweep", task_key=key, symbol=sym, timeframe=tf,
                                    family=fam, params_hash=None, data_fingerprint=fp, priority=pri,
                                    source_event_id=src, machine_reason=machine_reason, now=now)
    _bump_created(counters, "planned_run_sweep", created)


def _plan_events(tasks: FarmTasksDB, events: list[dict[str, Any]], families: tuple[str, ...],
                 data_state_fn: Callable, counters: dict[str, int], now: float,
                 gate_index: GateIndex | None = None) -> int:
    created_before = _count_active(tasks)
    for ev in events:
        decs = plan_symbol(ev["symbol"], ev.get("asset_class"), families,
                           data_state=lambda s, t: data_state_fn(s, t), now=now)
        for dec in decs:
            _create_from_decision(tasks, dec, ev, families, counters, now, gate_index)
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
    enrichment = set(state.get("enrichment") or ())
    if "oi" in required:
        # Honest gate: only an actually-merged ``oi`` field clears the block. The mere
        # existence of an oi-slot file (state['oi_available']) must NOT unblock a sweep,
        # or it would run "OI-ready" with zero merged coverage (Phase 0.5).
        return "oi" in enrichment
    if "funding" in required:
        return "funding" in enrichment
    if "microstructure" in required:
        return "obi_top5" in enrichment
    return True


def _unblock(tasks: FarmTasksDB, data_state_fn: Callable, counters: dict[str, int], now: float) -> None:
    for task in tasks.tasks_in_state("blocked", task_type="run_sweep"):
        if _gate_clear(task, data_state_fn):
            tasks.requeue_task(task["task_id"], reason="gate_cleared", now=now)
            _bump(counters, "unblocked")


def _park_terminal_prepare_provider_errors(
    tasks: FarmTasksDB,
    *,
    private_root: Path,
    counters: dict[str, int],
    now: float,
) -> None:
    """Turn non-actionable provider-error prepare tails into terminal skipped tasks.

    A missing or empty live universe is not authoritative, so those rows remain
    blocked for bounded retry/operator review. When the universe is present and a
    symbol is absent from it, no candles will arrive; keeping it active only makes
    the farm look broken.
    """
    universe = load_universe_symbols(Path(private_root))
    if not universe:
        return
    for task in tasks.tasks_in_state("blocked", task_type="prepare_data"):
        if str(task.get("machine_reason") or "") != "prepare_backoff:provider_error":
            continue
        symbol = _norm(str(task.get("symbol") or ""))
        if symbol and symbol not in universe:
            tasks.skip_task(
                int(task["task_id"]),
                reason="parked:no_instrument_or_delisted",
                now=now,
            )
            _bump(counters, "prepare_provider_error_parked")


_REC_PRIORITY = {"high": 40, "normal": 70, "low": 100}
# Max generations of failure-driven follow-up sweeps before stopping (no infinite re-grind).
MAX_FOLLOWUP_DEPTH = 2


def _load_setup_cards(private_root: Path) -> list[dict[str, Any]]:
    cards_dir = Path(private_root) / "setup_library" / "cards"
    if not cards_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(cards_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _candidate_context_by_id(tasks: FarmTasksDB) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in tasks.latest_unique_candidates(limit=5000):
        cid = str(row.get("candidate_id") or "")
        if not cid:
            continue
        try:
            params = json.loads(row.get("params_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            params = {}
        out[cid] = {
            "params": params if isinstance(params, dict) else {},
            "validation_status": str(row.get("validation_status") or ""),
            # carry the stored dominant regime bucket so REGIME_SWEEP follow-ups can
            # actually build a filter (Phase 1.3) instead of no-oping on missing_regime_filter.
            "regime_summary": {"dominant_bucket": str(row.get("regime_bucket") or "")},
        }
    return out


def _schedule_due_followups(tasks: FarmTasksDB, *, private_root: Path, counters: dict[str, int],
                            now: float, limit: int) -> None:
    recs = fr.build_recommendations(load_feedback_queue(private_root), _load_setup_cards(private_root))
    from src.research_lab.outcome_retest_result import build_outcome_retest_results

    build_outcome_retest_results(private_root)
    retest_catalog = write_outcome_retest_specs(private_root, max_specs=limit)
    if int(retest_catalog.get("specs") or 0):
        _bump(counters, "outcome_retests_cataloged", int(retest_catalog.get("specs") or 0))
    for spec in retest_catalog.get("items") or []:
        if not isinstance(spec, dict) or not bool(spec.get("queueable")):
            continue
        key = f"retest_schedule::{spec.get('retest_id')}"
        _, created = tasks.enqueue_task(
            task_type="schedule_retest",
            task_key=key,
            priority=60,
            symbol=str(spec.get("symbol") or ""),
            timeframe=str(spec.get("timeframe") or ""),
            family=str(spec.get("family") or ""),
            source_event_id=str(spec.get("retest_id") or ""),
            payload={"retest_spec": spec, "followup_depth": 0},
            now=now,
        )
        _bump(counters, "outcome_retests_scheduled" if created else "outcome_retests_deduped")
    for rec in recs[: max(0, int(limit))]:
        cid = rec.candidate_ids[0] if rec.candidate_ids else ""
        key = f"followup_schedule::{cid or rec.symbol}::{rec.strategy_id}::{rec.action}::{rec.hard_status}"
        _, created = tasks.enqueue_task(
            task_type="schedule_followup", task_key=key,
            priority=_REC_PRIORITY.get(rec.priority, 80), symbol=rec.symbol,
            timeframe=rec.timeframe, family=rec.strategy_id, source_event_id=cid,
            payload={"recommendation": rec.to_dict(), "followup_depth": 0}, now=now,
        )
        _bump(counters, "followups_scheduled" if created else "followups_deduped")


def _rec_from_payload(payload: dict[str, Any]) -> fr.Recommendation | None:
    data = payload.get("recommendation") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    return fr.Recommendation(
        action=str(data.get("action") or ""),
        strategy_id=str(data.get("strategy_id") or ""),
        symbol=str(data.get("symbol") or ""),
        timeframe=str(data.get("timeframe") or ""),
        reason=str(data.get("reason") or ""),
        hard_status=str(data.get("hard_status") or ""),
        priority=str(data.get("priority") or "normal"),
        candidate_ids=[str(x) for x in data.get("candidate_ids") or []],
        reason_codes=[str(x) for x in data.get("reason_codes") or []],
    )


def _sweep_payload(spec: SweepSpec) -> dict[str, Any]:
    data = asdict(spec)
    data["related_symbols"] = list(spec.related_symbols)
    return data


def _sweep_from_payload(data: dict[str, Any]) -> SweepSpec:
    return SweepSpec(
        sweep_id=str(data["sweep_id"]),
        anchor_symbol=str(data["anchor_symbol"]),
        related_symbols=tuple(data.get("related_symbols") or ()),
        timeframe=str(data["timeframe"]),
        setup_family=str(data["setup_family"]),
        setup_grid=dict(data.get("setup_grid") or {}),
        entry_grid=dict(data.get("entry_grid") or {}),
        exit_grid=dict(data.get("exit_grid") or {}),
        filter_grid=dict(data.get("filter_grid") or {}),
        max_variants=int(data.get("max_variants") or 1),
        backend=str(data.get("backend") or "cpu"),
        resource_class=str(data.get("resource_class") or "normal"),
        private_output_policy=str(data.get("private_output_policy") or "private_only"),
        variant_tier=str(data.get("variant_tier") or "smoke"),
    )


_SWEEP_EVENT_CONTEXT_KEYS = {
    "origin",
    "action",
    "retest_id",
    "review_id",
    "source_ref",
    "paper_signal_id",
    "actionability",
    "outcome_bucket",
    "baseline",
    "proposed_changes",
    "source_candidate_id",
    "hard_status",
    "hard_status_action",
    "followup_depth",
    "advisor_ref",
    "proposal_id",
    "feature_packet_id",
    "dimension",
    "source_signal_id",
    "paper_only",
    "execution_allowed",
}


def _event_context_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _SWEEP_EVENT_CONTEXT_KEYS:
        if key in payload:
            out[key] = payload[key]
    return out


def _retest_spec_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get("retest_spec") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else None


def _drain_retest_task(
    tasks: FarmTasksDB,
    task: dict[str, Any],
    *,
    profiles,
    policy,
    counters: dict[str, int],
    now: float,
) -> None:
    try:
        payload = json.loads(task.get("payload_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    spec_row = _retest_spec_from_payload(payload)
    if spec_row is None:
        tasks.skip_task(task["task_id"], "malformed_retest_payload", now=now)
        _bump(counters, "outcome_retest_invalid")
        return
    depth = int(payload.get("followup_depth") or 0)
    if depth >= MAX_FOLLOWUP_DEPTH:
        tasks.complete_task(task["task_id"], reason="retest_depth_capped", now=now)
        _bump(counters, "outcome_retest_notes")
        return
    sweep_data = spec_row.get("sweep_spec")
    if not isinstance(sweep_data, dict):
        tasks.complete_task(task["task_id"], reason=spec_row.get("not_queueable_reason") or "no_sweep_spec", now=now)
        _bump(counters, "outcome_retest_notes")
        return
    sweep = _sweep_from_payload(sweep_data)
    check = validate_sweep_spec(sweep, timeframe_profiles=profiles, resource_policy=policy)
    if not check.ok:
        tasks.skip_task(task["task_id"], "invalid_retest_spec:" + "|".join(check.errors), now=now)
        _bump(counters, "outcome_retest_invalid")
        return
    run_payload = {
        "origin": "outcome_retest",
        "retest_id": spec_row.get("retest_id"),
        "review_id": spec_row.get("review_id"),
        "source_ref": spec_row.get("source_ref"),
        "paper_signal_id": spec_row.get("paper_signal_id"),
        "actionability": spec_row.get("actionability"),
        "outcome_bucket": spec_row.get("outcome_bucket"),
        "baseline": spec_row.get("baseline") or {},
        "source_candidate_id": spec_row.get("candidate_id"),
        "source_family": spec_row.get("source_family"),
        "proposed_changes": spec_row.get("proposed_changes") or [],
        "sweep_spec": _sweep_payload(sweep),
        "followup_depth": depth + 1,
        "role_environment_id": payload.get("role_environment_id"),
        "feedback_id": payload.get("feedback_id"),
        "paper_only": True,
        "execution_allowed": False,
    }
    _, created = tasks.enqueue_task(
        task_type="run_sweep",
        task_key=f"run_sweep::outcome_retest::{sweep.sweep_id}",
        priority=int(task.get("priority") or 60),
        symbol=sweep.anchor_symbol,
        timeframe=sweep.timeframe,
        family=sweep.setup_family,
        source_event_id=str(spec_row.get("retest_id") or ""),
        payload=run_payload,
        now=now,
    )
    tasks.complete_task(task["task_id"], reason="planned_run_sweep" if created else "deduped", now=now)
    _bump(counters, "outcome_retest_sweeps_planned" if created else "outcome_retests_deduped")


def _schedule_advisor_sweeps(tasks: FarmTasksDB, *, private_root: Path, counters: dict[str, int],
                             now: float, limit: int) -> None:
    from src.research_lab.advisor_sweep_bridge import schedule_advisor_sweep_tasks

    summary = schedule_advisor_sweep_tasks(private_root, tasks, limit=limit, now=now)
    _bump(counters, "advisor_proposals_loaded", int(summary.get("loaded") or 0))
    _bump(counters, "advisor_sweeps_scheduled", int(summary.get("scheduled") or 0))
    _bump(counters, "advisor_sweeps_deduped", int(summary.get("deduped") or 0))


def _safe_part(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value or ""))[:48].strip("_") or "x"


def _drain_advisor_sweeps(
    tasks: FarmTasksDB,
    *,
    profiles,
    policy,
    backend: str,
    counters: dict[str, int],
    now: float,
    limit: int,
) -> None:
    for _ in range(limit):
        task = tasks.claim_next_task(task_types=("schedule_advisor_sweep",), now=now)
        if task is None:
            break
        try:
            payload = json.loads(task.get("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        proposal = payload.get("proposal") if isinstance(payload, dict) else {}
        source_signal = payload.get("source_signal") if isinstance(payload, dict) else {}
        if not isinstance(proposal, dict) or not isinstance(source_signal, dict):
            tasks.skip_task(task["task_id"], "malformed_advisor_payload", now=now)
            _bump(counters, "advisor_sweep_invalid")
            continue
        sym, tf = task["symbol"], task["timeframe"]
        source_family = str(source_signal.get("setup_family") or task.get("family") or "")
        fam = str(source_signal.get("executable_family") or paper_to_executable_family(source_family))
        fp = str(source_signal.get("data_fingerprint") or task.get("data_fingerprint") or "nofp")
        dimension = str(proposal.get("dimension") or "unknown")
        try:
            base = build_sweep_spec(
                sym, tf, fam, fingerprint=fp, backend=backend, tier="normal",
                dimensions=(dimension,),
            )
        except ValueError as exc:
            tasks.skip_task(task["task_id"], f"invalid_advisor_family:{exc}", now=now)
            _bump(counters, "advisor_sweep_invalid")
            continue
        sweep = replace(
            base,
            sweep_id=(
                "advisor_"
                f"{_safe_part(str(proposal.get('proposal_id') or 'proposal'))}_"
                f"{_safe_part(sym)}_{_safe_part(tf)}_{_safe_part(fam)}_"
                f"{_safe_part(dimension)}_{_safe_part(fp)}"
            ),
        )
        check = validate_sweep_spec(sweep, timeframe_profiles=profiles, resource_policy=policy)
        blocking_errors = [err for err in check.errors if "variant grid" not in err]
        if blocking_errors:
            tasks.skip_task(task["task_id"], "invalid_advisor_spec:" + "|".join(blocking_errors), now=now)
            _bump(counters, "advisor_sweep_invalid")
            continue
        run_payload = {
            "origin": "calculator_advisor",
            "proposal_id": proposal.get("proposal_id"),
            "advisor_ref": proposal.get("advisor_ref"),
            "feature_packet_id": proposal.get("feature_packet_id"),
            "dimension": dimension,
            "source_signal_id": source_signal.get("signal_id"),
            "source_family": source_family,
            "executable_family": fam,
            "sweep_spec": _sweep_payload(sweep),
            "paper_only": True,
            "execution_allowed": False,
        }
        _, created = tasks.enqueue_task(
            task_type="run_sweep",
            task_key=f"run_sweep::advisor::{sweep.sweep_id}",
            priority=int(task.get("priority") or 65),
            symbol=sweep.anchor_symbol,
            timeframe=sweep.timeframe,
            family=sweep.setup_family,
            source_event_id=str(proposal.get("proposal_id") or ""),
            payload=run_payload,
            now=now,
        )
        tasks.complete_task(task["task_id"], reason="planned_run_sweep" if created else "deduped", now=now)
        _bump(counters, "advisor_sweeps_planned" if created else "advisor_sweeps_deduped")


def _drain_followups(tasks: FarmTasksDB, *, profiles, policy, limit: int,
                     counters: dict[str, int], now: float) -> None:
    contexts = _candidate_context_by_id(tasks)
    for _ in range(limit):
        task = tasks.claim_next_task(task_types=("schedule_followup", "schedule_retest"), now=now)
        if task is None:
            break
        if task.get("task_type") == "schedule_retest":
            _drain_retest_task(tasks, task, profiles=profiles, policy=policy, counters=counters, now=now)
            continue
        try:
            payload = json.loads(task.get("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        rec = _rec_from_payload(payload)
        if rec is None:
            tasks.skip_task(task["task_id"], "malformed_followup_payload", now=now)
            _bump(counters, "followup_invalid")
            continue
        depth = int(payload.get("followup_depth") or 0)
        if depth >= MAX_FOLLOWUP_DEPTH:
            tasks.complete_task(task["task_id"], reason="followup_depth_capped", now=now)
            _bump(counters, "followup_notes")
            continue
        cid = rec.candidate_ids[0] if rec.candidate_ids else ""
        plan = plan_followup(rec, contexts.get(cid), max_variants=8)
        if not plan.queued or plan.sweep is None:
            tasks.complete_task(task["task_id"], reason=plan.not_queued_reason or "note", now=now)
            _bump(counters, "followup_notes")
            continue
        check = validate_sweep_spec(plan.sweep, timeframe_profiles=profiles, resource_policy=policy)
        if not check.ok:
            tasks.skip_task(task["task_id"], "invalid_followup_spec:" + "|".join(check.errors), now=now)
            _bump(counters, "followup_invalid")
            continue
        run_payload = {
            "origin": "feedback_followup", "action": plan.action,
            "source_candidate_id": plan.candidate_id, "hard_status": rec.hard_status,
            "sweep_spec": _sweep_payload(plan.sweep), "followup_depth": depth + 1,
        }
        _, created = tasks.enqueue_task(
            task_type="run_sweep", task_key=f"run_sweep::followup::{plan.sweep.sweep_id}",
            priority=int(task.get("priority") or 70), symbol=plan.symbol,
            timeframe=plan.timeframe, family=plan.strategy_id,
            source_event_id=plan.candidate_id, payload=run_payload, now=now,
        )
        tasks.complete_task(task["task_id"], reason="planned_run_sweep" if created else "deduped", now=now)
        _bump(counters, "followup_sweeps_planned" if created else "followups_deduped")


# ── execution (apply only) ────────────────────────────────────────────────────
def _replan_after_prepare(tasks: FarmTasksDB, task: dict, *, data_state_fn, counters, now,
                          gate_index=None) -> None:
    """Data just landed for this symbol -> plan its run_sweep/enrich tasks now (chain prepare->sweep)."""
    import json
    payload = json.loads(task.get("payload_json") or "{}")
    families = tuple(payload.get("families") or DEFAULT_FAMILIES)
    synth = {"event_id": task.get("source_event_id"), "priority": task.get("priority"),
             "asset_class": payload.get("asset_class")}
    for dec in plan_symbol(task["symbol"], payload.get("asset_class"), families,
                           data_state=lambda s, t: data_state_fn(s, t), now=now):
        if dec["action"] in ("run_sweep",):  # only the now-unlocked compute step
            _create_from_decision(tasks, dec, synth, families, counters, now, gate_index)


def _drain_prepare(tasks: FarmTasksDB, *, private_root, provider, now_ms, data_days,
                   allow_public, limit, counters, now, data_state_fn, gate_index=None) -> None:
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
            _replan_after_prepare(tasks, task, data_state_fn=data_state_fn, counters=counters,
                                  now=now, gate_index=gate_index)
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


# Operator-facing machine reasons for a deferred enrich_oi task (Phase 0.5).
_OI_DEFER_REASON = {
    "no_candles": "oi_window_too_short",
    "fetch_failed": "oi_provider_failed",
    "no_points": "oi_not_available_for_instrument",
    "not_enough_coverage": "oi_loaded_not_enough_points",
}
# Structural OI failures (no series / retention < window / thin coverage). After OI_MAX_ATTEMPTS
# of these we stop re-polling forever and mark the OI sweep oi_unmeasured (honest, not eternal).
_OI_STRUCTURAL = {"no_candles", "no_points", "not_enough_coverage"}


def _mark_oi_unmeasured_sweeps(tasks: FarmTasksDB, symbol: str, timeframe: str, now: float) -> int:
    """Free OI sweeps blocked on NEEDS_OI_DATA for (symbol, tf): terminal oi_unmeasured, not pending."""
    freed = 0
    for t in tasks.tasks_in_state("blocked", task_type="run_sweep"):
        if (t.get("symbol") == symbol and t.get("timeframe") == timeframe
                and t.get("machine_reason") == "NEEDS_OI_DATA"):
            tasks.skip_task(t["task_id"], reason="oi_unmeasured", now=now)
            freed += 1
    return freed


def _drain_enrich_oi(tasks: FarmTasksDB, *, private_root, oi_provider, now_ms, limit, counters, now) -> None:
    from src.research_lab.experiment import choose_symbol_file
    from src.research_lab.flow_enrich import enrich_oi_one
    from src.research_lab.oi_status import OI_MAX_ATTEMPTS
    for _ in range(limit):
        task = tasks.claim_next_task(task_types=("enrich_oi",), now=now)
        if task is None:
            break
        if oi_provider is None:
            tasks.defer_task(task["task_id"], until=now + 3600, reason="no_oi_provider", now=now)
            _bump(counters, "enrich_oi_deferred")
            continue
        glob = market_data_glob(private_root, task["timeframe"])
        path = choose_symbol_file(glob, task["symbol"], timeframe=task["timeframe"])
        if not path:
            tasks.defer_task(task["task_id"], until=now + 3600, reason="no_prepared_file", now=now)
            _bump(counters, "enrich_oi_deferred")
            continue
        status, _n = enrich_oi_one(path, task["symbol"], task["timeframe"], provider=oi_provider, now_ms=now_ms)
        if status == "enriched":
            tasks.complete_task(task["task_id"], reason="oi_loaded", now=now)
            _bump(counters, "enriched_oi_ok")
        elif status in _OI_STRUCTURAL and int(task.get("attempts") or 0) >= OI_MAX_ATTEMPTS:
            # Honest terminal state: stop re-polling structurally-absent OI and free the sweep.
            tasks.skip_task(task["task_id"], reason="oi_unmeasured", now=now)
            _mark_oi_unmeasured_sweeps(tasks, task["symbol"], task["timeframe"], now)
            _bump(counters, "oi_marked_unmeasured")
        else:
            reason = _OI_DEFER_REASON.get(status, f"oi_{status}")
            tasks.defer_task(task["task_id"], until=now + 6 * 3600, reason=reason, now=now)
            _bump(counters, "enrich_oi_deferred")


def _drain_run_sweep(tasks: FarmTasksDB, *, conn, private_root, profiles, policy, backend,
                     priority_base, limit, counters, now, sweep_tier="normal") -> None:
    from src.research_lab.data_fingerprint import fingerprint_for_symbol
    for _ in range(limit):
        task = tasks.claim_next_task(task_types=("run_sweep",), now=now)
        if task is None:
            break
        sym, tf, fam = task["symbol"], task["timeframe"], task["family"]
        try:
            payload = json.loads(task.get("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        glob = market_data_glob(private_root, tf)
        fp = fingerprint_for_symbol(glob, sym, tf) or task.get("data_fingerprint") or "nofp"
        if isinstance(payload.get("sweep_spec"), dict):
            spec = _sweep_from_payload(payload["sweep_spec"])
        else:
            spec = build_sweep_spec(sym, tf, fam, fingerprint=fp, backend=backend, tier=sweep_tier)
        exp_id, job_id, created = queue_sweep(
            conn,
            spec,
            private_root=private_root,
            profiles=profiles,
            policy=policy,
            data_glob=glob,
            fingerprint=fp,
            priority=priority_base + priority_value(task.get("priority")),
            event_context=_event_context_from_payload(payload),
        )
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
                    task_type="export_validation", task_key=f'export::{uc["uc_key"]}',
                    symbol=uc["symbol"], timeframe=uc["timeframe"], family=uc["family"],
                    params_hash=uc["params_hash"], data_fingerprint=uc["data_fingerprint"],
                    payload={"candidate_id": uc["candidate_id"], "uc_key": uc["uc_key"]}, now=now)
                if created:
                    _bump(counters, "exports_created")
        tasks.complete_task(task["task_id"], reason="classified", now=now)
        _bump(counters, "classified")


def _drain_worker(private_root, max_jobs: int, night_mode: bool, errors: list) -> None:
    if max_jobs <= 0:
        return
    from scripts.strategy_lab.worker_once import run_worker_once
    for _ in range(max_jobs):
        try:
            status = run_worker_once(private_root, night_mode=night_mode, ignore_cadence=True)
        except Exception as exc:  # noqa: BLE001 - record, then stop draining this cycle
            errors.append({"where": "worker", "error": f"{type(exc).__name__}: {exc}"})
            break
        if status.get("status") in {"queue_empty", "deferred"}:
            break


# ── pivot ─────────────────────────────────────────────────────────────────────
def _decide_pivot(tasks: FarmTasksDB, *, new_tasks: int, did_work: bool, now: float,
                  snapshot, families, data_state_fn, max_discovery: int, counters: dict,
                  gate_index=None) -> str:
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
                               data_state_fn, counters, now, gate_index)
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
    data_state_fn: Callable | None = None, provider=None, flow_provider=None, oi_provider=None,
    apply: bool = False,
    now: float | None = None, now_ms: int | None = None, backend: str = "auto",
    data_days: int | None = None, max_plan_events: int = 20, max_prepares: int = 4,
    max_enrich: int = 4, max_sweeps: int = 4, max_classify: int = 8, run_worker: bool = False,
    max_worker_jobs: int = 4, night_mode: bool = False, priority_base: int = 100,
    allow_public_output: bool = False, discovery_snapshot=None, max_discovery: int = 20,
    run_validation: bool = False, max_validations: int = 10,
    run_followups: bool = True, max_followups: int = 10, sweep_tier: str = "normal",
    use_outcome_memory: bool = True,
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
    errors: list[dict] = []

    # Read-through Setup Outcome Memory (built once per cycle from the brain; read-only): a
    # repeated signal consults prior outcomes before a fresh sweep is keyed. Off => fresh always.
    gate_index = build_gate_index(tasks.unique_candidates_for_gate()) if use_outcome_memory else None

    for ev in (intake_events or []):
        _, created = tasks.upsert_intake_event(ev, now=now)
        if created:
            _bump(counters, "events_ingested")
    fresh = tasks.unconsumed_events(limit=max_plan_events)
    new_tasks = _plan_events(tasks, fresh, families, data_state_fn, counters, now, gate_index)
    _unblock(tasks, data_state_fn, counters, now)
    _park_terminal_prepare_provider_errors(tasks, private_root=private_root, counters=counters, now=now)

    conn = None
    if apply:
        from src.research_lab.state_db import connect, default_db_path, init_db
        conn = connect(default_db_path(private_root))
        init_db(conn)
    try:
        if apply:
            _drain_prepare(tasks, private_root=private_root, provider=provider, now_ms=now_ms,
                           data_days=data_days, allow_public=allow_public_output, limit=max_prepares,
                           counters=counters, now=now, data_state_fn=data_state_fn, gate_index=gate_index)
            _drain_enrich(tasks, private_root=private_root, flow_provider=flow_provider, now_ms=now_ms,
                          limit=max_enrich, counters=counters, now=now)
            _drain_enrich_oi(tasks, private_root=private_root, oi_provider=oi_provider, now_ms=now_ms,
                             limit=max_enrich, counters=counters, now=now)
            if run_followups:
                _schedule_advisor_sweeps(tasks, private_root=private_root, counters=counters,
                                         now=now, limit=max_followups)
                _drain_advisor_sweeps(tasks, profiles=profiles, policy=policy, backend=backend,
                                      counters=counters, now=now, limit=max_followups)
            _drain_run_sweep(tasks, conn=conn, private_root=private_root, profiles=profiles, policy=policy,
                             backend=backend, priority_base=priority_base, limit=max_sweeps,
                             counters=counters, now=now, sweep_tier=sweep_tier)
            _sync_completions(tasks, conn=conn, counters=counters, now=now)
            if run_worker:
                _drain_worker(private_root, max_worker_jobs, night_mode, errors)
                _sync_completions(tasks, conn=conn, counters=counters, now=now)
            _classify_due(tasks, private_root=private_root, limit=max_classify, counters=counters, now=now)
            if run_followups:
                _schedule_due_followups(tasks, private_root=private_root, counters=counters,
                                        now=now, limit=max_followups)
                _drain_followups(tasks, profiles=profiles, policy=policy, limit=max_followups,
                                 counters=counters, now=now)
    finally:
        if conn is not None:
            conn.close()

    if apply and run_validation:
        from src.research_lab.validation_orchestrator import run_due_validations
        counters["validation"] = run_due_validations(tasks, private_root, apply=True,
                                                      limit=max_validations, now=now)
        if run_followups:
            _schedule_due_followups(tasks, private_root=private_root, counters=counters,
                                    now=now, limit=max_followups)

    did_work = any(counters[k] for k in ("prepared_ok", "enriched_ok", "enriched_oi_ok",
                                         "sweeps_materialized", "runs_completed", "classified",
                                         "unblocked", "followup_sweeps_planned",
                                         "outcome_retest_sweeps_planned",
                                         "followups_scheduled", "advisor_sweeps_planned"))
    pivot = _decide_pivot(tasks, new_tasks=new_tasks, did_work=did_work, now=now,
                          snapshot=discovery_snapshot, families=families, data_state_fn=data_state_fn,
                          max_discovery=max_discovery, counters=counters, gate_index=gate_index)
    return {"counters": counters, "pivot": pivot, "active_tasks": _count_active(tasks),
            "status": tasks.status_counts(), "errors": errors}


def main() -> None:  # pragma: no cover - thin CLI shim lives in scripts/
    raise SystemExit("use scripts/strategy_lab/farm_loop.py")
