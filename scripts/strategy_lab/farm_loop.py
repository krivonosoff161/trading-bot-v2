# -*- coding: utf-8 -*-
"""Continuous research-farm loop - the self-deciding intake -> compute -> validation cycle.

Each cycle: read scanner WATCH/GO watches (+ optional OKX-discovery refill) -> normalize
to intake events -> plan/prepare/enrich/run_sweep/classify/validate as typed lifecycle
tasks -> never spin on already_queued (it pivots to discovery / deferred work or reports
"blocked: no eligible tasks"). dry-run plans into an in-memory task DB and writes nothing.

    python -m scripts.strategy_lab.farm_loop --once --dry-run
    python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --enrich-funding
    python -m scripts.strategy_lab.farm_loop --loop --apply --run-worker --run-validation --run-paper --sleep-seconds 180

Safety: paper/research only. Public OKX market data only. Default path never touches
.env, AUTO_TRADE, order execution, private exchange endpoints, or Telegram credentials.
Telegram paper delivery is an explicit opt-in surface.
"""

from __future__ import annotations

import argparse
import _thread
import json
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TASK_CLAIM_LEASE_SECONDS = 900.0
STATUS_PUBLISH_MAX_OUTAGE_SECONDS = 120.0
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.farm_coordinator import (  # noqa: E402
    DEFAULT_FAMILIES,
    PriorityWorkerFatalError,
    run_coordinator_cycle,
)
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path  # noqa: E402
from src.research_lab.intake_adapter import watches_to_intake  # noqa: E402
from src.research_lab.ownership import (  # noqa: E402
    OwnershipConflictError,
    OwnershipStore,
    current_process_identity,
    probe_process_identity,
)
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.process_lease_supervisor import (  # noqa: E402
    ProcessLeaseSupervisor,
)
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.storage_capability import is_link_or_reparse  # noqa: E402
from src.research_lab.task_claim_heartbeat import TaskClaimHeartbeat  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402


class FarmCycleStopRequested(RuntimeError):
    """A canonical stop intent cancelled an interruptible long farm stage."""


class _ValidationGenerationWaiting(RuntimeError):
    """A fresh validation generation is still being produced for this revision."""


def _env_flag(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, SystemError, ValueError):
        return False
    return True


class _FarmLeaseHeartbeat(ProcessLeaseSupervisor):
    """Canonical farm lease renewal outside the foreground GIL domain."""

    def __init__(self, path: Path, lease, **kwargs) -> None:
        state = Path(path).parent
        super().__init__(
            path,
            lease,
            status_path=state / "farm_process_lease_status.json",
            alert_path=state / "farm_process_lease_alerts.jsonl",
            stop_path=state / "STOP_FARM_FULL_CYCLE.txt",
            **kwargs,
        )


def _task_claim_guard_factory(
    ownership_path,
    process_lease,
    stop_event,
    *,
    on_failure=None,
    stop_requested=None,
):
    """Bind every long task claim to the one canonical process generation."""

    def build(tasks: FarmTasksDB, task: dict):
        return TaskClaimHeartbeat(
            tasks,
            task,
            ownership_path=Path(ownership_path),
            process_lease=process_lease,
            stop_event=stop_event,
            lease_seconds=TASK_CLAIM_LEASE_SECONDS,
            renew_interval_seconds=30.0,
            max_no_progress_seconds=300.0,
            on_failure=on_failure,
            stop_requested=stop_requested,
        )

    return build


def _paper_telegram_delivery_config(args, *, apply: bool) -> dict:
    """Resolve the explicit paper Telegram delivery transport.

    The farm loop must stay preview/dry-run by default. Network delivery is
    allowed only when the operator explicitly enables it via CLI/env. Loading
    `.env` is delayed until that point so normal research cycles remain
    credential-free.
    """
    send_enabled = bool(getattr(args, "send_paper_telegram", False))
    if not (apply and send_enabled):
        return {
            "apply": False,
            "configured": False,
            "ids": [],
            "send_text": None,
            "send_photo": None,
        }

    try:
        from scripts.strategy_lab.paper_telegram_transport import (
            build_subscription_delivery_config,
        )

        config = build_subscription_delivery_config(ROOT)
    except Exception as exc:  # noqa: BLE001 - delivery config must not crash the farm
        return {
            "apply": True,
            "configured": False,
            "ids": [],
            "send_text": None,
            "send_photo": None,
            "config_error": type(exc).__name__,
        }
    return {
        "apply": True,
        "configured": bool(config.get("configured")),
        "ids": list(config.get("ids") or []),
        "send_text": config.get("send_text"),
        "send_photo": config.get("send_photo"),
    }


def _read_intake(
    limit: int,
    *,
    tasks: FarmTasksDB | None = None,
    metrics: dict[str, Any] | None = None,
) -> list[dict]:
    """Read open scanner watches (file only - never imports the scanner module)."""
    try:
        from src.scout.watch_queue import open_watches

        watches = open_watches()
        normalized = watches_to_intake(watches)
        known = (
            tasks.existing_intake_event_ids(
                str(event.get("event_id") or "") for event in normalized
            )
            if tasks is not None
            else set()
        )
        selected = watches_to_intake(
            watches,
            known_event_ids=known,
            limit=limit,
        )
        if metrics is not None:
            unseen = [
                event
                for event in normalized
                if str(event.get("event_id") or "") not in known
            ]
            now = time.time()
            metrics.update(
                {
                    "open_watches": len(watches),
                    "normalized_events": len(normalized),
                    "already_ingested": len(known),
                    "uningested_events": len(unseen),
                    "selected": len(selected),
                    "remaining_after_selection": max(0, len(unseen) - len(selected)),
                    "oldest_uningested_age_seconds": max(
                        (now - float(event.get("observed_at") or now) for event in unseen),
                        default=0.0,
                    ),
                }
            )
        return selected
    except Exception as exc:  # noqa: BLE001 - a missing/locked watch file must not crash the farm
        print(f"  intake: no watches ({type(exc).__name__})")
        return []


def _stage_status(args, apply: bool) -> dict:
    """Which closing-the-loop stages are active this run, and why a stage is skipped.

    worker/validation/paper are 'critical': with any of them OFF in apply mode the loop
    only QUEUES work but never computes/validates/papers it - the silent partial-loop
    foot-gun. enrich_* are non-critical (they only widen which families have data).
    """

    def st(enabled, flag: str, critical: bool) -> dict:
        return {
            "enabled": bool(enabled),
            "skipped_reason": None if enabled else f"flag {flag} off",
            "critical": critical,
        }

    return {
        "worker": st(args.run_worker, "--run-worker", True),
        "validation": st(args.run_validation, "--run-validation", True),
        "paper": st(args.run_paper, "--run-paper", True),
        "enrich_funding": st(args.enrich_funding, "--enrich-funding", False),
        "enrich_oi": st(args.enrich_oi, "--enrich-oi", False),
        "journal_export": st(
            getattr(args, "run_journal_export", False), "--run-journal-export", False
        ),
    }


def _print_stages(stages: dict, apply: bool) -> None:
    line = " ".join(
        f"{name}={'ON' if s['enabled'] else 'OFF'}" for name, s in stages.items()
    )
    print(f"stages: {line}")
    if apply:
        off_critical = [
            name for name, s in stages.items() if s["critical"] and not s["enabled"]
        ]
        if off_critical:
            flags = {
                "worker": "--run-worker",
                "validation": "--run-validation",
                "paper": "--run-paper",
            }
            need = " ".join(flags[n] for n in off_critical if n in flags)
            print(
                "WARNING: apply run with critical stage(s) OFF: "
                + ", ".join(off_critical)
                + " - the loop will QUEUE work but not "
                + "/".join(off_critical)
                + " it. "
                + f"Add {need} to close the loop "
                + "(or use bat\\strategy_lab_farm_full_cycle_loop.bat)."
            )


def _discovery(args, private_root: Path, apply: bool):
    """Load the discovery snapshot, auto-refreshing a stale/missing one when allowed.

    A stale snapshot must never silently degrade the farm to blocked:no_eligible: in
    apply mode (unless --no-discovery-refresh) we refresh through the TTL-throttled
    discover() (network only when actually stale); otherwise we warn loudly with the
    command hint and run on whatever snapshot exists.
    Returns (snapshot_or_None, info_dict).
    """
    from src.research_lab import instrument_discovery as idisc

    ttl = args.discovery_ttl_seconds
    now_ms = int(time.time() * 1000)
    refreshed = False
    if apply and not args.no_discovery_refresh:
        try:
            from scripts.strategy_lab.discover_okx_universe import discover

            res = discover(private_root, apply=True, now_ms=now_ms, ttl_seconds=ttl)
            refreshed = res.get("status") == "discovered"
            if res.get("status") == "fetch_failed":
                print(
                    f"  discovery: refresh failed ({res.get('reason')}) - using existing snapshot"
                )
            elif refreshed:
                print(
                    f"  discovery: refreshed snapshot (count={res.get('count')}, "
                    f"new={res.get('diff', {}).get('new')})"
                )
        except Exception as exc:  # noqa: BLE001 - discovery refresh must never crash the farm
            print(f"  discovery: refresh skipped ({type(exc).__name__})")
    snap = idisc.load_snapshot(private_root)
    if not snap.get("instruments"):
        print(
            "  WARNING discovery snapshot MISSING - run: "
            "python -m scripts.strategy_lab.discover_okx_universe --apply"
        )
        return None, {"status": "missing", "age_seconds": None, "count": 0}
    age = idisc.snapshot_age_seconds(snap, now_ms)
    fresh = idisc.is_fresh(snap, now_ms, ttl)
    if not fresh and not refreshed:
        print(
            f"  WARNING discovery snapshot STALE (age={age}s > ttl={ttl}s) - run: "
            "python -m scripts.strategy_lab.discover_okx_universe --apply "
            "(or remove --no-discovery-refresh in apply mode)"
        )
        status = "stale_no_refresh"
    else:
        status = "refreshed" if refreshed else "fresh"
    return snap, {
        "status": status,
        "age_seconds": age,
        "count": int(snap.get("count") or 0),
    }


def _live_universe_snapshot_info(private_root: Path, now: float) -> dict:
    path = Path(private_root) / "discovery" / "live_universe.json"
    if not path.exists():
        return {"status": "missing", "age_seconds": None, "count": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "invalid", "age_seconds": None, "count": 0}
    generated_at = data.get("generated_at")
    age_seconds: int | None = None
    try:
        age_seconds = max(0, int(float(now) - float(generated_at)))
    except (TypeError, ValueError):
        age_seconds = None
    count = sum(len(rows or []) for rows in (data.get("detail") or {}).values())
    return {"status": "loaded", "age_seconds": age_seconds, "count": int(count)}


def _refresh_live_universe(
    args, private_root: Path, apply: bool, *, now: float | None = None
) -> dict:
    """Refresh the movement-ranked paper-signal universe when stale.

    The regular discovery snapshot tracks which instruments exist. Paper signals use a separate
    movement-ranked live_universe snapshot; if that goes stale, the paper lane burns fetch budget on
    dead/no-data symbols. This refresh uses only the public OKX tickers endpoint.
    """
    now = time.time() if now is None else now
    ttl = int(getattr(args, "live_universe_ttl_seconds", 15 * 60))
    info = _live_universe_snapshot_info(private_root, now)
    age = info.get("age_seconds")
    is_fresh = age is not None and age < ttl and info.get("count", 0) > 0
    if is_fresh:
        return {**info, "status": "fresh", "refreshed": False, "ttl_seconds": ttl}
    if not apply or getattr(args, "no_live_universe_refresh", False):
        status = "stale_no_refresh" if info.get("count", 0) else info["status"]
        return {**info, "status": status, "refreshed": False, "ttl_seconds": ttl}
    try:
        from src.research_lab.live_universe_selector import (
            apply_intake,
            run,
            write_snapshot,
        )

        top_n = int(getattr(args, "live_universe_top_n", 12))
        result = run(private_root, top_n_per_group=top_n, now=now)
        write_snapshot(private_root, result, generated_at=now)
        applied = apply_intake(private_root, result.get("intake_events") or [], now=now)
        selected = result.get("selected") or {}
        count = sum(len(rows or []) for rows in selected.values())
        return {
            "status": "refreshed",
            "refreshed": True,
            "ttl_seconds": ttl,
            "age_seconds": 0,
            "count": int(count),
            "tickers_seen": int(result.get("tickers_seen") or 0),
            "intake_events": len(result.get("intake_events") or []),
            "registered": int(applied.get("registered") or 0),
            "duplicate": int(applied.get("duplicate") or 0),
        }
    except Exception as exc:  # noqa: BLE001 - stale refresh must not kill the farm
        return {
            **info,
            "status": f"refresh_failed:{type(exc).__name__}",
            "refreshed": False,
            "ttl_seconds": ttl,
            "error": str(exc)[:160],
        }


def _maybe_storage_maintain(private_root: Path, apply: bool) -> dict[str, Any]:
    if not apply:
        return {"state": "dry_run", "activated": False}
    from src.research_lab.runtime_storage_rotation import (
        archive_pending_segments,
        maybe_runtime_storage_capability,
        storage_budget_status,
    )

    capability = maybe_runtime_storage_capability(Path(private_root) / "sentinel")
    if capability is not None:
        maintenance = archive_pending_segments(capability)
        budget = storage_budget_status(capability)
        if maintenance["state"] != "ready" or budget["state"] != "ready":
            raise RuntimeError("activated runtime storage maintenance failed closed")
        return {
            "state": "ready",
            "activated": True,
            "archived": int(maintenance.get("archived") or 0),
            "retained": int(maintenance.get("retained") or 0),
            "budget_state": str(budget.get("state") or ""),
            "paper_only": True,
            "execution_allowed": False,
        }
    try:
        from src.research_lab.farm_journal import farm_log_paths
        from src.research_lab.storage_policy import bound_farm_artifacts, maintain

        maintain(farm_log_paths(private_root), apply=False)
        bound_farm_artifacts(private_root, apply=False)
        return {
            "state": "report_only",
            "activated": False,
            "paper_only": True,
            "execution_allowed": False,
        }
    except Exception as exc:  # noqa: BLE001 - storage hygiene must never break the loop
        print(f"  storage: skipped ({type(exc).__name__})")
        return {
            "state": "degraded",
            "activated": False,
            "problem_type": type(exc).__name__,
            "paper_only": True,
            "execution_allowed": False,
        }


def _providers(args, apply: bool):
    provider = flow_provider = oi_provider = None
    if apply:
        from src.research_lab.market_data_provider import get_provider

        provider = get_provider(
            args.provider, allow_synthetic=(args.provider == "synthetic")
        )
        if args.enrich_funding:
            from src.research_lab.providers.okx_flow import OkxPublicFundingProvider

            flow_provider = OkxPublicFundingProvider()
        if args.enrich_oi:
            from src.research_lab.providers.okx_flow import (
                OkxPublicOpenInterestProvider,
            )

            oi_provider = OkxPublicOpenInterestProvider()
    return provider, flow_provider, oi_provider


def _run_main_paper_derived_chain(
    args,
    private_root: Path,
    *,
    tasks: FarmTasksDB,
    apply: bool,
    loop: bool,
    cycle_started_at: float,
    out: dict,
    provider=None,
) -> None:
    """Refresh paper-only main artifacts after paper state changes.

    This is intentionally local/derived work: no live orders, no AUTO_TRADE, and no
    Telegram delivery. Network market data is used only by the bounded runtime observer
    through the explicit public provider already selected for the farm cycle.
    """
    paper_generation_runtime = getattr(args, "paper_generation_runtime", None)
    if paper_generation_runtime is not None:
        _run_v2_main_paper_derived_chain(
            args,
            private_root,
            tasks=tasks,
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
            out=out,
            provider=provider,
        )
        return
    if apply and getattr(args, "paper_evidence_v2_required", False):
        raise RuntimeError("required Paper Evidence v2 runtime is unavailable")
    _run_legacy_main_paper_derived_chain(
        args,
        private_root,
        tasks=tasks,
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        out=out,
        provider=provider,
    )


def _require_current_paper_generation(
    stage: str,
    payload: dict,
    *,
    run_id: str,
    nested: str | None = None,
) -> None:
    evidence = payload.get(nested) if nested else payload
    evidence = evidence if isinstance(evidence, dict) else {}
    if (
        evidence.get("current_generation_compatible") is not True
        or str(evidence.get("paper_generation_run_id") or "") != run_id
    ):
        raise RuntimeError(f"{stage} is not bound to current v2 generation")


def _publish_farm_product_checkpoint(private_root: Path, out: dict) -> None:
    """Publish only a completed, generation-bound product boundary."""

    from src.research_lab.product_progress import farm_metrics, publish_checkpoint

    completed_at = time.time()
    metrics = farm_metrics(out)
    publish_checkpoint(
        private_root,
        component="farm",
        sequence=max(1, int(completed_at * 1_000_000)),
        status=(
            "waiting"
            if metrics.get("paper_generation_waiting")
            else "completed"
            if not out.get("errors")
            else "degraded"
        ),
        metrics=metrics,
        completed_at=completed_at,
    )


def _run_v2_main_paper_derived_chain(
    args,
    private_root: Path,
    *,
    tasks: FarmTasksDB,
    apply: bool,
    loop: bool,
    cycle_started_at: float,
    out: dict,
    provider=None,
) -> None:
    """Publish one atomic v2 generation, then rebuild only bound consumers.

    Unlike the legacy compatibility path above, any failure is propagated to the
    canonical farm owner.  That prevents a stale preview, training export or delivery
    file from surviving as if it belonged to the new generation.
    """
    if not apply or provider is None:
        raise RuntimeError(
            "Paper Evidence v2 requires apply mode and an explicit provider"
        )
    runtime = args.paper_generation_runtime
    runtime.raise_if_failed()
    from src.research_lab.validation_generation import (
        load_current_generation_snapshot,
        load_pending_generation,
    )

    validation_generation = load_current_generation_snapshot(private_root)
    if validation_generation.status in {"legacy_absent", "pending", "code_stale"}:
        pending_generation = load_pending_generation(private_root) or {}
        out["paper_generation_v2"] = {
            "state": "waiting_validation_generation",
            "validation_generation_status": validation_generation.status,
            "validation_generation_id": validation_generation.generation_id,
            "validation_generation_started_at": float(
                pending_generation.get("producer_time") or 0.0
            ),
            "run_id": "",
            "current": False,
            "producer_membership": {
                "active_executable_signals": 0,
                "validation_bound_members": 0,
                "research_only_excluded": 0,
                "authority_source": "pfr_farm",
            },
            "paper_only": True,
            "execution_allowed": False,
        }
        _write_loop_status(
            private_root,
            stage="paper_generation_v2",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
            details={
                "milestone": "waiting_validation_generation",
                "validation_generation_status": validation_generation.status,
            },
        )
        raise _ValidationGenerationWaiting(validation_generation.status)
    if validation_generation.status not in {"ready", "ready_empty"}:
        raise RuntimeError(
            "validation generation is invalid for Paper Evidence v2: "
            f"{validation_generation.status}"
        )
    _write_loop_status(
        private_root,
        stage="paper_generation_v2",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={"milestone": "generation_started"},
    )
    generation = runtime.run(
        provider=provider,
        now_ms=int(time.time() * 1000),
        validation_generation_id=validation_generation.generation_id,
    )
    run_id = str(generation["run_id"])

    evidence_database_path = runtime.database_path
    args.paper_generation_run_id = run_id
    out["paper_generation_v2"] = {
        "state": "ready",
        "validation_generation_status": validation_generation.status,
        "validation_generation_id": validation_generation.generation_id,
        "run_id": run_id,
        "producer_generation_id": generation["producer_generation_id"],
        "account_generation_id": generation["account_generation_id"],
        "producer_membership": dict(generation["producer_membership"]),
        "current": True,
        "paper_only": True,
        "execution_allowed": False,
    }
    out["main_paper_bridge"] = generation["bridge"]
    out["main_paper_consumer"] = generation["consumer"]
    out["main_paper_runtime_queue"] = generation["queue"]
    out["main_paper_runtime_observation"] = generation["observer"]
    out["main_paper_trade_ledger"] = generation["trades"]
    _write_loop_status(
        private_root,
        stage="paper_generation_v2",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={"milestone": "generation_promoted", "paper_generation_run_id": run_id},
    )

    feature_packet_ids = _generation_feature_packet_ids(
        generation.get("queue") or {},
        expected_run_id=run_id,
        private_root=private_root,
    )
    if getattr(args, "run_calculator_advisor", False):
        try:
            out["calculator_advisor"] = _run_calculator_advisor_stage(
                args,
                private_root,
                apply,
                feature_packet_ids=feature_packet_ids,
            )
        except Exception as exc:  # noqa: BLE001 - bounded advisory falls back deterministically
            out["calculator_advisor"] = {
                "schema": "CalculatorAdvisorStage.v1",
                "enabled": True,
                "requested": len(feature_packet_ids),
                "eligible": len(feature_packet_ids),
                "attempted": 0,
                "processed": 0,
                "accepted": 0,
                "fallback": len(feature_packet_ids),
                "skipped": 0,
                "deferred": 0,
                "blocked": max(1, len(feature_packet_ids)),
                "reason_counts": {f"pre_delivery_{type(exc).__name__}": 1},
                "pre_delivery": True,
                "paper_only": True,
                "execution_allowed": False,
            }
    else:
        out["calculator_advisor"] = {
            "schema": "CalculatorAdvisorStage.v1",
            "enabled": False,
            "requested": len(feature_packet_ids),
            "eligible": len(feature_packet_ids),
            "attempted": 0,
            "processed": 0,
            "accepted": 0,
            "fallback": len(feature_packet_ids),
            "skipped": len(feature_packet_ids),
            "deferred": 0,
            "blocked": 0,
            "reason_counts": (
                {"advisor_explicitly_disabled": len(feature_packet_ids)}
                if feature_packet_ids
                else {}
            ),
            "pre_delivery": True,
            "paper_only": True,
            "execution_allowed": False,
        }

    from src.research_lab.paper_telegram_preview import build_paper_telegram_preview
    from src.research_lab.paper_signals.training_export import export_training_rows
    from src.research_lab.paper_lineage import build_paper_lineage
    from src.research_lab.outcome_retest_result import build_outcome_retest_results

    out["paper_product_trade_ledger"] = {
        "skipped": "legacy_projection_not_authoritative_under_v2",
        "paper_generation_run_id": run_id,
        "paper_only": True,
        "execution_allowed": False,
    }
    out["trade_thesis_supervisor"] = {
        "skipped": "legacy_product_ledger_not_authoritative_under_v2",
        "paper_generation_run_id": run_id,
        "paper_only": True,
        "execution_allowed": False,
    }
    out["paper_telegram_preview"] = build_paper_telegram_preview(
        private_root,
        fetch_public_chart_candles=True,
        evidence_database_path=evidence_database_path,
    )
    preview = out["paper_telegram_preview"]
    if (
        preview.get("current_generation_compatible") is not True
        or str(preview.get("paper_generation_run_id") or "") != run_id
    ):
        raise RuntimeError(
            "paper Telegram preview is not bound to current v2 generation"
        )
    out["paper_signal_training_export"] = export_training_rows(
        private_root,
        evidence_database_path=evidence_database_path,
    )
    _require_current_paper_generation(
        "paper training export",
        out["paper_signal_training_export"],
        run_id=run_id,
    )
    out["product_signal_training_export"] = {
        "skipped": "separate_legacy_product_event_source_not_paper_v2_authority",
        "paper_generation_run_id": run_id,
        "paper_only": True,
        "execution_allowed": False,
    }
    out["paper_lineage_index"] = build_paper_lineage(
        private_root,
        evidence_database_path=evidence_database_path,
    )
    _require_current_paper_generation(
        "paper lineage", out["paper_lineage_index"], run_id=run_id
    )
    out["outcome_retest_results"] = build_outcome_retest_results(
        private_root,
        evidence_database_path=evidence_database_path,
    )
    _require_current_paper_generation(
        "outcome retest",
        out["outcome_retest_results"],
        run_id=run_id,
        nested="training_evidence",
    )
    runtime.raise_if_failed()
    _write_loop_status(
        private_root,
        stage="paper_generation_v2",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={
            "milestone": "delivery_inputs_ready",
            "paper_generation_run_id": run_id,
        },
    )
def _run_v2_post_delivery_maintenance_chain(
    args,
    private_root: Path,
    *,
    tasks: FarmTasksDB,
    apply: bool,
    loop: bool,
    cycle_started_at: float,
    out: dict,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Run bounded analyst maintenance after generation-bound delivery."""

    runtime = args.paper_generation_runtime

    def check_active() -> None:
        runtime.raise_if_failed()
        failure_signal = getattr(args, "task_claim_failure_signal", None)
        if failure_signal is not None:
            failure_signal.raise_if_failed()
        if should_stop is not None and should_stop():
            raise FarmCycleStopRequested(
                "canonical stop requested during v2 post-delivery maintenance"
            )

    check_active()
    run_id = str((out.get("paper_generation_v2") or {}).get("run_id") or "")
    if not run_id:
        raise RuntimeError("post-delivery maintenance requires a current v2 generation")
    _require_current_paper_generation(
        "paper Telegram delivery",
        out.get("paper_telegram_delivery") or {},
        run_id=run_id,
    )
    evidence_database_path = runtime.database_path

    from src.research_lab.paper_product_quality_report import (
        build_paper_product_quality_report,
    )
    from src.research_lab.role_environment_dispatch import (
        dispatch_role_environments,
        reconcile_role_work_results,
    )
    from src.research_lab.system_analyst_cycle import run_system_analyst_cycle
    from src.research_lab.trading_policy_calibration import (
        build_trading_policy_calibration,
    )

    _write_loop_status(
        private_root,
        stage="paper_generation_v2",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={
            "milestone": "system_analyst_started",
            "paper_generation_run_id": run_id,
        },
    )
    out["system_analyst_feedback"] = run_system_analyst_cycle(
        private_root,
        apply=apply,
        expected_generation_run_id=run_id,
        evidence_database_path=evidence_database_path,
        check_active=check_active,
    )
    check_active()
    _require_current_paper_generation(
        "system analyst", out["system_analyst_feedback"], run_id=run_id
    )
    _write_loop_status(
        private_root,
        stage="paper_generation_v2",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={
            "milestone": "system_analyst_completed",
            "paper_generation_run_id": run_id,
        },
    )
    accepted_environment_ids = (
        out["system_analyst_feedback"].get("accepted_environment_ids") or {}
    )
    _write_loop_status(
        private_root,
        stage="paper_generation_v2",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={
            "milestone": "role_dispatch_started",
            "paper_generation_run_id": run_id,
        },
    )
    out["role_environment_dispatch"] = dispatch_role_environments(
        private_root,
        tasks,
        apply=apply,
        limit_per_role=20,
        expected_generation_run_id=run_id,
        evidence_database_path=evidence_database_path,
        environment_ids_by_role=accepted_environment_ids,
        check_active=check_active,
    )
    _require_current_paper_generation(
        "role dispatch", out["role_environment_dispatch"], run_id=run_id
    )
    _write_loop_status(
        private_root,
        stage="paper_generation_v2",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={
            "milestone": "role_dispatch_completed",
            "paper_generation_run_id": run_id,
        },
    )
    out["role_work_result_reconciliation"] = reconcile_role_work_results(
        private_root,
        tasks,
        apply=apply,
        expected_generation_run_id=run_id,
        environment_ids_by_role=(
            out["role_environment_dispatch"].get("environment_ids") or {}
        ),
        check_active=check_active,
    )
    check_active()
    _require_current_paper_generation(
        "role result reconciliation",
        out["role_work_result_reconciliation"],
        run_id=run_id,
    )
    _write_loop_status(
        private_root,
        stage="paper_generation_v2",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={
            "milestone": "role_maintenance_completed",
            "paper_generation_run_id": run_id,
        },
    )
    out["trading_policy_calibration"] = build_trading_policy_calibration(
        private_root,
        evidence_database_path=evidence_database_path,
    )
    _require_current_paper_generation(
        "trading policy calibration",
        out["trading_policy_calibration"],
        run_id=run_id,
    )
    out["setup_outcome_memory_refresh"] = _refresh_setup_outcome_memory(
        args,
        private_root,
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        evidence_database_path=evidence_database_path,
    )
    _require_current_paper_generation(
        "setup outcome memory",
        out["setup_outcome_memory_refresh"],
        run_id=run_id,
    )
    runtime.raise_if_failed()
    out["paper_product_quality_report"] = build_paper_product_quality_report(
        private_root,
        evidence_database_path=evidence_database_path,
    )
    _require_current_paper_generation(
        "paper product quality",
        out["paper_product_quality_report"],
        run_id=run_id,
    )
    _write_loop_status(
        private_root,
        stage="paper_generation_v2",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={
            "milestone": "generation_consumers_completed",
            "paper_generation_run_id": run_id,
        },
    )
    # Delivery, deterministic generation consumers, analyst routing, role
    # reconciliation, calibration, setup memory, and the quality report are the
    # mandatory paper-product boundary. Calculator and broad role-review LLM
    # calls that follow are advisory maintenance: they remain observable and
    # bounded by the ordinary steady-state SLO, but cannot consume the whole RCC
    # cold-start budget before T+0.
    out["mandatory_product_cycle_complete"] = True
    _publish_farm_product_checkpoint(private_root, out)


def _run_legacy_main_paper_derived_chain(
    args,
    private_root: Path,
    *,
    tasks: FarmTasksDB,
    apply: bool,
    loop: bool,
    cycle_started_at: float,
    out: dict,
    provider=None,
) -> None:
    """Compatibility/display pipeline used only outside the canonical v2 launcher."""
    try:
        _write_loop_status(
            private_root,
            stage="main_paper_bridge",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.main_paper_bridge import export_main_paper_instructions

        out["main_paper_bridge"] = export_main_paper_instructions(private_root)
    except Exception as exc:  # noqa: BLE001 - legacy compatibility surface
        out.setdefault("errors", []).append(
            {"where": "main_paper_bridge", "error": str(exc)}
        )
    try:
        _write_loop_status(
            private_root,
            stage="main_paper_consumer",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.main_paper_consumer import consume_main_paper_instructions

        out["main_paper_consumer"] = consume_main_paper_instructions(private_root)
    except Exception as exc:  # noqa: BLE001 - paper consumer must not break the cycle
        out.setdefault("errors", []).append(
            {"where": "main_paper_consumer", "error": str(exc)}
        )
    try:
        _write_loop_status(
            private_root,
            stage="main_paper_runtime_queue",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.main_paper_runtime_adapter import (
            build_main_paper_runtime_queue,
        )

        out["main_paper_runtime_queue"] = build_main_paper_runtime_queue(private_root)
    except Exception as exc:  # noqa: BLE001 - runtime queue must not break the cycle
        out.setdefault("errors", []).append(
            {"where": "main_paper_runtime_queue", "error": str(exc)}
        )
    try:
        _write_loop_status(
            private_root,
            stage="main_paper_runtime_observation",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
            details={"limit": int(getattr(args, "main_paper_runtime_limit", 50))},
        )
        from src.research_lab.main_paper_runtime import observe_main_paper_runtime

        runtime_limit = int(getattr(args, "main_paper_runtime_limit", 50))
        out["main_paper_runtime_observation"] = observe_main_paper_runtime(
            private_root,
            limit=runtime_limit,
            apply=apply and runtime_limit != 0,
            provider=provider,
        )
    except Exception as exc:  # noqa: BLE001 - paper observer must not break the cycle
        out.setdefault("errors", []).append(
            {
                "where": "main_paper_runtime_observation",
                "error": str(exc),
            }
        )
    try:
        _write_loop_status(
            private_root,
            stage="main_paper_trade_ledger",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.main_paper_trade_ledger import (
            build_main_paper_trade_ledger,
        )

        out["main_paper_trade_ledger"] = build_main_paper_trade_ledger(private_root)
    except Exception as exc:  # noqa: BLE001 - paper trade ledger must not break the cycle
        out.setdefault("errors", []).append(
            {"where": "main_paper_trade_ledger", "error": str(exc)}
        )
    try:
        _write_loop_status(
            private_root,
            stage="paper_product_trade_ledger",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.paper_product_trade_ledger import (
            build_paper_product_trade_ledger,
        )

        out["paper_product_trade_ledger"] = build_paper_product_trade_ledger(
            private_root
        )
    except Exception as exc:  # noqa: BLE001 - product ledger must not break the cycle
        out.setdefault("errors", []).append(
            {"where": "paper_product_trade_ledger", "error": str(exc)}
        )
    try:
        _write_loop_status(
            private_root,
            stage="trade_thesis_supervisor",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.trade_thesis_supervisor import (
            write_trade_thesis_supervisor,
        )

        out["trade_thesis_supervisor"] = write_trade_thesis_supervisor(private_root)
    except Exception as exc:  # noqa: BLE001 - thesis supervisor must not break the cycle
        out.setdefault("errors", []).append(
            {"where": "trade_thesis_supervisor", "error": str(exc)}
        )
    try:
        _write_loop_status(
            private_root,
            stage="paper_telegram_preview",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.paper_telegram_preview import build_paper_telegram_preview

        out["paper_telegram_preview"] = build_paper_telegram_preview(
            private_root,
            fetch_public_chart_candles=True,
        )
    except Exception as exc:  # noqa: BLE001 - preview surface must not break the cycle
        out.setdefault("errors", []).append(
            {"where": "paper_telegram_preview", "error": str(exc)}
        )
    try:
        _write_loop_status(
            private_root,
            stage="paper_signal_training_export",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.paper_signals.training_export import export_training_rows

        out["paper_signal_training_export"] = export_training_rows(private_root)
    except Exception as exc:  # noqa: BLE001 - training export must not break the cycle
        out.setdefault("errors", []).append(
            {
                "where": "paper_signal_training_export",
                "error": str(exc),
            }
        )
    try:
        _write_loop_status(
            private_root,
            stage="product_signal_training_export",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.product_signal_training import (
            export_product_signal_training,
        )

        out["product_signal_training_export"] = export_product_signal_training(
            private_root
        )
    except Exception as exc:  # noqa: BLE001 - product training export must not break the cycle
        out.setdefault("errors", []).append(
            {
                "where": "product_signal_training_export",
                "error": str(exc),
            }
        )
    try:
        _write_loop_status(
            private_root,
            stage="paper_lineage_index",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.paper_lineage import build_paper_lineage

        out["paper_lineage_index"] = build_paper_lineage(private_root)
    except Exception as exc:  # noqa: BLE001 - lineage audit must not break the cycle
        out.setdefault("errors", []).append(
            {"where": "paper_lineage_index", "error": str(exc)}
        )
    try:
        _write_loop_status(
            private_root,
            stage="outcome_retest_results",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.outcome_retest_result import build_outcome_retest_results

        out["outcome_retest_results"] = build_outcome_retest_results(private_root)
    except Exception as exc:  # noqa: BLE001 - retest reconciliation must not break the cycle
        out.setdefault("errors", []).append(
            {"where": "outcome_retest_results", "error": str(exc)}
        )
    try:
        _write_loop_status(
            private_root,
            stage="system_analyst_feedback",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.system_analyst_cycle import run_system_analyst_cycle

        out["system_analyst_feedback"] = run_system_analyst_cycle(
            private_root, apply=apply
        )
    except Exception as exc:  # noqa: BLE001 - advisory feedback must not break the cycle
        out.setdefault("errors", []).append(
            {"where": "system_analyst_feedback", "error": str(exc)}
        )
    try:
        _write_loop_status(
            private_root,
            stage="role_environment_dispatch",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.role_environment_dispatch import (
            dispatch_role_environments,
            reconcile_role_work_results,
        )

        out["role_environment_dispatch"] = dispatch_role_environments(
            private_root, tasks, apply=apply, limit_per_role=20
        )
        out["role_work_result_reconciliation"] = reconcile_role_work_results(
            private_root, tasks, apply=apply
        )
    except Exception as exc:  # noqa: BLE001 - role work must not break the cycle
        out.setdefault("errors", []).append(
            {"where": "role_environment_dispatch", "error": str(exc)}
        )
    try:
        _write_loop_status(
            private_root,
            stage="trading_policy_calibration",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.trading_policy_calibration import (
            build_trading_policy_calibration,
        )

        out["trading_policy_calibration"] = build_trading_policy_calibration(
            private_root
        )
    except Exception as exc:  # noqa: BLE001 - calibration report must not break the cycle
        out.setdefault("errors", []).append(
            {"where": "trading_policy_calibration", "error": str(exc)}
        )
    try:
        _write_loop_status(
            private_root,
            stage="setup_outcome_memory_refresh",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        out["setup_outcome_memory_refresh"] = _refresh_setup_outcome_memory(
            args,
            private_root,
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
    except FarmCycleStopRequested:
        raise
    except Exception as exc:  # noqa: BLE001 - memory refresh must not break the cycle
        out.setdefault("errors", []).append(
            {
                "where": "setup_outcome_memory_refresh",
                "error": str(exc),
            }
        )
    failure_signal = getattr(args, "task_claim_failure_signal", None)
    if failure_signal is not None:
        # A stage-local Exception handler must not turn lost ownership into a
        # best-effort report and continue into later materialization/delivery.
        failure_signal.raise_if_failed()
    try:
        _write_loop_status(
            private_root,
            stage="paper_product_quality_report",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.paper_product_quality_report import (
            build_paper_product_quality_report,
        )

        out["paper_product_quality_report"] = build_paper_product_quality_report(
            private_root
        )
    except Exception as exc:  # noqa: BLE001 - aggregate report must not break the cycle
        out.setdefault("errors", []).append(
            {
                "where": "paper_product_quality_report",
                "error": str(exc),
            }
        )


def _print_cycle(out: dict) -> None:
    c = out["counters"]
    interesting = {k: v for k, v in c.items() if isinstance(v, int) and v}
    print(f"  pivot={out['pivot']} active_tasks={out['active_tasks']}")
    print(
        "  " + (" ".join(f"{k}={v}" for k, v in interesting.items()) or "(no new work)")
    )
    st = out["status"]
    if st.get("by_state"):
        print("  states: " + " ".join(f"{k}={v}" for k, v in st["by_state"].items()))
    if st.get("blocked_reasons"):
        print(
            "  blocked: "
            + " ".join(f"{k}={v}" for k, v in st["blocked_reasons"].items())
        )
    if st.get("deferred_reasons"):
        print(
            "  deferred: "
            + " ".join(f"{k}={v}" for k, v in st["deferred_reasons"].items())
        )
    paper = out.get("paper") or {}
    if paper:
        pc = paper.get("counters") or {}
        readiness = paper.get("readiness") or {}
        shown = " ".join(
            f"{k}={v}"
            for k, v in pc.items()
            if isinstance(v, int) and (v or k == "cards")
        )
        print("  paper: " + (shown or "(no paper work)"))
        if readiness:
            print(
                "  paper_ready: "
                f"checked={readiness.get('checked_cards', 0)} "
                f"ready={readiness.get('paper_forward_ready', 0)} "
                f"plan_ready={readiness.get('plan_ready', 0)} "
                f"local_data_ready={readiness.get('local_data_ready', 0)}"
            )
            blockers = readiness.get("blocked_reasons") or {}
            if blockers:
                print(
                    "  paper_blocked: "
                    + " ".join(f"{k}={v}" for k, v in list(blockers.items())[:6])
                )
    ps_op = out.get("paper_signals") or {}
    if ps_op:
        pfr_c = {
            k: v
            for k, v in (ps_op.get("pfr_counts") or {}).items()
            if isinstance(v, int) and v
        }
        if pfr_c:
            print("  pfr_lane: " + " ".join(f"{k}={v}" for k, v in pfr_c.items()))
    exit_supervisor = out.get("paper_exit_supervisor") or {}
    if exit_supervisor:
        print(
            "  paper_exit_supervisor: "
            f"supervised={exit_supervisor.get('supervised', 0)} "
            f"by_action={exit_supervisor.get('by_action') or {}} "
            f"execution_allowed={exit_supervisor.get('execution_allowed')}"
        )
    mb = out.get("main_paper_bridge") or {}
    if mb:
        print(
            "  main_paper_bridge: "
            f"instructions={mb.get('instructions', 0)} "
            f"paper_only={mb.get('paper_only')} execution_allowed={mb.get('execution_allowed')}"
        )
    mc = out.get("main_paper_consumer") or {}
    if mc:
        print(
            "  main_paper_consumer: "
            f"read={mc.get('instructions_read', 0)} "
            f"accepted={mc.get('accepted', 0)} rejected={mc.get('rejected', 0)} "
            f"paper_only={mc.get('paper_only')} execution_allowed={mc.get('execution_allowed')}"
        )
    rtq = out.get("main_paper_runtime_queue") or {}
    if rtq:
        print(
            "  main_paper_runtime_queue: "
            f"read={rtq.get('rows_read', 0)} "
            f"queued={rtq.get('queued', 0)} invalid={rtq.get('invalid', 0)} "
            f"action={rtq.get('runtime_action')} execution_allowed={rtq.get('execution_allowed')}"
        )
        ap = rtq.get("adaptive_policy") or {}
        if ap:
            print(
                "  main_adaptive_policy: "
                f"policies={ap.get('policies', 0)} "
                f"by_profile={ap.get('by_execution_profile') or {}}"
            )
    rto = out.get("main_paper_runtime_observation") or {}
    if rto:
        print(
            "  main_paper_runtime_observation: "
            f"read={rto.get('rows_read', 0)} observed={rto.get('observed', 0)} "
            f"reviewed={rto.get('reviewed', 0)} pending={rto.get('pending', 0)} "
            f"invalid={rto.get('invalid', 0)} provider_error={rto.get('provider_error', 0)} "
            f"execution_allowed={rto.get('execution_allowed')}"
        )
    trade_ledger = out.get("main_paper_trade_ledger") or {}
    if trade_ledger:
        print(
            "  main_paper_trade_ledger: "
            f"trades={trade_ledger.get('trades', 0)} invalid={trade_ledger.get('invalid', 0)} "
            f"by_status={trade_ledger.get('by_status') or {}} "
            f"execution_allowed={trade_ledger.get('execution_allowed')}"
        )
    product_ledger = out.get("paper_product_trade_ledger") or {}
    if product_ledger:
        print(
            "  paper_product_trade_ledger: "
            f"trades={product_ledger.get('trades', 0)} "
            f"live_ready={product_ledger.get('live_ready', 0)} "
            f"live_blocked={product_ledger.get('live_blocked', 0)} "
            f"active={product_ledger.get('active_trades', 0)} "
            f"active_live_ready={product_ledger.get('active_live_ready', 0)} "
            f"active_live_blocked={product_ledger.get('active_live_blocked', 0)} "
            f"by_status={product_ledger.get('by_status') or {}} "
            f"execution_allowed={product_ledger.get('execution_allowed')}"
        )
    thesis = out.get("trade_thesis_supervisor") or {}
    if thesis:
        print(
            "  trade_thesis_supervisor: "
            f"theses={thesis.get('theses', 0)} "
            f"active={thesis.get('active_trades', 0)} "
            f"events={thesis.get('events', 0)} "
            f"by_action={thesis.get('by_action') or {}} "
            f"execution_allowed={thesis.get('execution_allowed')}"
        )
    tp = out.get("paper_telegram_preview") or {}
    if tp:
        print(
            "  paper_telegram_preview: "
            f"rendered={tp.get('rendered', 0)} invalid={tp.get('invalid', 0)} "
            f"quality_skip={tp.get('skipped_quality_gate', 0)} "
            f"sends_network={tp.get('sends_network')}"
        )
    td = out.get("paper_telegram_delivery") or {}
    if td:
        print(
            "  paper_telegram_delivery: "
            f"eligible_cards={td.get('eligible_cards', td.get('eligible', 0))} "
            f"targets={td.get('target_recipients', td.get('targets', 0))} "
            f"sent_messages={td.get('sent_messages', td.get('sent', 0))} "
            f"sent_cards={td.get('sent_cards', 0)} "
            f"duplicate_messages={td.get('duplicate_messages', td.get('duplicates', 0))} "
            f"duplicate_cards={td.get('duplicate_cards', 0)} "
            f"skipped_messages={td.get('skipped_messages', td.get('skipped', 0))} "
            f"errors={td.get('error_messages', td.get('errors', 0))} "
            f"status_digest_sent={td.get('status_digest_sent_messages', 0)} "
            f"status_digest_reason={td.get('status_digest_reason') or '-'} "
            f"dry_run={td.get('dry_run')} sends_network={td.get('sends_network')}"
        )
    train = out.get("paper_signal_training_export") or {}
    if train:
        print(
            "  paper_signal_training_export: "
            f"rows={train.get('rows', 0)} terminal_only={train.get('terminal_only')} "
            f"paper_only={train.get('paper_only')}"
        )
    calibration = out.get("trading_policy_calibration") or {}
    if calibration:
        print(
            "  trading_policy_calibration: "
            f"trusted={calibration.get('trusted_terminal_rows', 0)} "
            f"legacy_excluded={calibration.get('legacy_rows_excluded', 0)} "
            f"ready={calibration.get('calibration_ready')} "
            f"verdicts={calibration.get('profile_verdicts') or {}} "
            f"execution_allowed={calibration.get('execution_allowed')}"
        )
    product_train = out.get("product_signal_training_export") or {}
    if product_train:
        print(
            "  product_signal_training_export: "
            f"rows={product_train.get('rows', 0)} source_rows={product_train.get('source_rows', 0)} "
            f"paper_only={product_train.get('paper_only')}"
        )
    memory_refresh = out.get("setup_outcome_memory_refresh") or {}
    if memory_refresh:
        print(
            "  setup_outcome_memory_refresh: "
            f"total={memory_refresh.get('total', 0)} "
            f"product_rows={memory_refresh.get('product_rows', 0)} "
            f"product_terminal={memory_refresh.get('product_terminal_rows', 0)} "
            f"product_pnl={memory_refresh.get('product_pnl_usdt', 0)} "
            f"paper_only={memory_refresh.get('paper_only')}"
        )
    quality = out.get("paper_product_quality_report") or {}
    if quality:
        print(
            "  paper_product_quality_report: "
            f"rows={quality.get('training_rows', 0)} active={quality.get('active_trades', 0)} "
            f"active_live_ready={quality.get('active_live_ready', 0)} "
            f"action={quality.get('operator_action')} "
            f"families={len(quality.get('families') or [])} "
            f"execution_allowed={quality.get('execution_allowed')}"
        )
    journal_export = out.get("journal_export") or {}
    if journal_export:
        print(
            "  journal_export: "
            f"status={journal_export.get('status') or journal_export.get('skipped')} "
            f"exists={journal_export.get('exists')} private_fills={journal_export.get('private_fills')} "
            f"execution_allowed={journal_export.get('execution_allowed')}"
        )
    advisor = out.get("calculator_advisor") or {}
    if advisor:
        print(
            "  calculator_advisor: "
            f"requested={advisor.get('requested', 0)} "
            f"eligible={advisor.get('eligible', 0)} "
            f"attempted={advisor.get('attempted', 0)} "
            f"accepted={advisor.get('accepted', 0)} "
            f"fallback={advisor.get('fallback', 0)} "
            f"skipped={advisor.get('skipped', 0)} blocked={advisor.get('blocked', 0)} "
            f"reason_counts={advisor.get('reason_counts', {})}"
        )
    role_reviews = out.get("agent_role_reviews") or {}
    if role_reviews:
        print(
            "  agent_role_reviews: "
            f"provider={role_reviews.get('provider')} configured={role_reviews.get('configured')} "
            f"reviews={role_reviews.get('reviews', 0)} accepted={role_reviews.get('accepted', 0)} "
            f"rejected={role_reviews.get('rejected', 0)}"
        )
    catalog = out.get("ready_strategy_catalog") or {}
    if catalog:
        print(
            "  ready_strategy_catalog: "
            f"loaded={catalog.get('records_loaded', 0)} "
            f"ready={catalog.get('ready', 0)} rejected={catalog.get('rejected_quality', 0)} "
            f"execution_allowed={catalog.get('execution_allowed')}"
        )
    for e in out.get("errors") or []:
        print(f"  ERROR [{e.get('where')}]: {e.get('error')}")


def _cycle_summary(out: dict) -> dict:
    """Compact operator-facing state for farm_loop_status.json.

    The verbose log remains the audit trail. This summary is for health/status
    tools that need to distinguish "pipeline broken" from "pipeline idle: no
    validator/PFR trigger on this market cycle".
    """
    paper_signals = out.get("paper_signals") or {}
    return {
        "pivot": out.get("pivot"),
        "active_tasks": out.get("active_tasks"),
        "errors": len(out.get("errors") or []),
        "pfr_counts": paper_signals.get("pfr_counts") or {},
        "main_paper_bridge": {
            "instructions": (out.get("main_paper_bridge") or {}).get("instructions", 0),
            "skip_reasons": (out.get("main_paper_bridge") or {}).get("skip_reasons")
            or {},
        },
        "main_paper_runtime": {
            "queued": (out.get("main_paper_runtime_queue") or {}).get("queued", 0),
            "observed": (out.get("main_paper_runtime_observation") or {}).get(
                "observed", 0
            ),
            "provider_error": (out.get("main_paper_runtime_observation") or {}).get(
                "provider_error", 0
            ),
        },
        "paper_product_trades": {
            "trades": (out.get("paper_product_trade_ledger") or {}).get("trades", 0),
            "live_ready": (out.get("paper_product_trade_ledger") or {}).get(
                "live_ready", 0
            ),
            "live_blocked": (out.get("paper_product_trade_ledger") or {}).get(
                "live_blocked", 0
            ),
            "active_trades": (out.get("paper_product_trade_ledger") or {}).get(
                "active_trades", 0
            ),
            "active_live_ready": (out.get("paper_product_trade_ledger") or {}).get(
                "active_live_ready", 0
            ),
            "active_live_blocked": (out.get("paper_product_trade_ledger") or {}).get(
                "active_live_blocked", 0
            ),
        },
        "paper_exit_supervisor": {
            "supervised": (out.get("paper_exit_supervisor") or {}).get("supervised", 0),
            "by_action": (out.get("paper_exit_supervisor") or {}).get("by_action")
            or {},
        },
        "trade_thesis_supervisor": {
            "theses": (out.get("trade_thesis_supervisor") or {}).get("theses", 0),
            "events": (out.get("trade_thesis_supervisor") or {}).get("events", 0),
            "by_action": (out.get("trade_thesis_supervisor") or {}).get("by_action")
            or {},
        },
        "system_analyst_feedback": {
            "feedback_candidates": (out.get("system_analyst_feedback") or {}).get(
                "feedback_candidates", 0
            ),
            "routed": (out.get("system_analyst_feedback") or {}).get("routed", 0),
            "role_environment_candidates": (
                (out.get("system_analyst_feedback") or {}).get(
                    "role_environment_candidates"
                )
                or {}
            ),
            "accepted_role_requests": (
                (out.get("system_analyst_feedback") or {}).get("accepted_role_requests")
                or {}
            ),
        },
        "telegram": {
            "preview_rendered": (out.get("paper_telegram_preview") or {}).get(
                "rendered", 0
            ),
            "preview_quality_skip": (out.get("paper_telegram_preview") or {}).get(
                "skipped_quality_gate", 0
            ),
            "preview_quality_skip_reasons": (
                (out.get("paper_telegram_preview") or {}).get("quality_gate_reasons")
                or {}
            ),
            "delivery_sent_messages": (
                (out.get("paper_telegram_delivery") or {}).get(
                    "sent_messages",
                    (out.get("paper_telegram_delivery") or {}).get("sent", 0),
                )
            ),
            "delivery_sent_cards": (out.get("paper_telegram_delivery") or {}).get(
                "sent_cards", 0
            ),
            "delivery_errors": (
                (out.get("paper_telegram_delivery") or {}).get(
                    "error_messages",
                    (out.get("paper_telegram_delivery") or {}).get("errors", 0),
                )
            ),
        },
        "calculator_advisor": {
            "requested": (out.get("calculator_advisor") or {}).get("requested", 0),
            "eligible": (out.get("calculator_advisor") or {}).get("eligible", 0),
            "attempted": (out.get("calculator_advisor") or {}).get("attempted", 0),
            "processed": (out.get("calculator_advisor") or {}).get("processed", 0),
            "accepted": (out.get("calculator_advisor") or {}).get("accepted", 0),
            "fallback": (out.get("calculator_advisor") or {}).get("fallback", 0),
            "blocked": (out.get("calculator_advisor") or {}).get("blocked", 0),
        },
        "paper_product_quality": {
            "operator_action": (out.get("paper_product_quality_report") or {}).get(
                "operator_action", ""
            ),
            "families": len(
                (out.get("paper_product_quality_report") or {}).get("families") or []
            ),
            "quality_labels": (out.get("paper_product_quality_report") or {}).get(
                "quality_labels"
            )
            or {},
        },
        "trading_policy_calibration": {
            "trusted_terminal_rows": (out.get("trading_policy_calibration") or {}).get(
                "trusted_terminal_rows", 0
            ),
            "legacy_rows_excluded": (out.get("trading_policy_calibration") or {}).get(
                "legacy_rows_excluded", 0
            ),
            "calibration_ready": (out.get("trading_policy_calibration") or {}).get(
                "calibration_ready", False
            ),
            "profile_verdicts": (out.get("trading_policy_calibration") or {}).get(
                "profile_verdicts"
            )
            or {},
        },
    }


def _cycle_signature(out: dict) -> tuple:
    """A change-signature so --loop doesn't reprint identical state every sleep tick."""
    nz = tuple(
        sorted(k for k, v in out["counters"].items() if isinstance(v, int) and v)
    )
    by_state = tuple(sorted(((out.get("status") or {}).get("by_state") or {}).items()))
    paper = out.get("paper") or {}
    paper_counters = tuple(sorted((paper.get("counters") or {}).items()))
    paper_ready = tuple(
        sorted((paper.get("readiness") or {}).get("blocked_reasons", {}).items())
    )
    main_consumer = tuple(sorted((out.get("main_paper_consumer") or {}).items()))
    main_runtime_queue = tuple(
        sorted((out.get("main_paper_runtime_queue") or {}).items())
    )
    main_runtime_observation = tuple(
        sorted((out.get("main_paper_runtime_observation") or {}).items())
    )
    main_trade_ledger = tuple(
        sorted((out.get("main_paper_trade_ledger") or {}).items())
    )
    product_trade_ledger = tuple(
        sorted((out.get("paper_product_trade_ledger") or {}).items())
    )
    trade_thesis = tuple(sorted((out.get("trade_thesis_supervisor") or {}).items()))
    telegram_preview = tuple(sorted((out.get("paper_telegram_preview") or {}).items()))
    telegram_delivery = tuple(
        sorted((out.get("paper_telegram_delivery") or {}).items())
    )
    training_export = tuple(
        sorted((out.get("paper_signal_training_export") or {}).items())
    )
    product_training_export = tuple(
        sorted((out.get("product_signal_training_export") or {}).items())
    )
    memory_refresh = tuple(
        sorted((out.get("setup_outcome_memory_refresh") or {}).items())
    )
    product_quality = tuple(
        sorted((out.get("paper_product_quality_report") or {}).items())
    )
    calibration = tuple(sorted((out.get("trading_policy_calibration") or {}).items()))
    calculator_advisor = tuple(sorted((out.get("calculator_advisor") or {}).items()))
    agent_role_reviews = tuple(sorted((out.get("agent_role_reviews") or {}).items()))
    ready_catalog = tuple(sorted((out.get("ready_strategy_catalog") or {}).items()))
    return (
        out.get("pivot"),
        nz,
        by_state,
        paper_counters,
        paper_ready,
        main_consumer,
        main_runtime_queue,
        main_runtime_observation,
        main_trade_ledger,
        product_trade_ledger,
        trade_thesis,
        telegram_preview,
        telegram_delivery,
        training_export,
        product_training_export,
        memory_refresh,
        product_quality,
        calibration,
        calculator_advisor,
        agent_role_reviews,
        ready_catalog,
        bool(out.get("errors")),
    )


def _parse_csv(value: str, *, default: tuple[str, ...]) -> tuple[str, ...]:
    items = tuple(item.strip() for item in str(value or "").split(",") if item.strip())
    return items or default


def _sleep_until_next_cycle(
    seconds: int,
    stop_file: str = "",
    *,
    wake_event: threading.Event | None = None,
) -> bool:
    deadline = time.monotonic() + max(1, seconds)
    while time.monotonic() < deadline:
        if stop_file and Path(stop_file).exists():
            return False
        wait_seconds = min(5.0, max(0.0, deadline - time.monotonic()))
        if wake_event is None:
            time.sleep(wait_seconds)
            continue
        if wake_event.wait(wait_seconds):
            wake_event.clear()
            if stop_file and Path(stop_file).exists():
                return False
            return True
    return True


def _provider_env(args) -> dict[str, str]:
    env = dict(os.environ)
    if args.calculator_provider:
        env["STRATEGY_LAB_LLM_ENABLED"] = "1"
        env["STRATEGY_LAB_LLM_PROVIDER"] = args.calculator_provider
    if args.calculator_model:
        env["STRATEGY_LAB_LLM_MODEL_CHEAP"] = args.calculator_model
    if args.calculator_base_url:
        env["STRATEGY_LAB_LLM_BASE_URL"] = args.calculator_base_url
    if args.calculator_timeout:
        env["STRATEGY_LAB_LLM_TIMEOUT"] = str(args.calculator_timeout)
    return env


_FEATURE_PACKET_ID = re.compile(r"fp_[a-f0-9]{16}")
_PAPER_QUEUE_SCHEMA = "main_paper_runtime_adapter.v1"
_PAPER_QUEUE_ITEM_SCHEMA = "MainPaperRuntimeQueueItem.v1"


def _canonical_queue_snapshot_path(private_root: Path, raw_path: Any) -> Path:
    text = str(raw_path or "").strip()
    candidate = Path(text)
    if not text or not candidate.is_absolute():
        raise RuntimeError("paper queue snapshot path is not canonical")
    expected = (
        Path(private_root) / "state" / "derived" / "main_paper_runtime_queue.json"
    )
    try:
        resolved = candidate.resolve(strict=True)
        expected_resolved = expected.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("paper queue snapshot is unavailable") from exc
    if resolved != expected_resolved or is_link_or_reparse(candidate):
        raise RuntimeError("paper queue snapshot escapes its canonical path")
    return resolved


def _generation_feature_packet_ids(
    queue_summary: dict[str, Any], *, expected_run_id: str, private_root: Path
) -> list[str]:
    """Load the exact validation-bound feature IDs for pre-delivery advice."""

    if str(queue_summary.get("paper_generation_run_id") or "") != expected_run_id:
        raise RuntimeError("paper queue is not bound to the current generation")
    path = _canonical_queue_snapshot_path(
        private_root, queue_summary.get("snapshot_path")
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("paper queue snapshot is unavailable") from exc
    if not isinstance(payload, dict) or payload.get("schema") != _PAPER_QUEUE_SCHEMA:
        raise RuntimeError("paper queue snapshot schema mismatch")
    if str(payload.get("paper_generation_run_id") or "") != expected_run_id:
        raise RuntimeError("paper queue snapshot generation mismatch")
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError("paper queue snapshot items must be a list")
    feature_ids: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("schema") != _PAPER_QUEUE_ITEM_SCHEMA:
            raise RuntimeError("paper queue item schema mismatch")
        if str(item.get("paper_generation_run_id") or "") != expected_run_id:
            raise RuntimeError("paper queue item generation mismatch")
        if str(item.get("validation_tier") or "") != "validated_pfr":
            continue
        feature_id = str(item.get("feature_packet_id") or "")
        if not _FEATURE_PACKET_ID.fullmatch(feature_id):
            raise RuntimeError("paper queue feature packet identity is invalid")
        feature_ids.append(feature_id)
    return list(dict.fromkeys(feature_ids))


def _canonical_feature_packet_path(private_root: Path, feature_id: str) -> Path:
    if not _FEATURE_PACKET_ID.fullmatch(feature_id):
        raise RuntimeError("feature packet identity is invalid")
    root = Path(private_root) / "features" / "decision"
    candidate = root / f"{feature_id}.json"
    if candidate.exists():
        try:
            resolved_root = root.resolve(strict=True)
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("feature packet path is unavailable") from exc
        if resolved.parent != resolved_root or is_link_or_reparse(candidate):
            raise RuntimeError("feature packet path escapes its canonical root")
        return resolved
    return candidate


def _run_calculator_advisor_stage(
    args,
    private_root: Path,
    apply: bool,
    *,
    feature_packet_ids: list[str] | None = None,
) -> dict[str, Any]:
    requested = list(dict.fromkeys(feature_packet_ids or ()))
    result: dict[str, Any] = {
        "schema": "CalculatorAdvisorStage.v1",
        "enabled": True,
        "requested": len(requested),
        "eligible": len(requested),
        "attempted": 0,
        "processed": 0,
        "accepted": 0,
        "fallback": len(requested),
        "skipped": 0,
        "deferred": 0,
        "blocked": 0,
        "reason_counts": {},
        "pre_delivery": feature_packet_ids is not None,
        "paper_only": True,
        "execution_allowed": False,
    }
    if not apply:
        result["skipped"] = len(requested) or 1
        result["reason_counts"] = {"dry_run": len(requested) or 1}
        return result
    max_calls = max(0, int(getattr(args, "calculator_advisor_max_calls", 1)))
    if max_calls < 1:
        result["skipped"] = len(requested) or 1
        result["reason_counts"] = {"cap_zero": len(requested) or 1}
        return result
    from src.research_lab.advisor_sweep_bridge import compile_sweep_proposals
    from src.research_lab.calculator_advisor import load_latest_calculator_advice
    from src.research_lab.feature_packet import (
        latest_feature_packet_path,
        load_feature_packet,
    )
    from src.research_lab.lineage_contract import write_cycle_link
    from src.research_lab.llm_provider import load_provider
    from src.research_lab.local_calculator_swarm import request_local_calculator_swarm

    if feature_packet_ids is None:
        latest = latest_feature_packet_path(private_root)
        packet_paths = [latest] if latest is not None else []
        result["requested"] = len(packet_paths)
        result["eligible"] = len(packet_paths)
        result["fallback"] = len(packet_paths)
    else:
        existing = load_latest_calculator_advice(private_root, set(requested))
        result["already_available"] = len(existing)
        result["accepted"] = len(existing)
        result["fallback"] = max(0, len(requested) - len(existing))
        pending_paths = [
            _canonical_feature_packet_path(private_root, feature_id)
            for feature_id in requested
            if feature_id not in existing
        ]
        budget_deferred = max(0, len(pending_paths) - max_calls)
        if budget_deferred:
            result["deferred"] += budget_deferred
            result["reason_counts"]["pre_delivery_budget_exhausted"] = (
                budget_deferred
            )
        packet_paths = pending_paths
    packet_paths = packet_paths[:max_calls]
    if not packet_paths:
        if requested and int(result.get("already_available") or 0) == len(requested):
            result["reason_counts"] = {"advice_already_available": len(requested)}
        elif result["eligible"]:
            result["deferred"] += int(result["eligible"])
            result["reason_counts"] = {
                "missing_feature_packet": int(result["eligible"])
            }
        return result
    provider = load_provider(_provider_env(args))
    items: list[dict[str, Any]] = []
    for packet_path in packet_paths:
        if packet_path is None or not packet_path.is_file():
            result["deferred"] += 1
            result["reason_counts"]["missing_feature_packet"] = (
                int(result["reason_counts"].get("missing_feature_packet") or 0) + 1
            )
            continue
        packet = load_feature_packet(packet_path)
        expected_feature_id = packet_path.stem
        if packet.feature_packet_id != expected_feature_id:
            raise RuntimeError("feature packet content identity mismatch")
        result["attempted"] += 1
        advice = request_local_calculator_swarm(
            private_root,
            packet,
            provider,
            allow_public_output=bool(getattr(args, "allow_public_output", False)),
        )
        reason = (
            "accepted"
            if advice.accepted
            else (advice.problems[0] if advice.problems else "llm_schema_reject")
        )
        result["processed"] += 1
        result["accepted"] += 1 if advice.accepted else 0
        result["blocked"] += 0 if advice.accepted else 1
        result["reason_counts"][reason] = (
            int(result["reason_counts"].get(reason) or 0) + 1
        )
        items.append(
            {
                "feature_packet_id": packet.feature_packet_id,
                "advisor_ref": advice.advisor_ref,
                "accepted": bool(advice.accepted),
            }
        )
        result["sweep_proposals"] = compile_sweep_proposals(private_root, advice)
        write_cycle_link(
            private_root,
            {
                "feature_packet_id": packet.feature_packet_id,
                "llm_interpretation_ref": advice.advisor_ref,
                "source": "calculator_advisor",
                "mode": packet.mode,
                "paper_only": True,
                "execution_allowed": False,
            },
        )
    result["items"] = items
    result["fallback"] = max(
        0, int(result["eligible"]) - int(result["accepted"])
    )
    if len(items) == 1:
        result["advisor_ref"] = items[0]["advisor_ref"]
        result["feature_packet_id"] = items[0]["feature_packet_id"]
    result["swarm_roles"] = [
        "calculator_context_classifier",
        "calculator_hypothesis_proposer",
        "calculator_hypothesis_critic",
    ]
    from src.research_lab.llm_invocation_ledger import invocation_summary

    result["invocations"] = invocation_summary(private_root)
    return result


def _run_journal_export_stage(private_root: Path, apply: bool) -> dict:
    """Rebuild the operator Excel journal from paper/training artifacts.

    The legacy journal builder has an optional private-fills path. The farm loop is
    paper/research only, so force that opt-in off while this stage runs and restore
    the caller environment afterwards.
    """
    if not apply:
        return {
            "schema": "journal_export_stage.v1",
            "skipped": "dry_run",
            "paper_only": True,
            "execution_allowed": False,
        }

    old_root = os.environ.get("TRADING_BOT_RESEARCH_ROOT")
    old_private_fills = os.environ.get("JOURNAL_ENABLE_PRIVATE_FILLS")
    os.environ["TRADING_BOT_RESEARCH_ROOT"] = str(private_root)
    os.environ["JOURNAL_ENABLE_PRIVATE_FILLS"] = "0"
    try:
        import scripts.build_journal as build_journal

        build_journal.build()
        journal_path = Path(
            getattr(build_journal, "JOURNAL_PATH", ROOT / "scripts" / "journal.xlsx")
        )
        return {
            "schema": "journal_export_stage.v1",
            "status": "rebuilt",
            "path": str(journal_path),
            "exists": journal_path.exists(),
            "size_bytes": journal_path.stat().st_size if journal_path.exists() else 0,
            "private_fills": False,
            "paper_only": True,
            "execution_allowed": False,
        }
    finally:
        if old_root is None:
            os.environ.pop("TRADING_BOT_RESEARCH_ROOT", None)
        else:
            os.environ["TRADING_BOT_RESEARCH_ROOT"] = old_root
        if old_private_fills is None:
            os.environ.pop("JOURNAL_ENABLE_PRIVATE_FILLS", None)
        else:
            os.environ["JOURNAL_ENABLE_PRIVATE_FILLS"] = old_private_fills


class _LoopStatusPublisher:
    """Keep the last good status visible across transient Windows contention."""

    _retry_delays = (0.05, 0.1, 0.2, 0.4)

    def __init__(self, path: Path) -> None:
        self.path = path
        self.consecutive_failures = 0
        self.outage_started_monotonic: float | None = None
        self.last_error_code: int | None = None

    @staticmethod
    def _is_transient(exc: OSError) -> bool:
        return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {
            5,
            32,
            33,
        }

    def publish(
        self,
        payload: dict,
        *,
        now_monotonic: float | None = None,
    ) -> bool:
        current = time.monotonic() if now_monotonic is None else float(now_monotonic)
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        text = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
        last_error: OSError | None = None
        try:
            try:
                temporary.write_text(text, encoding="utf-8")
            except OSError as exc:
                if not self._is_transient(exc):
                    raise
                last_error = exc
            else:
                for attempt in range(len(self._retry_delays) + 1):
                    try:
                        temporary.replace(self.path)
                        recovered = self.consecutive_failures
                        self.consecutive_failures = 0
                        self.outage_started_monotonic = None
                        self.last_error_code = None
                        if recovered:
                            print(
                                "status publication recovered after "
                                f"{recovered} failed update(s)"
                            )
                        return True
                    except OSError as exc:
                        if not self._is_transient(exc):
                            raise
                        last_error = exc
                        if attempt < len(self._retry_delays):
                            time.sleep(self._retry_delays[attempt])

            self.consecutive_failures += 1
            if self.outage_started_monotonic is None:
                self.outage_started_monotonic = current
            self.last_error_code = getattr(last_error, "winerror", None)
            if self.consecutive_failures == 1 or self.consecutive_failures % 10 == 0:
                print(
                    "WARNING: farm status publication degraded; "
                    "last-known-good status retained; "
                    f"failed_updates={self.consecutive_failures}"
                )
            return False
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # The fixed per-process temporary name prevents unbounded litter.
                pass

    def outage_seconds(self, *, now_monotonic: float | None = None) -> float:
        if self.outage_started_monotonic is None:
            return 0.0
        current = time.monotonic() if now_monotonic is None else float(now_monotonic)
        return max(0.0, current - self.outage_started_monotonic)


_LOOP_STATUS_PUBLISHERS: dict[Path, _LoopStatusPublisher] = {}
_PROCESS_LEASE_SUPERVISORS: dict[Path, _FarmLeaseHeartbeat] = {}


def _loop_status_publisher(private_root: Path) -> _LoopStatusPublisher:
    path = private_root / "state" / "farm_loop_status.json"
    publisher = _LOOP_STATUS_PUBLISHERS.get(path)
    if publisher is None:
        publisher = _LoopStatusPublisher(path)
        _LOOP_STATUS_PUBLISHERS[path] = publisher
    return publisher


def _record_process_lease_progress(private_root: Path, stage: str) -> None:
    supervisor = _PROCESS_LEASE_SUPERVISORS.get(
        Path(private_root) / "state" / "ownership.sqlite"
    )
    if supervisor is not None:
        supervisor.record_progress(stage)


def _status_publication_requires_stop(
    private_root: Path,
    *,
    max_outage_seconds: float,
    now_monotonic: float | None = None,
) -> bool:
    publisher = _LOOP_STATUS_PUBLISHERS.get(
        private_root / "state" / "farm_loop_status.json"
    )
    return bool(
        publisher
        and publisher.consecutive_failures
        and publisher.outage_seconds(now_monotonic=now_monotonic)
        > max(0.0, float(max_outage_seconds))
    )


def _leave_for_status_publication_outage(
    private_root: Path,
    *,
    max_outage_seconds: float,
) -> bool:
    if not _status_publication_requires_stop(
        private_root,
        max_outage_seconds=max_outage_seconds,
    ):
        return False
    publisher = _loop_status_publisher(private_root)
    print(
        "ERROR: farm status publication remained unavailable for "
        f"{publisher.outage_seconds():.1f}s; "
        "leaving at the completed-cycle boundary without restart"
    )
    return True


def _write_loop_status(
    private_root: Path,
    *,
    stage: str,
    apply: bool,
    loop: bool,
    cycle_started_at: float,
    details: dict | None = None,
) -> bool:
    """Write a private heartbeat for long visible runs.

    The loop prints a full summary only after a cycle finishes. Some stages can take
    minutes, so this status file is the operator-facing "where is it now" signal.
    """
    if not apply:
        return True
    path = private_root / "state" / "farm_loop_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    payload = {
        "schema": "FarmLoopStatus.v1",
        "pid": os.getpid(),
        "stage": stage,
        "updated_at": now,
        "cycle_started_at": cycle_started_at,
        "cycle_age_seconds": round(now - cycle_started_at, 3),
        "loop": bool(loop),
        "paper_only": True,
        "execution_allowed": False,
        "details": details or {},
    }
    published = _loop_status_publisher(private_root).publish(payload)
    if published:
        milestone = str((details or {}).get("milestone") or "").strip()
        progress_stage = f"{stage}:{milestone}" if milestone else stage
        _record_process_lease_progress(private_root, progress_stage)
        if milestone:
            from src.research_lab.product_progress import publish_checkpoint

            metrics: dict[str, int | str] = {
                "stage": str(stage)[:80],
                "milestone": milestone[:80],
            }
            for key in ("completed", "total"):
                value = (details or {}).get(key)
                if isinstance(value, int):
                    metrics[key] = value
            publish_checkpoint(
                private_root,
                component="farm_progress",
                sequence=max(1, int(now * 1_000_000)),
                status="progress",
                metrics=metrics,
                completed_at=now,
            )
    return published


def _run_paper_runtime(
    args,
    private_root: Path,
    *,
    apply: bool,
    loop: bool,
    cycle_started_at: float,
) -> dict[str, Any]:
    """Run paper evaluation with real completed-chunk lease progress."""

    from src.research_lab.paper_runtime import run_paper_cycle

    failure_signal = getattr(args, "task_claim_failure_signal", None)

    def check_active() -> None:
        if failure_signal is not None:
            failure_signal.raise_if_failed()
        stop_file = str(getattr(args, "stop_file", "") or "")
        if stop_file and Path(stop_file).exists():
            raise FarmCycleStopRequested(
                "canonical stop requested during paper runtime"
            )

    def progress(milestone: str, completed: int, total: int) -> None:
        check_active()
        _write_loop_status(
            private_root,
            stage="paper_runtime",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
            details={
                "milestone": milestone,
                "completed": int(completed),
                "total": int(total),
            },
        )
        check_active()

    check_active()
    return run_paper_cycle(
        private_root,
        apply=apply,
        limit=args.max_paper_cards,
        progress=progress,
        check_active=check_active,
    )


def _refresh_setup_outcome_memory(
    args,
    private_root: Path,
    *,
    apply: bool,
    loop: bool,
    cycle_started_at: float,
    evidence_database_path: Path | str | None = None,
) -> dict[str, Any]:
    """Run the production memory refresh with real, completed milestones."""

    from src.research_lab import setup_outcome_memory as memory

    failure_signal = getattr(args, "task_claim_failure_signal", None)

    def check_active() -> None:
        if failure_signal is not None:
            failure_signal.raise_if_failed()
        stop_file = str(getattr(args, "stop_file", "") or "")
        if stop_file and Path(stop_file).exists():
            raise FarmCycleStopRequested(
                "canonical stop requested during setup outcome memory refresh"
            )

    def progress(milestone: str, completed: int, total: int) -> None:
        check_active()
        _write_loop_status(
            private_root,
            stage="setup_outcome_memory_refresh",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
            details={
                "milestone": milestone,
                "completed": int(completed),
                "total": int(total),
            },
        )
        check_active()

    build_stats: dict[str, Any] = {}
    records = memory.build_memory_index(
        private_root,
        progress=progress,
        check_active=check_active,
        reject_cache_path=private_root
        / "state"
        / "derived"
        / "setup_outcome_memory_reject_cache.json",
        build_stats=build_stats,
    )
    summary = memory.summarize_memory(records)
    progress("memory_summarized", len(records), len(records))
    product_memory_evidence = memory.summarize_product_training_memory(
        private_root,
        evidence_database_path=evidence_database_path,
    )
    product_memory = product_memory_evidence["summary"]
    progress(
        "product_memory_summarized",
        int(product_memory.get("rows") or 0),
        int(product_memory.get("rows") or 0),
    )
    snapshot_path = memory.write_memory_snapshot(
        private_root,
        records=records,
        product_paper_memory=product_memory_evidence,
    )
    progress("snapshot_written", len(records), len(records))
    return {
        "schema": "setup_outcome_memory_refresh.v1",
        "snapshot_path": str(snapshot_path),
        "total": summary.get("total", 0),
        "paper_ready_without_hard_pass": summary.get(
            "paper_ready_without_hard_pass", 0
        ),
        "product_rows": product_memory.get("rows", 0),
        "product_terminal_rows": product_memory.get("terminal_rows", 0),
        "product_pnl_usdt": product_memory.get("paper_pnl_usdt", 0),
        "paper_generation_run_id": str(
            product_memory_evidence.get("paper_generation_run_id") or ""
        ),
        "generation_status": str(
            product_memory_evidence.get("generation_status") or ""
        ),
        "current_generation_compatible": bool(
            product_memory_evidence.get("current_generation_compatible")
        ),
        "reject_characterization": dict(
            build_stats.get("reject_characterization") or {}
        ),
        "paper_only": True,
        "execution_allowed": False,
    }


def _run_validation_maintenance(
    args,
    tasks: FarmTasksDB,
    private_root: Path,
    *,
    apply: bool,
    loop: bool,
    cycle_started_at: float,
    status_target: str = "farm_loop",
) -> dict[str, Any]:
    """Run one bounded validation batch with durable, real progress milestones."""
    failure_signal = getattr(args, "task_claim_failure_signal", None)
    max_validations = max(1, int(getattr(args, "max_validations", 10)))
    backlog_slo_seconds = max(
        1.0, float(getattr(args, "validation_backlog_slo_seconds", 3600.0))
    )
    from src.research_lab.validation_generation import (
        current_generation_manifest_status,
        pending_generation_manifest_status,
        validation_producer_code_digest,
    )

    producer_code_digest = validation_producer_code_digest()
    generation_status_at_start = current_generation_manifest_status(private_root)
    successor_build_started_at = 0.0
    if generation_status_at_start == "code_stale":
        prior_digest = str(
            getattr(args, "_validation_successor_code_digest", "") or ""
        )
        prior_started_at = float(
            getattr(args, "_validation_successor_build_started_at", 0.0) or 0.0
        )
        if prior_digest == producer_code_digest and prior_started_at > 0.0:
            successor_build_started_at = prior_started_at
        else:
            successor_build_started_at = float(cycle_started_at)
            args._validation_successor_build_started_at = successor_build_started_at
            args._validation_successor_code_digest = producer_code_digest
    else:
        args._validation_successor_build_started_at = 0.0
        args._validation_successor_code_digest = ""

    def backlog_snapshot() -> dict[str, Any]:
        metrics = getattr(tasks, "validation_backlog_metrics", None)
        if callable(metrics):
            return metrics()
        return {
            "active": 0,
            "eligible": 0,
            "by_state": {},
            "oldest_age_seconds": 0.0,
            "freshness_window_seconds": 3600.0,
            "fresh_eligible": 0,
            "fresh_oldest_eligible_age_seconds": 0.0,
            "window_seconds": 3600.0,
            "arrivals": 0,
            "terminal": 0,
            "arrival_count_method": "task_created_in_window",
            "service_count_method": "current_terminal_state_updated_in_window",
            "arrival_rate_per_hour": 0.0,
            "service_rate_per_hour": 0.0,
            "net_drain_rate_per_hour": 0.0,
            "drain_eta_hours": 0.0,
        }

    def publish(details: dict[str, Any]) -> None:
        payload = {
            **details,
            "max_validations": max_validations,
            "backlog_high_water": max(
                1, int(getattr(args, "validation_backlog_high_water", 256))
            ),
            "backlog_slo_seconds": backlog_slo_seconds,
        }
        if status_target == "priority_worker":
            _write_priority_worker_status(
                private_root,
                stage="validation_maintenance",
                started_at=cycle_started_at,
                details=payload,
            )
            return
        _write_loop_status(
            private_root,
            stage="validation_maintenance",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
            details=payload,
        )

    def check_active() -> None:
        if failure_signal is not None:
            failure_signal.raise_if_failed()
        stop_file = str(getattr(args, "stop_file", "") or "")
        if stop_file and Path(stop_file).exists():
            raise FarmCycleStopRequested(
                "canonical stop requested during validation maintenance"
            )

    def progress(milestone: str, completed: int, total: int) -> None:
        check_active()
        backlog = backlog_snapshot()
        publish(
            {
                "milestone": milestone,
                "completed": int(completed),
                "total": int(total),
                "backlog": backlog,
                "backlog_slo_breached": bool(
                    float(backlog["oldest_age_seconds"]) > backlog_slo_seconds
                ),
            }
        )
        if status_target == "priority_worker":
            from src.research_lab.product_progress import publish_checkpoint

            completed_at = time.time()
            successor_metrics: dict[str, Any] = {}
            if successor_build_started_at > 0.0:
                pending_status = pending_generation_manifest_status(private_root)
                current_status = current_generation_manifest_status(private_root)
                if current_status == "code_current":
                    successor_phase = "current_published"
                elif pending_status == "code_current":
                    successor_phase = "pending_marker"
                else:
                    successor_phase = "pre_marker"
                successor_metrics = {
                    "successor_build_phase": successor_phase,
                    "successor_build_started_at": successor_build_started_at,
                    "successor_code_digest": producer_code_digest,
                    "successor_marker_code_status": pending_status,
                    "successor_current_code_status": current_status,
                }
            publish_checkpoint(
                private_root,
                component="validation_progress",
                sequence=max(1, int(completed_at * 1_000_000)),
                status="progress",
                metrics={
                    "stage": "validation_maintenance",
                    "milestone": str(milestone)[:80],
                    "completed": int(completed),
                    "total": int(total),
                    **successor_metrics,
                    "validation_active": int(backlog.get("active") or 0),
                    "validation_eligible": int(backlog.get("eligible") or 0),
                    "validation_fresh_eligible": int(
                        backlog.get("fresh_eligible") or 0
                    ),
                    "validation_fresh_oldest_age_seconds": float(
                        backlog.get("fresh_oldest_eligible_age_seconds") or 0.0
                    ),
                    "validation_arrival_rate_per_hour": float(
                        backlog.get("arrival_rate_per_hour") or 0.0
                    ),
                    "validation_service_rate_per_hour": float(
                        backlog.get("service_rate_per_hour") or 0.0
                    ),
                    "validation_net_drain_rate_per_hour": float(
                        backlog.get("net_drain_rate_per_hour") or 0.0
                    ),
                },
                completed_at=completed_at,
            )
        check_active()

    check_active()
    backlog_before = backlog_snapshot()
    publish(
        {
            "milestone": "starting",
            "backlog": backlog_before,
            "backlog_slo_breached": bool(
                float(backlog_before["oldest_age_seconds"]) > backlog_slo_seconds
            ),
        }
    )
    from src.research_lab.validation_orchestrator import run_due_validations

    result = run_due_validations(
        tasks,
        private_root,
        apply=True,
        limit=max_validations,
        now=time.time(),
        progress=progress,
        check_active=check_active,
    )
    backlog_after = backlog_snapshot()
    result["backlog_before"] = backlog_before
    result["backlog_after"] = backlog_after
    result["backlog_high_water"] = max(
        1, int(getattr(args, "validation_backlog_high_water", 256))
    )
    result["backlog_slo_seconds"] = backlog_slo_seconds
    result["backlog_slo_breached"] = bool(
        float(backlog_after["oldest_age_seconds"]) > backlog_slo_seconds
    )
    return result


def _run_once(
    args, tasks: FarmTasksDB, profiles, policy, private_root: Path, apply: bool
) -> dict:
    cycle_started_at = time.time()
    loop = bool(getattr(args, "loop", False))

    def cycle_stop_requested() -> bool:
        stop_file = str(getattr(args, "stop_file", "") or "")
        return bool(stop_file and Path(stop_file).exists())

    _write_loop_status(
        private_root,
        stage="cycle_start",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
    )
    _write_loop_status(
        private_root,
        stage="provider_setup",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
    )
    provider, flow_provider, oi_provider = _providers(args, apply)
    _write_loop_status(
        private_root,
        stage="intake",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={"max_plan_events": int(getattr(args, "max_plan_events", 20))},
    )
    intake_metrics: dict[str, Any] = {}
    events = _read_intake(
        args.max_plan_events,
        tasks=tasks if apply else None,
        metrics=intake_metrics,
    )
    if apply and events:
        from src.research_lab.lineage_contract import (
            scanner_event_from_intake,
            write_scanner_event,
        )

        for event in events[: max(0, int(getattr(args, "max_plan_events", 20)))]:
            write_scanner_event(
                private_root, scanner_event_from_intake(event, mode="live")
            )
    _write_loop_status(
        private_root,
        stage="discovery",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={"intake_events": len(events)},
    )
    snapshot, discovery_info = _discovery(args, private_root, apply)
    _write_loop_status(
        private_root,
        stage="coordinator",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={"intake_events": len(events)},
    )
    if bool(getattr(args, "priority_worker_active", False)):
        # The dedicated worker is the sole coordinator/compute owner. The main
        # thread continues discovery + paper/role maintenance without racing the
        # same SQLite task graph.
        out: dict[str, Any] = {
            "pivot": "priority_worker_active",
            "active_tasks": tasks.eligible_count(),
            "counters": {"delegated_to_priority_worker": 1},
            "status": tasks.status_counts(),
            "errors": [],
        }
    else:
        out = run_coordinator_cycle(
            tasks,
            private_root=private_root,
            profiles=profiles,
            policy=policy,
            intake_events=events,
            families=DEFAULT_FAMILIES,
            provider=provider,
            flow_provider=flow_provider,
            oi_provider=oi_provider,
            apply=apply,
            backend=args.backend,
            data_days=args.data_days,
            max_plan_events=args.max_plan_events,
            max_prepares=args.max_prepares,
            max_enrich=args.max_enrich,
            max_sweeps=args.max_sweeps,
            run_worker=args.run_worker,
            max_worker_jobs=args.max_worker_jobs,
            night_mode=args.night_mode,
            allow_public_output=args.allow_public_output,
            discovery_snapshot=snapshot,
            max_discovery=args.max_plan_events,
            max_validations=int(getattr(args, "max_validations", 10)),
            validation_backlog_high_water=int(
                getattr(args, "validation_backlog_high_water", 256)
            ),
            run_validation=args.run_validation,
            run_followups=not getattr(args, "no_followups", False),
            max_followups=getattr(args, "max_followups", 10),
            sweep_tier=args.sweep_tier,
            task_claim_guard_factory=getattr(args, "task_claim_guard_factory", None),
        )
    out["discovery"] = discovery_info
    out["scanner_intake"] = intake_metrics

    def priority_checkpoint(after_stage: str) -> None:
        """Let urgent/GO work advance between heavyweight full-cycle stages."""
        if not (apply and loop) or bool(getattr(args, "priority_worker_active", False)):
            return
        slot = _run_priority_slot(args, tasks, profiles, policy, private_root)
        out.setdefault("priority_checkpoints", []).append(
            {
                "after_stage": after_stage,
                "did_work": _slot_did_work(slot),
                "counters": slot.get("counters") or {},
                "errors": len(slot.get("errors") or []),
            }
        )
        _write_priority_checkpoint(
            private_root,
            slot,
            sequence=len(out["priority_checkpoints"]),
        )

    priority_checkpoint("coordinator")
    if cycle_stop_requested():
        out["stop_requested"] = True
        return out
    if args.run_paper:
        _write_loop_status(
            private_root,
            stage="paper_runtime",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        paper = _run_paper_runtime(
            args,
            private_root,
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        out["paper"] = {
            "counters": paper.get("counters", {}),
            "readiness": paper.get("readiness", {}),
            "results": (paper.get("results") or [])[:10],
        }
        if not getattr(args, "run_paper_signals", False):
            try:
                _run_main_paper_derived_chain(
                    args,
                    private_root,
                    tasks=tasks,
                    apply=apply,
                    loop=loop,
                    cycle_started_at=cycle_started_at,
                    out=out,
                    provider=provider,
                )
            except _ValidationGenerationWaiting as exc:
                out["paper_telegram_delivery"] = {
                    "skipped": "validation_generation_waiting",
                    "validation_generation_status": str(exc),
                    "paper_only": True,
                    "execution_allowed": False,
                }
        priority_checkpoint("paper_runtime")
        if cycle_stop_requested():
            out["stop_requested"] = True
            return out
    if apply:
        # True-forward research lane: pin boundaries for the current watchlist (idempotent) and
        # accumulate forward outcomes on genuinely new local bars. Bounded + crash-isolated so a
        # research lane can never break the cycle. matured != edge; nothing paper-ready.
        tf_limit = int(getattr(args, "true_forward_max_candidates", 20))
        if tf_limit > 0:
            try:
                _write_loop_status(
                    private_root,
                    stage="true_forward",
                    apply=apply,
                    loop=loop,
                    cycle_started_at=cycle_started_at,
                    details={"max_candidates": tf_limit},
                )
                from src.research_lab import true_forward

                true_forward.register(private_root, max_candidates=tf_limit)
                tf_res = true_forward.collect_once(
                    private_root, max_candidates=tf_limit
                )
                out["true_forward"] = tf_res.get("summary", {})
            except Exception as exc:  # noqa: BLE001 - research lane must never break the cycle
                out.setdefault("errors", []).append(
                    {"where": "true_forward", "error": str(exc)}
                )
        else:
            out["true_forward"] = {"skipped": "true_forward_max_candidates=0"}
        priority_checkpoint("true_forward")
        if cycle_stop_requested():
            out["stop_requested"] = True
            return out
        if getattr(args, "run_paper_signals", False):
            # Operational paper-watch lane: one bounded cycle (observe armed -> close -> remember ->
            # generate new). Crash-isolated; paper/research-only, never an order.
            try:
                from src.research_lab.paper_signals import cycle as paper_cycle
                from src.research_lab.providers.okx_public import (
                    OkxPublicMarketDataProvider,
                    _httpx_get_direct,
                )

                raw_pfr_db = Path(getattr(args, "pfr_db_path", "") or "")
                _pfr_db: Path | None = (
                    raw_pfr_db if raw_pfr_db.as_posix() not in ("", ".") else None
                )
                if _pfr_db is not None:
                    try:
                        _write_loop_status(
                            private_root,
                            stage="ready_strategy_catalog",
                            apply=apply,
                            loop=loop,
                            cycle_started_at=cycle_started_at,
                            details={"pfr_db_path": str(_pfr_db)},
                        )
                        from src.research_lab.ready_strategy_catalog import (
                            build_ready_strategy_catalog,
                        )

                        out["ready_strategy_catalog"] = build_ready_strategy_catalog(
                            private_root, _pfr_db
                        )
                    except Exception as exc:  # noqa: BLE001 - catalog must not break the cycle
                        out.setdefault("errors", []).append(
                            {
                                "where": "ready_strategy_catalog",
                                "error": str(exc),
                            }
                        )
                _write_loop_status(
                    private_root,
                    stage="live_universe_refresh",
                    apply=apply,
                    loop=loop,
                    cycle_started_at=cycle_started_at,
                    details={
                        "ttl_seconds": int(
                            getattr(args, "live_universe_ttl_seconds", 15 * 60)
                        ),
                        "top_n": int(getattr(args, "live_universe_top_n", 12)),
                    },
                )
                out["live_universe"] = _refresh_live_universe(args, private_root, apply)
                paper_provider: Any = OkxPublicMarketDataProvider(
                    timeout=float(getattr(args, "paper_signals_fetch_timeout", 10.0)),
                    http_get=_httpx_get_direct,
                )
                try:
                    from src.research_lab.providers.local_first import (
                        LocalFirstMarketDataProvider,
                    )

                    paper_provider = LocalFirstMarketDataProvider(
                        private_root, paper_provider
                    )
                except Exception as exc:  # noqa: BLE001 - cache wrapper must not break public fallback
                    out.setdefault("errors", []).append(
                        {
                            "where": "local_first_market_data_provider",
                            "error": str(exc),
                        }
                    )
                paper_timeframes = _parse_csv(
                    getattr(args, "paper_signals_timeframes", ""),
                    default=("15m", "1h", "4h"),
                )
                _write_loop_status(
                    private_root,
                    stage="paper_signals",
                    apply=apply,
                    loop=loop,
                    cycle_started_at=cycle_started_at,
                    details={
                        "timeframes": list(paper_timeframes),
                        "max_pfr_scan": int(
                            getattr(args, "paper_signals_max_pfr_scan", 30)
                        ),
                        "max_pfr_fetches": int(
                            getattr(args, "paper_signals_max_pfr_fetches", 12)
                        ),
                        "max_live_fetches": int(
                            getattr(args, "paper_signals_max_live_fetches", 12)
                        ),
                        "max_network_fetches": int(
                            getattr(args, "paper_signals_max_network_fetches", 16)
                        ),
                        "max_wall_seconds": float(
                            getattr(args, "paper_signals_max_seconds", 45.0)
                        ),
                    },
                )
                out["paper_signals"] = paper_cycle.run_cycle(
                    private_root,
                    mode="live",
                    timeframes=paper_timeframes,
                    max_new=int(getattr(args, "paper_signals_max_new", 5)),
                    apply=True,
                    pfr_db_path=_pfr_db,
                    provider=paper_provider,
                    max_pfr_scan=int(getattr(args, "paper_signals_max_pfr_scan", 30)),
                    max_pfr_fetches=int(
                        getattr(args, "paper_signals_max_pfr_fetches", 12)
                    ),
                    pfr_reserved_new=int(
                        getattr(args, "paper_signals_pfr_reserved", 0)
                    ),
                    max_observe=getattr(args, "paper_signals_max_observe", None),
                    max_live_fetches=int(
                        getattr(args, "paper_signals_max_live_fetches", 12)
                    ),
                    max_network_fetches=int(
                        getattr(args, "paper_signals_max_network_fetches", 16)
                    ),
                    max_wall_seconds=float(
                        getattr(args, "paper_signals_max_seconds", 45.0)
                    ),
                    should_stop=(
                        lambda: bool(
                            getattr(args, "stop_file", "")
                            and Path(getattr(args, "stop_file", "")).exists()
                        )
                    ),
                )
                priority_checkpoint("paper_signals")
                if cycle_stop_requested():
                    out["stop_requested"] = True
                    return out
                _run_main_paper_derived_chain(
                    args,
                    private_root,
                    tasks=tasks,
                    apply=apply,
                    loop=loop,
                    cycle_started_at=cycle_started_at,
                    out=out,
                    provider=paper_provider,
                )
                if getattr(args, "paper_evidence_v2_required", False):
                    out["paper_exit_supervisor"] = {
                        "skipped": "legacy_thesis_projection_not_authoritative_under_v2",
                        "paper_generation_run_id": str(
                            getattr(args, "paper_generation_run_id", "") or ""
                        ),
                        "paper_only": True,
                        "execution_allowed": False,
                    }
                else:
                    try:
                        _write_loop_status(
                            private_root,
                            stage="paper_exit_supervisor",
                            apply=apply,
                            loop=loop,
                            cycle_started_at=cycle_started_at,
                        )
                        from src.research_lab.paper_exit_supervisor import (
                            write_exit_supervisor,
                        )

                        out["paper_exit_supervisor"] = write_exit_supervisor(
                            private_root
                        )
                    except Exception as exc:  # noqa: BLE001 - compatibility advice
                        out.setdefault("errors", []).append(
                            {"where": "paper_exit_supervisor", "error": str(exc)}
                        )
                try:
                    _write_loop_status(
                        private_root,
                        stage="paper_telegram_delivery",
                        apply=apply,
                        loop=loop,
                        cycle_started_at=cycle_started_at,
                    )
                    from src.research_lab.paper_telegram_sender import (
                        send_paper_telegram_previews,
                    )

                    delivery_config = _paper_telegram_delivery_config(args, apply=apply)
                    out["paper_telegram_delivery"] = send_paper_telegram_previews(
                        private_root,
                        limit=int(getattr(args, "paper_telegram_limit", 20)),
                        apply=bool(delivery_config["apply"]),
                        paper_chat_configured=bool(delivery_config["configured"]),
                        paper_chat_ids_count=len(delivery_config["ids"]),
                        recipient_ids=delivery_config["ids"],
                        send_text=delivery_config["send_text"],
                        send_photo=delivery_config["send_photo"],
                        status_digest=bool(
                            getattr(args, "paper_telegram_status_digest", False)
                        ),
                        status_digest_interval_hours=int(
                            getattr(args, "paper_telegram_status_digest_hours", 12)
                        ),
                        expected_generation_run_id=(
                            str(getattr(args, "paper_generation_run_id", "") or "")
                            if getattr(args, "paper_evidence_v2_required", False)
                            else None
                        ),
                    )
                    if getattr(args, "paper_evidence_v2_required", False) and out[
                        "paper_telegram_delivery"
                    ].get("generation_block_reason"):
                        raise RuntimeError(
                            "paper Telegram delivery source is not bound to current v2 generation"
                        )
                    if delivery_config.get("config_error"):
                        out["paper_telegram_delivery"]["config_error"] = (
                            delivery_config["config_error"]
                        )
                    if getattr(args, "paper_evidence_v2_required", False):
                        run_id = str(
                            (out.get("paper_generation_v2") or {}).get("run_id") or ""
                        )
                        _require_current_paper_generation(
                            "paper Telegram delivery",
                            out["paper_telegram_delivery"],
                            run_id=run_id,
                        )
                        _write_loop_status(
                            private_root,
                            stage="paper_generation_v2",
                            apply=apply,
                            loop=loop,
                            cycle_started_at=cycle_started_at,
                            details={
                                "milestone": "generation_delivery_completed",
                                "paper_generation_run_id": run_id,
                            },
                        )
                        _run_v2_post_delivery_maintenance_chain(
                            args,
                            private_root,
                            tasks=tasks,
                            apply=apply,
                            loop=loop,
                            cycle_started_at=cycle_started_at,
                            out=out,
                            should_stop=cycle_stop_requested,
                        )
                except Exception as exc:  # noqa: BLE001 - compatibility path is best effort
                    if getattr(args, "paper_evidence_v2_required", False):
                        raise
                    out.setdefault("errors", []).append(
                        {"where": "paper_telegram_delivery", "error": str(exc)}
                    )
                if getattr(args, "run_journal_export", False):
                    try:
                        _write_loop_status(
                            private_root,
                            stage="journal_export",
                            apply=apply,
                            loop=loop,
                            cycle_started_at=cycle_started_at,
                        )
                        out["journal_export"] = _run_journal_export_stage(
                            private_root, apply
                        )
                    except Exception as exc:  # noqa: BLE001 - journal export must not break the cycle
                        out.setdefault("errors", []).append(
                            {"where": "journal_export", "error": str(exc)}
                        )
                if getattr(args, "run_calculator_advisor", False) and not (
                    getattr(args, "paper_evidence_v2_required", False)
                    and "calculator_advisor" in out
                ):
                    if cycle_stop_requested():
                        out["stop_requested"] = True
                        return out
                    try:
                        _write_loop_status(
                            private_root,
                            stage="calculator_advisor",
                            apply=apply,
                            loop=loop,
                            cycle_started_at=cycle_started_at,
                            details={
                                "max_calls": int(
                                    getattr(args, "calculator_advisor_max_calls", 1)
                                )
                            },
                        )
                        out["calculator_advisor"] = _run_calculator_advisor_stage(
                            args, private_root, apply
                        )
                    except Exception as exc:  # noqa: BLE001 - advisory stage must not break the cycle
                        out.setdefault("errors", []).append(
                            {"where": "calculator_advisor", "error": str(exc)}
                        )
                priority_checkpoint("calculator_advisor")
                if getattr(args, "run_agent_role_reviews", False):
                    if cycle_stop_requested():
                        out["stop_requested"] = True
                        return out
                    try:
                        _write_loop_status(
                            private_root,
                            stage="agent_role_reviews",
                            apply=apply,
                            loop=loop,
                            cycle_started_at=cycle_started_at,
                        )
                        from argparse import Namespace
                        from scripts.strategy_lab.agent_role_review_cycle import (
                            run_cycle as run_role_review_cycle,
                        )

                        out["agent_role_reviews"] = run_role_review_cycle(
                            Namespace(
                                private_root=private_root,
                                provider=getattr(
                                    args, "agent_role_provider", "alibaba"
                                ),
                                base_url=getattr(args, "agent_role_base_url", ""),
                                api_key_env=getattr(
                                    args, "agent_role_api_key_env", "ALIBABA_API_KEY"
                                ),
                                model=getattr(args, "agent_role_model", ""),
                                timeout=float(
                                    getattr(args, "agent_role_timeout", 60.0)
                                ),
                                rate_rub_per_1k=float(
                                    getattr(args, "agent_role_rate_rub_per_1k", 0.0)
                                ),
                                max_outcomes=int(
                                    getattr(args, "agent_role_max_outcomes", 1)
                                ),
                                max_validator=int(
                                    getattr(args, "agent_role_max_validator", 1)
                                ),
                                max_sources=int(
                                    getattr(args, "agent_role_max_sources", 1)
                                ),
                                max_analyst=int(
                                    getattr(args, "agent_role_max_analyst", 1)
                                ),
                                sleep_seconds=float(
                                    getattr(args, "agent_role_sleep_seconds", 0.0)
                                ),
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 - advisory reviews must not break the cycle
                        out.setdefault("errors", []).append(
                            {"where": "agent_role_reviews", "error": str(exc)}
                        )
            except FarmCycleStopRequested:
                raise
            except _ValidationGenerationWaiting as exc:
                out["paper_telegram_delivery"] = {
                    "skipped": "validation_generation_waiting",
                    "validation_generation_status": str(exc),
                    "paper_only": True,
                    "execution_allowed": False,
                }
            except Exception as exc:  # noqa: BLE001 - paper lane must never break the cycle
                out.setdefault("errors", []).append(
                    {"where": "paper_signals", "error": str(exc)}
                )
    stages = _stage_status(args, apply)
    out["stages"] = stages
    if apply:
        _write_loop_status(
            private_root,
            stage="farm_journal",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab import farm_journal

        farm_journal.log_cycle(
            private_root,
            ts=time.time(),
            mode="apply",
            result=out,
            stages=stages,
            discovery=discovery_info,
        )
        for e in out.get("errors") or []:
            farm_journal.log_error(
                private_root,
                where=e.get("where", "cycle"),
                error=e.get("error", ""),
                ts=time.time(),
            )
    _write_loop_status(
        private_root,
        stage="storage_maintenance",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
    )
    out["runtime_storage_maintenance"] = _maybe_storage_maintain(private_root, apply)
    _write_loop_status(
        private_root,
        stage="cycle_complete",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
        details={
            "last_summary": _cycle_summary(out),
        },
    )
    if apply:
        from src.research_lab.validation_generation import (
            load_pending_generation,
            pending_generation_payload_status,
        )

        pending_generation = load_pending_generation(private_root) or {}
        out["validation_generation_build"] = {
            "active": bool(pending_generation),
            "generation_id": str(pending_generation.get("generation_id") or ""),
            "started_at": float(pending_generation.get("producer_time") or 0.0),
            "code_status": pending_generation_payload_status(
                pending_generation or None
            ),
        }
        backlog_reader = getattr(tasks, "validation_backlog_metrics", None)
        validation_backlog = (
            backlog_reader()
            if callable(backlog_reader)
            else {
                "active": 0,
                "eligible": 0,
                "oldest_age_seconds": 0.0,
                "freshness_window_seconds": 3600.0,
                "fresh_eligible": 0,
                "fresh_oldest_eligible_age_seconds": 0.0,
            }
        )
        validation_backlog["backlog_slo_seconds"] = max(
            1.0,
            float(getattr(args, "validation_backlog_slo_seconds", 3600.0)),
        )
        out["validation_backlog"] = validation_backlog
        out["product_cycle_complete"] = True
        _publish_farm_product_checkpoint(private_root, out)
    return out


def _run_priority_slot(
    args, tasks: FarmTasksDB, profiles, policy, private_root: Path
) -> dict:
    """Advance one short compute slot between expensive full farm cycles.

    The slot reads fresh WATCH/GO/manual intake, prepares at most one missing
    dataset, materializes one sweep and runs one worker job.  It deliberately
    skips discovery refresh, paper delivery, validation and LLM roles.  Those
    remain on the bounded full-cycle cadence, while queued numeric work no
    longer sleeps for minutes.
    """
    provider, flow_provider, oi_provider = _providers(args, True)
    intake_metrics: dict[str, Any] = {}
    events = _read_intake(
        min(8, max(1, int(args.max_plan_events))),
        tasks=tasks,
        metrics=intake_metrics,
    )
    out = run_coordinator_cycle(
        tasks,
        private_root=private_root,
        profiles=profiles,
        policy=policy,
        intake_events=events,
        families=DEFAULT_FAMILIES,
        provider=provider,
        flow_provider=flow_provider,
        oi_provider=oi_provider,
        apply=True,
        backend=args.backend,
        data_days=args.data_days,
        max_plan_events=min(8, max(1, int(args.max_plan_events))),
        max_prepares=1,
        max_enrich=0,
        max_sweeps=1,
        max_classify=2,
        validation_backlog_high_water=int(
            getattr(args, "validation_backlog_high_water", 256)
        ),
        run_worker=True,
        max_worker_jobs=1,
        night_mode=args.night_mode,
        allow_public_output=args.allow_public_output,
        discovery_snapshot=None,
        max_discovery=0,
        run_validation=False,
        run_followups=False,
        max_followups=0,
        sweep_tier=args.sweep_tier,
        task_claim_guard_factory=getattr(args, "task_claim_guard_factory", None),
    )
    out["scanner_intake"] = intake_metrics
    return out


def _slot_did_work(slot: dict) -> bool:
    counters = slot.get("counters") or {}
    validation = slot.get("validation_maintenance") or {}
    return any(
        int(counters.get(name) or 0) > 0
        for name in (
            "events_ingested",
            "events_consumed",
            "prepared_ok",
            "sweeps_materialized",
            "runs_completed",
            "classified",
            "unblocked",
        )
    ) or any(
        int(validation.get(name) or 0) > 0
        for name in (
            "validated",
            "exported",
            "orphan_tasks_skipped",
            "ineligible_tasks_skipped",
            "retry_exhausted_skipped",
        )
    )


def _write_priority_checkpoint(
    private_root: Path, slot: dict, *, sequence: int
) -> Path:
    """Persist the slot boundary; restart resumes from durable queue state."""
    target = private_root / "state" / "farm_priority_checkpoint.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "FarmPriorityCheckpoint.v1",
        "updated_at": time.time(),
        "sequence": int(sequence),
        "pivot": slot.get("pivot"),
        "active_tasks": int(slot.get("active_tasks") or 0),
        "queue": (slot.get("status") or {}).get("by_state", {}),
        "counters": slot.get("counters") or {},
        "errors": len(slot.get("errors") or []),
        "resume_mode": "requeue_atomic_slot_from_durable_ledgers",
        "paper_only": True,
        "execution_allowed": False,
    }
    temporary = target.with_name(f"{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        for attempt in range(5):
            try:
                temporary.replace(target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)
    _record_process_lease_progress(private_root, "priority_worker:checkpoint")
    return target


def _write_priority_worker_status(
    private_root: Path,
    *,
    stage: str,
    started_at: float,
    details: dict | None = None,
) -> Path:
    target = private_root / "state" / "farm_priority_worker_status.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "FarmPriorityWorkerStatus.v1",
        "pid": os.getpid(),
        "stage": stage,
        "updated_at": time.time(),
        "slot_started_at": started_at,
        "slot_age_seconds": round(max(0.0, time.time() - started_at), 3),
        "paper_only": True,
        "execution_allowed": False,
        "details": details or {},
    }
    temporary = target.with_name(f"{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        for attempt in range(5):
            try:
                temporary.replace(target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)
    _record_process_lease_progress(private_root, f"priority_worker:{stage}")
    return target


class _TaskClaimFailureSignal:
    """Make a background claim failure immediately visible to the farm foreground."""

    def __init__(
        self,
        private_root: Path,
        stop_event: threading.Event,
        *,
        interrupt_main=_thread.interrupt_main,
    ) -> None:
        self.private_root = Path(private_root)
        self.stop_event = stop_event
        self._interrupt_main = interrupt_main
        self._lock = threading.Lock()
        self._failure: BaseException | None = None
        self._snapshot: dict = {}

    @property
    def failure(self) -> BaseException | None:
        with self._lock:
            return self._failure

    def notify(self, failure: BaseException, snapshot: dict) -> None:
        with self._lock:
            if self._failure is not None:
                return
            self._failure = failure
            self._snapshot = dict(snapshot)
        self.stop_event.set()
        failure_kind = str(snapshot.get("failure_kind") or "task_claim")
        failure_stage = (
            "worker_failed"
            if failure_kind == "compute_worker_lifecycle"
            else "owner_lease_failed"
            if failure_kind == "process_lease"
            else "claim_failed"
        )
        try:
            _write_priority_worker_status(
                self.private_root,
                stage=failure_stage,
                started_at=time.time(),
                details={
                    "failure_kind": failure_kind,
                    "error_type": type(failure).__name__,
                    "task_id": snapshot.get("task_id"),
                    "task_fencing_token": snapshot.get("task_fencing_token"),
                    "process_fencing_token": snapshot.get("process_fencing_token"),
                    "last_progress_stage": snapshot.get("last_progress_stage"),
                    "last_progress_age_seconds": snapshot.get(
                        "last_progress_age_seconds"
                    ),
                    "failure": snapshot.get("failure"),
                },
            )
        finally:
            # Status I/O must not delay or suppress the foreground fail-closed path.
            self._interrupt_main()

    def raise_if_failed(self) -> None:
        failure = self.failure
        if failure is not None:
            with self._lock:
                failure_kind = str(self._snapshot.get("failure_kind") or "task_claim")
            message = (
                "priority compute worker failed closed"
                if failure_kind == "compute_worker_lifecycle"
                else "canonical process ownership heartbeat failed"
                if failure_kind == "process_lease"
                else "priority task claim heartbeat failed"
            )
            raise RuntimeError(message) from failure

    def status_details(self) -> dict:
        with self._lock:
            snapshot = dict(self._snapshot)
            failure = self._failure
        return {
            "failure_kind": snapshot.get("failure_kind"),
            "error_type": None if failure is None else type(failure).__name__,
            "task_id": snapshot.get("task_id"),
            "task_fencing_token": snapshot.get("task_fencing_token"),
            "process_fencing_token": snapshot.get("process_fencing_token"),
            "last_progress_stage": snapshot.get("last_progress_stage"),
            "last_progress_age_seconds": snapshot.get("last_progress_age_seconds"),
            "failure": snapshot.get("failure"),
        }


def _priority_worker_loop(
    args, profiles, policy, private_root: Path, stop_event: threading.Event
) -> None:
    """Continuously drain urgent/background numeric work beside the full cycle."""
    task_db_kwargs: dict[str, Any] = {"lease_seconds": TASK_CLAIM_LEASE_SECONDS}
    canonical_owner_id = getattr(args, "canonical_owner_id", None)
    if canonical_owner_id is not None:
        task_db_kwargs["owner_id"] = canonical_owner_id
    worker_tasks = FarmTasksDB(
        tasks_db_path(private_root),
        **task_db_kwargs,
    )
    from src.research_lab.farm_journal import make_transition_sink

    worker_tasks.on_transition = make_transition_sink(private_root)
    sequence = 0
    try:
        while not stop_event.is_set():
            if getattr(args, "stop_file", "") and Path(args.stop_file).exists():
                break
            slot_started = time.time()
            try:
                _write_priority_worker_status(
                    private_root,
                    stage="running_slot",
                    started_at=slot_started,
                    details={"sequence": sequence + 1},
                )
                validation_maintenance: dict[str, Any] = {}
                if bool(
                    getattr(args, "run_validation", False)
                ) and worker_tasks.eligible_count(task_types=("export_validation",)):
                    validation_maintenance = _run_validation_maintenance(
                        args,
                        worker_tasks,
                        private_root,
                        apply=True,
                        loop=True,
                        cycle_started_at=slot_started,
                        status_target="priority_worker",
                    )
                    generation_published = bool(
                        int(validation_maintenance.get("generation_published") or 0)
                        > 0
                        or int(
                            validation_maintenance.get(
                                "generation_empty_published"
                            )
                            or 0
                        )
                        > 0
                    )
                    wake_event = getattr(
                        args, "product_cycle_wakeup_event", None
                    )
                    if generation_published and wake_event is not None:
                        wake_event.set()
                        validation_maintenance[
                            "product_cycle_wakeup_requested"
                        ] = True
                slot = _run_priority_slot(
                    args, worker_tasks, profiles, policy, private_root
                )
                if validation_maintenance:
                    slot["validation_maintenance"] = validation_maintenance
                sequence += 1
                _write_priority_checkpoint(private_root, slot, sequence=sequence)
                did_work = _slot_did_work(slot)
                eligible = worker_tasks.eligible_count()
                errors = len(slot.get("errors") or [])
                _write_priority_worker_status(
                    private_root,
                    stage="busy" if did_work or eligible else "idle",
                    started_at=slot_started,
                    details={
                        "sequence": sequence,
                        "did_work": did_work,
                        "eligible_now": eligible,
                        "pivot": slot.get("pivot"),
                        "active_tasks": slot.get("active_tasks"),
                        "queue": (slot.get("status") or {}).get("by_state", {}),
                        "errors": errors,
                    },
                )
                if did_work or errors:
                    print(
                        f"  priority-worker did_work={did_work} eligible={eligible} "
                        f"pivot={slot.get('pivot')} errors={errors}"
                    )
                delay = (
                    args.busy_slot_seconds
                    if eligible or did_work
                    else args.idle_poll_seconds
                )
            except PriorityWorkerFatalError as exc:
                failure_signal = getattr(args, "task_claim_failure_signal", None)
                if failure_signal is not None:
                    failure_signal.notify(
                        exc,
                        {
                            "failure_kind": "compute_worker_lifecycle",
                            "failure": type(exc.__cause__).__name__
                            if exc.__cause__ is not None
                            else type(exc).__name__,
                        },
                    )
                break
            except Exception as exc:  # noqa: BLE001 - claim failures stop; ordinary errors retry
                failure_signal = getattr(args, "task_claim_failure_signal", None)
                if failure_signal is not None and failure_signal.failure is not None:
                    break
                delay = args.idle_poll_seconds
                _write_priority_worker_status(
                    private_root,
                    stage="error",
                    started_at=slot_started,
                    details={"sequence": sequence, "error": str(exc)[:300]},
                )
                print(f"  priority-worker error: {exc}")
            stop_event.wait(max(0.1, float(delay)))
    finally:
        failure_signal = getattr(args, "task_claim_failure_signal", None)
        claim_failed = failure_signal is not None and failure_signal.failure is not None
        failure_details = (
            failure_signal.status_details()
            if claim_failed and failure_signal is not None
            else {}
        )
        failure_kind = str(failure_details.get("failure_kind") or "task_claim")
        _write_priority_worker_status(
            private_root,
            stage=(
                "worker_failed"
                if claim_failed and failure_kind == "compute_worker_lifecycle"
                else "claim_failed"
                if claim_failed
                else "stopped"
            ),
            started_at=time.time(),
            details=(
                {"sequence": sequence, **failure_details}
                if claim_failed
                else {"sequence": sequence}
            ),
        )
        worker_tasks.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="plan only; write nothing (default)"
    )
    mode.add_argument(
        "--apply", action="store_true", help="prepare/enrich/queue/classify/validate"
    )
    run = ap.add_mutually_exclusive_group()
    run.add_argument(
        "--once", action="store_true", help="one cycle then exit (default)"
    )
    run.add_argument("--loop", action="store_true", help="run until stop-file / Ctrl+C")
    ap.add_argument(
        "--run-worker", action="store_true", help="drain a few compute jobs each cycle"
    )
    ap.add_argument(
        "--run-validation",
        action="store_true",
        help="export + honest-backtest + stamp-back",
    )
    ap.add_argument(
        "--run-paper",
        action="store_true",
        help="simulate paper outcomes from validated setup cards",
    )
    ap.add_argument(
        "--run-paper-signals",
        action="store_true",
        help="run one bounded operational paper-watch cycle (observe+generate; research-only)",
    )
    ap.add_argument(
        "--paper-evidence-v2-required",
        action="store_true",
        default=_env_flag("STRATEGY_LAB_PAPER_EVIDENCE_V2_REQUIRED"),
        help=(
            "require an already activated Paper Evidence v2 cutover and bind its "
            "writer to the canonical farm owner"
        ),
    )
    ap.add_argument(
        "--paper-evidence-cutover-manifest",
        default="",
        help="optional explicit v2 cutover manifest path inside the private root",
    )
    ap.add_argument(
        "--run-calculator-advisor",
        action="store_true",
        help="run bounded calculator advisor over the latest feature packet (requires paper signals)",
    )
    ap.add_argument(
        "--run-agent-role-reviews",
        action="store_true",
        help="run bounded advisory LLM reviews over outcomes/validator/source artifacts",
    )
    ap.add_argument(
        "--calculator-advisor-max-calls",
        type=int,
        default=1,
        help="max calculator advisor calls per cycle",
    )
    ap.add_argument(
        "--calculator-provider",
        default="",
        help="optional LLM provider override for calculator advisor, e.g. ollama",
    )
    ap.add_argument(
        "--calculator-model",
        default="",
        help="optional calculator model override, e.g. calculator",
    )
    ap.add_argument(
        "--calculator-base-url",
        default="",
        help="optional OpenAI-compatible base URL for calculator advisor",
    )
    ap.add_argument(
        "--calculator-timeout",
        type=float,
        default=0.0,
        help="optional calculator advisor timeout seconds",
    )
    ap.add_argument(
        "--agent-role-provider",
        default="alibaba",
        help="provider for advisory role reviews",
    )
    ap.add_argument(
        "--agent-role-base-url",
        default="",
        help="optional OpenAI-compatible base URL for role reviews",
    )
    ap.add_argument(
        "--agent-role-api-key-env",
        default="ALIBABA_API_KEY",
        help="environment variable that holds the role-review provider key",
    )
    ap.add_argument(
        "--agent-role-model", default="", help="model for advisory role reviews"
    )
    ap.add_argument(
        "--agent-role-timeout",
        type=float,
        default=60.0,
        help="role-review provider timeout seconds",
    )
    ap.add_argument(
        "--agent-role-rate-rub-per-1k",
        type=float,
        default=0.0,
        help="optional accounting rate for role-review provider",
    )
    ap.add_argument(
        "--agent-role-max-outcomes",
        type=int,
        default=1,
        help="max outcome rows reviewed per cycle",
    )
    ap.add_argument(
        "--agent-role-max-validator",
        type=int,
        default=1,
        help="max validator rows reviewed per cycle",
    )
    ap.add_argument(
        "--agent-role-max-sources",
        type=int,
        default=1,
        help="max scanner/source rows reviewed per cycle",
    )
    ap.add_argument(
        "--agent-role-max-analyst",
        type=int,
        default=1,
        help="max completed role results reviewed by System Analyst per cycle",
    )
    ap.add_argument(
        "--agent-role-sleep-seconds",
        type=float,
        default=0.0,
        help="sleep between role-review provider calls",
    )
    ap.add_argument(
        "--true-forward-max-candidates",
        type=int,
        default=20,
        help="max true-forward records collected per apply cycle; set 0 for wiring smoke checks",
    )
    ap.add_argument(
        "--paper-signals-max-new",
        type=int,
        default=5,
        help="max new paper-watch cards generated per cycle; set 0 for wiring smoke checks",
    )
    ap.add_argument(
        "--pfr-db-path",
        default="",
        help="path to strategy_lab.sqlite for PFR forward-watch lane "
        "(requires --run-paper-signals; OFF by default — must be explicit)",
    )
    ap.add_argument(
        "--paper-signals-max-pfr-scan",
        type=int,
        default=30,
        help="max PFR records inspected by --run-paper-signals per farm cycle",
    )
    ap.add_argument(
        "--paper-signals-max-pfr-fetches",
        type=int,
        default=int(os.getenv("STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES", "12")),
        help="max PFR candle fetch attempts per paper-signal cycle",
    )
    ap.add_argument(
        "--paper-signals-pfr-reserved",
        type=int,
        default=0,
        help=(
            "reserve this many new paper-watch card slots for PFR records when "
            "--pfr-db-path is provided"
        ),
    )
    ap.add_argument(
        "--paper-signals-max-observe",
        type=int,
        default=50,
        help=(
            "max active paper signals observed by --run-paper-signals per cycle "
            "(set 0 for smoke checks)"
        ),
    )
    ap.add_argument(
        "--paper-signals-max-live-fetches",
        type=int,
        default=int(os.getenv("STRATEGY_LAB_PAPER_SIGNALS_MAX_LIVE_FETCHES", "12")),
        help="max live-mover candle fetch attempts per paper-signal cycle",
    )
    ap.add_argument(
        "--paper-signals-max-network-fetches",
        type=int,
        default=int(os.getenv("STRATEGY_LAB_PAPER_SIGNALS_MAX_NETWORK_FETCHES", "44")),
        help=(
            "max observe+live+PFR candle fetch attempts per paper-signal cycle; "
            "default matches the visible launcher caps 20+12+12"
        ),
    )
    ap.add_argument(
        "--paper-signals-fetch-timeout",
        type=float,
        default=10.0,
        help="per-request public OKX timeout used by --run-paper-signals",
    )
    ap.add_argument(
        "--paper-signals-max-seconds",
        type=float,
        default=float(os.getenv("STRATEGY_LAB_PAPER_SIGNALS_MAX_SECONDS", "45")),
        help="wall-clock budget for one paper-signal stage; checked between atomic fetches",
    )
    ap.add_argument(
        "--paper-signals-timeframes",
        default="15m,1h,4h",
        help="comma-separated paper-signal timeframes; default includes validator-heavy 4h PFR",
    )
    ap.add_argument(
        "--live-universe-ttl-seconds",
        type=int,
        default=int(os.getenv("STRATEGY_LAB_LIVE_UNIVERSE_TTL_SECONDS", str(15 * 60))),
        help=(
            "treat discovery/live_universe.json as fresh for this many seconds before "
            "refreshing the movement-ranked paper-signal universe"
        ),
    )
    ap.add_argument(
        "--live-universe-top-n",
        type=int,
        default=int(os.getenv("STRATEGY_LAB_LIVE_UNIVERSE_TOP_N", "12")),
        help="top symbols per live-universe group used to feed paper-signal generation",
    )
    ap.add_argument(
        "--no-live-universe-refresh",
        action="store_true",
        help="never auto-refresh discovery/live_universe.json before paper-signal generation",
    )
    ap.add_argument(
        "--main-paper-runtime-limit",
        type=int,
        default=50,
        help="max main-paper runtime queue items observed per --run-paper-signals cycle",
    )
    ap.add_argument(
        "--send-paper-telegram",
        action="store_true",
        default=_env_flag("STRATEGY_LAB_PAPER_TELEGRAM_SEND"),
        help=(
            "opt-in network delivery of validated paper Telegram previews to active "
            "subscription users; default is dry-run/preview only"
        ),
    )
    ap.add_argument(
        "--run-journal-export",
        action="store_true",
        default=_env_flag("STRATEGY_LAB_RUN_JOURNAL_EXPORT"),
        help=(
            "rebuild scripts/journal.xlsx after paper/training export; private fills are "
            "forced off inside the farm loop"
        ),
    )
    ap.add_argument(
        "--paper-telegram-limit",
        type=int,
        default=int(os.getenv("STRATEGY_LAB_PAPER_TELEGRAM_SEND_LIMIT", "20")),
        help="max paper Telegram previews delivered/audited per cycle",
    )
    ap.add_argument(
        "--paper-telegram-status-digest",
        action="store_true",
        default=_env_flag("STRATEGY_LAB_PAPER_TELEGRAM_STATUS_DIGEST"),
        help=(
            "when paper Telegram sending is enabled, send a bounded status digest if "
            "no new card was sent because cards are duplicate or quality-gated"
        ),
    )
    ap.add_argument(
        "--paper-telegram-status-digest-hours",
        type=int,
        default=int(os.getenv("STRATEGY_LAB_PAPER_TELEGRAM_STATUS_DIGEST_HOURS", "12")),
        help="dedup bucket size for paper Telegram status digest",
    )
    ap.add_argument(
        "--enrich-funding",
        action="store_true",
        help="enable public funding enrichment tasks",
    )
    ap.add_argument(
        "--enrich-oi",
        action="store_true",
        help="enable public open-interest enrichment tasks",
    )
    ap.add_argument("--backend", choices=["cpu", "auto", "gpu"], default="auto")
    ap.add_argument(
        "--sweep-tier",
        choices=["smoke", "normal", "deep"],
        default="normal",
        help="parameter-search depth: smoke=profile cap; normal=x2; deep=x4 (abs-capped)",
    )
    ap.add_argument(
        "--provider", choices=["okx-public", "synthetic"], default="okx-public"
    )
    ap.add_argument("--max-plan-events", type=int, default=20)
    ap.add_argument("--max-prepares", type=int, default=4)
    ap.add_argument("--max-enrich", type=int, default=4)
    ap.add_argument("--max-sweeps", type=int, default=4)
    ap.add_argument("--max-worker-jobs", type=int, default=4)
    ap.add_argument("--max-validations", type=int, default=10)
    ap.add_argument(
        "--validation-backlog-high-water",
        type=int,
        default=256,
        help=(
            "pause classify_result consumption when active export_validation backlog "
            "reaches this bound"
        ),
    )
    ap.add_argument(
        "--validation-backlog-slo-seconds",
        type=float,
        default=3600.0,
        help="operator SLO for the oldest active export_validation task",
    )
    ap.add_argument("--max-paper-cards", type=int, default=20)
    ap.add_argument("--max-followups", type=int, default=10)
    ap.add_argument(
        "--no-followups",
        action="store_true",
        help="disable automatic bounded feedback follow-ups",
    )
    ap.add_argument("--data-days", type=int, default=None)
    ap.add_argument(
        "--discovery-ttl-seconds",
        type=int,
        default=6 * 3600,
        help="treat the discovery snapshot as fresh for this many seconds; refresh when older",
    )
    ap.add_argument(
        "--no-discovery-refresh",
        action="store_true",
        help="never auto-refresh the discovery snapshot in apply mode (warn loudly if stale)",
    )
    ap.add_argument("--night-mode", action="store_true")
    ap.add_argument("--sleep-seconds", type=int, default=180)
    ap.add_argument(
        "--busy-slot-seconds",
        type=float,
        default=1.0,
        help="pause between short compute slots while work is available",
    )
    ap.add_argument(
        "--idle-poll-seconds",
        type=float,
        default=5.0,
        help="poll interval for new urgent/scanner work between full cycles",
    )
    ap.add_argument("--stop-file", default="")
    ap.add_argument(
        "--status-publish-max-outage-seconds",
        type=float,
        default=STATUS_PUBLISH_MAX_OUTAGE_SECONDS,
        help=(
            "gracefully leave the loop at a completed-cycle boundary when the "
            "operator status channel stays unavailable"
        ),
    )
    ap.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
    )
    ap.add_argument("--allow-public-output", action="store_true")
    verb = ap.add_mutually_exclusive_group()
    verb.add_argument(
        "--verbose", action="store_true", help="print the full cycle block every tick"
    )
    verb.add_argument(
        "--quiet",
        action="store_true",
        help="print only on change/error (loop heartbeat)",
    )
    args = ap.parse_args()
    apply = bool(args.apply)

    if apply:
        private_root = resolve_private_root(
            Path(args.private_root), allow_public_output=args.allow_public_output
        )
        from src.research_lab.runtime_storage_rotation import install_runtime_stdout_tee

        install_runtime_stdout_tee(private_root, stream_id="farm.stdout")

    print(
        f"farm_loop mode={'APPLY' if apply else 'DRY-RUN'} run={'loop' if args.loop else 'once'} "
        f"private_root={args.private_root}"
    )
    print(
        "safety: paper-only; public OKX market data; no orders / AUTO_TRADE / private endpoints; "
        "Telegram send is explicit opt-in"
    )
    _print_stages(_stage_status(args, apply), apply)

    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=args.night_mode)
    ownership_store = None
    process_lease = None
    lease_heartbeat = None
    paper_generation_runtime = None
    priority_stop = threading.Event()
    product_cycle_wakeup = threading.Event()
    priority_thread = None
    claim_failure_signal = None
    if apply:
        claim_failure_signal = _TaskClaimFailureSignal(private_root, priority_stop)
        legacy_lock = private_root / "state" / "farm_loop.lock"
        if legacy_lock.exists():
            print(
                "ABORT: legacy farm_loop.lock is present; it is migration evidence, "
                "not an age-based lease. Quiesce and disposition it explicitly."
            )
            return
        owner_id = f"farm-{os.getpid()}-{uuid.uuid4().hex}"
        ownership_path = private_root / "state" / "ownership.sqlite"
        ownership_store = OwnershipStore(
            ownership_path,
            identity_probe=probe_process_identity,
        )
        try:
            process_lease = ownership_store.acquire(
                resource_id="canonical_farm",
                role_id="farm",
                owner_id=owner_id,
                identity=current_process_identity(),
                lease_seconds=90.0,
            )
        except OwnershipConflictError as exc:
            ownership_store.close()
            print(f"ABORT: canonical farm ownership conflict: {exc}")
            return
        assert process_lease is not None
        lease_heartbeat = _FarmLeaseHeartbeat(
            ownership_path,
            process_lease,
            on_failure=claim_failure_signal.notify,
        )
        try:
            lease_heartbeat.start()
            _PROCESS_LEASE_SUPERVISORS[ownership_path] = lease_heartbeat
            lease_heartbeat.record_progress("lease_supervisor_ready")
            if lease_heartbeat.failure is not None:
                claim_failure_signal.notify(
                    lease_heartbeat.failure,
                    lease_heartbeat.snapshot() | {"failure_kind": "process_lease"},
                )
            if getattr(args, "paper_evidence_v2_required", False):
                from src.research_lab.paper_generation_cutover import (
                    CanonicalPaperGenerationRuntime,
                )

                configured_manifest = str(
                    getattr(args, "paper_evidence_cutover_manifest", "") or ""
                ).strip()
                paper_generation_runtime = (
                    CanonicalPaperGenerationRuntime.open_required(
                        private_root,
                        owner_id=process_lease.owner_id,
                        identity=process_lease.identity,
                        manifest_path=(
                            Path(configured_manifest) if configured_manifest else None
                        ),
                        on_failure=claim_failure_signal.notify,
                    )
                )
                args.paper_generation_runtime = paper_generation_runtime
            tasks = FarmTasksDB(
                tasks_db_path(private_root),
                owner_id=owner_id,
                lease_seconds=TASK_CLAIM_LEASE_SECONDS,
            )
            from src.research_lab.farm_journal import make_transition_sink

            tasks.on_transition = make_transition_sink(
                private_root
            )  # durable task-transition audit
            n_orphan = tasks.reconcile_orphan_running()
            if n_orphan:
                print(
                    f"  reconcile: requeued {n_orphan} orphan running task(s) from a previous stop"
                )
        except Exception:
            if paper_generation_runtime is not None:
                try:
                    paper_generation_runtime.close()
                except Exception:
                    pass
            _PROCESS_LEASE_SUPERVISORS.pop(ownership_path, None)
            lease_heartbeat.stop()
            try:
                ownership_store.release_local(process_lease)
            finally:
                ownership_store.close()
            raise
    else:
        private_root = Path(args.private_root)
        tasks = FarmTasksDB(":memory:")  # dry-run persists nothing

    if apply:
        assert process_lease is not None
        assert claim_failure_signal is not None
        args.canonical_owner_id = process_lease.owner_id
        args.task_claim_failure_signal = claim_failure_signal
        args.product_cycle_wakeup_event = product_cycle_wakeup
        args.task_claim_guard_factory = _task_claim_guard_factory(
            ownership_path,
            process_lease,
            priority_stop,
            on_failure=claim_failure_signal.notify,
            stop_requested=(
                (lambda: Path(args.stop_file).exists()) if args.stop_file else None
            ),
        )
    try:
        if not args.loop:
            try:
                out = _run_once(args, tasks, profiles, policy, private_root, apply)
            except FarmCycleStopRequested:
                print("canonical stop requested - cancelled current farm stage")
                return
            _print_cycle(out)  # a single explicit cycle is always shown
            return
        prev_sig = None
        args.priority_worker_active = bool(apply)
        if apply:
            priority_thread = threading.Thread(
                target=_priority_worker_loop,
                args=(args, profiles, policy, private_root, priority_stop),
                name="farm-priority-worker",
                daemon=True,
            )
            priority_thread.start()
        while True:
            if args.stop_file and Path(args.stop_file).exists():
                if ownership_store is not None and process_lease is not None:
                    ownership_store.acknowledge_stop_intent_local(
                        process_lease, Path(args.stop_file)
                    )
                print(f"stop-file present ({args.stop_file}) - exiting loop")
                break
            if lease_heartbeat is not None and lease_heartbeat.failure is not None:
                raise RuntimeError(
                    "farm ownership lease renewal failed"
                ) from lease_heartbeat.failure
            if claim_failure_signal is not None:
                claim_failure_signal.raise_if_failed()
            if apply and _leave_for_status_publication_outage(
                private_root,
                max_outage_seconds=args.status_publish_max_outage_seconds,
            ):
                break
            try:
                out = _run_once(args, tasks, profiles, policy, private_root, apply)
            except FarmCycleStopRequested:
                if (
                    args.stop_file
                    and ownership_store is not None
                    and process_lease is not None
                ):
                    ownership_store.acknowledge_stop_intent_local(
                        process_lease, Path(args.stop_file)
                    )
                print("canonical stop requested - cancelled current farm stage")
                break
            if claim_failure_signal is not None:
                claim_failure_signal.raise_if_failed()
            if apply and _leave_for_status_publication_outage(
                private_root,
                max_outage_seconds=args.status_publish_max_outage_seconds,
            ):
                break
            sig = _cycle_signature(out)
            # Always show a CHANGED cycle or any error (never hide a changed block, even with
            # --quiet). Unchanged cycle: heartbeat by default; with --quiet, print nothing.
            show_full = args.verbose or sig != prev_sig or bool(out.get("errors"))
            if show_full:
                print(f"\n=== farm cycle @ {int(time.time())} ===")
                _print_cycle(out)
            elif not args.quiet:
                print(
                    f"  heartbeat @ {int(time.time())} pivot={out['pivot']} active={out['active_tasks']}"
                )
            prev_sig = sig
            if apply:
                _write_loop_status(
                    private_root,
                    stage="priority_slots",
                    apply=apply,
                    loop=True,
                    cycle_started_at=time.time(),
                    details={
                        "full_cycle_seconds": args.sleep_seconds,
                        "busy_slot_seconds": args.busy_slot_seconds,
                        "idle_poll_seconds": args.idle_poll_seconds,
                    },
                )
            if not _sleep_until_next_cycle(
                args.sleep_seconds,
                args.stop_file,
                wake_event=product_cycle_wakeup,
            ):
                if (
                    args.stop_file
                    and ownership_store is not None
                    and process_lease is not None
                ):
                    ownership_store.acknowledge_stop_intent_local(
                        process_lease, Path(args.stop_file)
                    )
                print(f"stop-file present ({args.stop_file}) - exiting loop")
                break
    except KeyboardInterrupt:
        if claim_failure_signal is not None:
            claim_failure_signal.raise_if_failed()
        print("\ninterrupted - graceful stop")
    finally:
        priority_stop.set()
        if priority_thread is not None:
            priority_thread.join(timeout=15)
        tasks.close()
        paper_generation_close_error = None
        if paper_generation_runtime is not None:
            try:
                paper_generation_runtime.close()
            except Exception as exc:  # noqa: BLE001 - finish all owner cleanup first
                paper_generation_close_error = exc
        if lease_heartbeat is not None:
            _PROCESS_LEASE_SUPERVISORS.pop(
                private_root / "state" / "ownership.sqlite",
                None,
            )
            lease_heartbeat.stop()
        if ownership_store is not None and process_lease is not None:
            try:
                ownership_store.release_local(process_lease)
            except Exception:
                pass
            ownership_store.close()
        if apply:
            _LOOP_STATUS_PUBLISHERS.pop(
                private_root / "state" / "farm_loop_status.json",
                None,
            )
        if paper_generation_close_error is not None:
            raise RuntimeError(
                "paper evidence writer did not stop cleanly"
            ) from paper_generation_close_error


if __name__ == "__main__":
    main()
