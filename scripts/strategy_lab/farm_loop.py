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
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.farm_coordinator import DEFAULT_FAMILIES, run_coordinator_cycle  # noqa: E402
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path  # noqa: E402
from src.research_lab.intake_adapter import watches_to_intake  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402


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
        }

    try:
        from scripts.strategy_lab.paper_telegram_transport import build_subscription_delivery_config
        config = build_subscription_delivery_config(ROOT)
    except Exception as exc:  # noqa: BLE001 - delivery config must not crash the farm
        return {
            "apply": True,
            "configured": False,
            "ids": [],
            "send_text": None,
            "config_error": type(exc).__name__,
        }
    return {
        "apply": True,
        "configured": bool(config.get("configured")),
        "ids": list(config.get("ids") or []),
        "send_text": config.get("send_text"),
    }


def _read_intake(limit: int) -> list[dict]:
    """Read open scanner watches (file only - never imports the scanner module)."""
    try:
        from src.scout.watch_queue import open_watches
        return watches_to_intake(open_watches())[:limit]
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
        "journal_export": st(getattr(args, "run_journal_export", False), "--run-journal-export", False),
    }


def _print_stages(stages: dict, apply: bool) -> None:
    line = " ".join(f"{name}={'ON' if s['enabled'] else 'OFF'}" for name, s in stages.items())
    print(f"stages: {line}")
    if apply:
        off_critical = [name for name, s in stages.items() if s["critical"] and not s["enabled"]]
        if off_critical:
            flags = {"worker": "--run-worker", "validation": "--run-validation", "paper": "--run-paper"}
            need = " ".join(flags[n] for n in off_critical if n in flags)
            print(
                "WARNING: apply run with critical stage(s) OFF: " + ", ".join(off_critical)
                + " - the loop will QUEUE work but not " + "/".join(off_critical) + " it. "
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
                print(f"  discovery: refresh failed ({res.get('reason')}) - using existing snapshot")
            elif refreshed:
                print(f"  discovery: refreshed snapshot (count={res.get('count')}, "
                      f"new={res.get('diff', {}).get('new')})")
        except Exception as exc:  # noqa: BLE001 - discovery refresh must never crash the farm
            print(f"  discovery: refresh skipped ({type(exc).__name__})")
    snap = idisc.load_snapshot(private_root)
    if not snap.get("instruments"):
        print("  WARNING discovery snapshot MISSING - run: "
              "python -m scripts.strategy_lab.discover_okx_universe --apply")
        return None, {"status": "missing", "age_seconds": None, "count": 0}
    age = idisc.snapshot_age_seconds(snap, now_ms)
    fresh = idisc.is_fresh(snap, now_ms, ttl)
    if not fresh and not refreshed:
        print(f"  WARNING discovery snapshot STALE (age={age}s > ttl={ttl}s) - run: "
              "python -m scripts.strategy_lab.discover_okx_universe --apply "
              "(or remove --no-discovery-refresh in apply mode)")
        status = "stale_no_refresh"
    else:
        status = "refreshed" if refreshed else "fresh"
    return snap, {"status": status, "age_seconds": age, "count": int(snap.get("count") or 0)}


def _maybe_storage_maintain(private_root: Path, apply: bool) -> None:
    if not apply:
        return
    try:
        from src.research_lab.farm_journal import farm_log_paths
        from src.research_lab.storage_policy import bound_farm_artifacts, maintain
        maintain(farm_log_paths(private_root), apply=True)  # rotate the farm logs too
        bound_farm_artifacts(private_root, apply=True)
    except Exception as exc:  # noqa: BLE001 - storage hygiene must never break the loop
        print(f"  storage: skipped ({type(exc).__name__})")


def _providers(args, apply: bool):
    provider = flow_provider = oi_provider = None
    if apply:
        from src.research_lab.market_data_provider import get_provider
        provider = get_provider(args.provider, allow_synthetic=(args.provider == "synthetic"))
        if args.enrich_funding:
            from src.research_lab.providers.okx_flow import OkxPublicFundingProvider
            flow_provider = OkxPublicFundingProvider()
        if args.enrich_oi:
            from src.research_lab.providers.okx_flow import OkxPublicOpenInterestProvider
            oi_provider = OkxPublicOpenInterestProvider()
    return provider, flow_provider, oi_provider


def _print_cycle(out: dict) -> None:
    c = out["counters"]
    interesting = {k: v for k, v in c.items() if isinstance(v, int) and v}
    print(f"  pivot={out['pivot']} active_tasks={out['active_tasks']}")
    print("  " + (" ".join(f"{k}={v}" for k, v in interesting.items()) or "(no new work)"))
    st = out["status"]
    if st.get("by_state"):
        print("  states: " + " ".join(f"{k}={v}" for k, v in st["by_state"].items()))
    if st.get("blocked_reasons"):
        print("  blocked: " + " ".join(f"{k}={v}" for k, v in st["blocked_reasons"].items()))
    if st.get("deferred_reasons"):
        print("  deferred: " + " ".join(f"{k}={v}" for k, v in st["deferred_reasons"].items()))
    paper = out.get("paper") or {}
    if paper:
        pc = paper.get("counters") or {}
        readiness = paper.get("readiness") or {}
        shown = " ".join(f"{k}={v}" for k, v in pc.items() if isinstance(v, int) and (v or k == "cards"))
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
                print("  paper_blocked: " + " ".join(f"{k}={v}" for k, v in list(blockers.items())[:6]))
    ps_op = out.get("paper_signals") or {}
    if ps_op:
        pfr_c = {k: v for k, v in (ps_op.get("pfr_counts") or {}).items() if isinstance(v, int) and v}
        if pfr_c:
            print("  pfr_lane: " + " ".join(f"{k}={v}" for k, v in pfr_c.items()))
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
    tp = out.get("paper_telegram_preview") or {}
    if tp:
        print(
            "  paper_telegram_preview: "
            f"rendered={tp.get('rendered', 0)} invalid={tp.get('invalid', 0)} "
            f"sends_network={tp.get('sends_network')}"
        )
    td = out.get("paper_telegram_delivery") or {}
    if td:
        print(
            "  paper_telegram_delivery: "
            f"eligible={td.get('eligible', 0)} sent={td.get('sent', 0)} "
            f"skipped={td.get('skipped', 0)} errors={td.get('errors', 0)} "
            f"dry_run={td.get('dry_run')} sends_network={td.get('sends_network')}"
        )
    train = out.get("paper_signal_training_export") or {}
    if train:
        print(
            "  paper_signal_training_export: "
            f"rows={train.get('rows', 0)} terminal_only={train.get('terminal_only')} "
            f"paper_only={train.get('paper_only')}"
        )
    product_train = out.get("product_signal_training_export") or {}
    if product_train:
        print(
            "  product_signal_training_export: "
            f"rows={product_train.get('rows', 0)} source_rows={product_train.get('source_rows', 0)} "
            f"paper_only={product_train.get('paper_only')}"
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
            f"processed={advisor.get('processed', 0)} accepted={advisor.get('accepted', 0)} "
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
            "skip_reasons": (out.get("main_paper_bridge") or {}).get("skip_reasons") or {},
        },
        "main_paper_runtime": {
            "queued": (out.get("main_paper_runtime_queue") or {}).get("queued", 0),
            "observed": (out.get("main_paper_runtime_observation") or {}).get("observed", 0),
            "provider_error": (out.get("main_paper_runtime_observation") or {}).get("provider_error", 0),
        },
        "paper_product_trades": {
            "trades": (out.get("paper_product_trade_ledger") or {}).get("trades", 0),
            "live_ready": (out.get("paper_product_trade_ledger") or {}).get("live_ready", 0),
            "live_blocked": (out.get("paper_product_trade_ledger") or {}).get("live_blocked", 0),
            "active_trades": (out.get("paper_product_trade_ledger") or {}).get("active_trades", 0),
            "active_live_ready": (out.get("paper_product_trade_ledger") or {}).get("active_live_ready", 0),
            "active_live_blocked": (out.get("paper_product_trade_ledger") or {}).get("active_live_blocked", 0),
        },
        "telegram": {
            "preview_rendered": (out.get("paper_telegram_preview") or {}).get("rendered", 0),
            "delivery_sent": (out.get("paper_telegram_delivery") or {}).get("sent", 0),
            "delivery_errors": (out.get("paper_telegram_delivery") or {}).get("errors", 0),
        },
        "calculator_advisor": {
            "processed": (out.get("calculator_advisor") or {}).get("processed", 0),
            "accepted": (out.get("calculator_advisor") or {}).get("accepted", 0),
            "blocked": (out.get("calculator_advisor") or {}).get("blocked", 0),
        },
    }


def _cycle_signature(out: dict) -> tuple:
    """A change-signature so --loop doesn't reprint identical state every sleep tick."""
    nz = tuple(sorted(k for k, v in out["counters"].items() if isinstance(v, int) and v))
    by_state = tuple(sorted(((out.get("status") or {}).get("by_state") or {}).items()))
    paper = out.get("paper") or {}
    paper_counters = tuple(sorted((paper.get("counters") or {}).items()))
    paper_ready = tuple(sorted((paper.get("readiness") or {}).get("blocked_reasons", {}).items()))
    main_consumer = tuple(sorted((out.get("main_paper_consumer") or {}).items()))
    main_runtime_queue = tuple(sorted((out.get("main_paper_runtime_queue") or {}).items()))
    main_runtime_observation = tuple(sorted((out.get("main_paper_runtime_observation") or {}).items()))
    main_trade_ledger = tuple(sorted((out.get("main_paper_trade_ledger") or {}).items()))
    product_trade_ledger = tuple(sorted((out.get("paper_product_trade_ledger") or {}).items()))
    telegram_preview = tuple(sorted((out.get("paper_telegram_preview") or {}).items()))
    telegram_delivery = tuple(sorted((out.get("paper_telegram_delivery") or {}).items()))
    training_export = tuple(sorted((out.get("paper_signal_training_export") or {}).items()))
    product_training_export = tuple(sorted((out.get("product_signal_training_export") or {}).items()))
    calculator_advisor = tuple(sorted((out.get("calculator_advisor") or {}).items()))
    agent_role_reviews = tuple(sorted((out.get("agent_role_reviews") or {}).items()))
    ready_catalog = tuple(sorted((out.get("ready_strategy_catalog") or {}).items()))
    return (
        out.get("pivot"), nz, by_state, paper_counters, paper_ready,
        main_consumer, main_runtime_queue, main_runtime_observation, main_trade_ledger, product_trade_ledger,
        telegram_preview,
        telegram_delivery, training_export, product_training_export, calculator_advisor,
        agent_role_reviews, ready_catalog,
        bool(out.get("errors")),
    )


def _parse_csv(value: str, *, default: tuple[str, ...]) -> tuple[str, ...]:
    items = tuple(item.strip() for item in str(value or "").split(",") if item.strip())
    return items or default


def _sleep_until_next_cycle(seconds: int, stop_file: str = "") -> bool:
    deadline = time.time() + max(1, seconds)
    while time.time() < deadline:
        if stop_file and Path(stop_file).exists():
            return False
        time.sleep(min(5.0, max(0.0, deadline - time.time())))
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


def _run_calculator_advisor_stage(args, private_root: Path, apply: bool) -> dict:
    result = {
        "schema": "CalculatorAdvisorStage.v1",
        "processed": 0,
        "accepted": 0,
        "skipped": 0,
        "deferred": 0,
        "blocked": 0,
        "reason_counts": {},
        "paper_only": True,
        "execution_allowed": False,
    }
    if not apply:
        result["skipped"] = 1
        result["reason_counts"] = {"dry_run": 1}
        return result
    max_calls = max(0, int(getattr(args, "calculator_advisor_max_calls", 1)))
    if max_calls < 1:
        result["skipped"] = 1
        result["reason_counts"] = {"cap_zero": 1}
        return result
    from src.research_lab.calculator_advisor import request_calculator_advice
    from src.research_lab.advisor_sweep_bridge import compile_sweep_proposals
    from src.research_lab.feature_packet import latest_feature_packet_path, load_feature_packet
    from src.research_lab.lineage_contract import write_cycle_link
    from src.research_lab.llm_provider import load_provider

    packet_path = latest_feature_packet_path(private_root)
    if packet_path is None:
        result["deferred"] = 1
        result["reason_counts"] = {"missing_feature_packet": 1}
        return result
    packet = load_feature_packet(packet_path)
    advice = request_calculator_advice(
        private_root,
        packet,
        load_provider(_provider_env(args)),
        allow_public_output=bool(getattr(args, "allow_public_output", False)),
    )
    reason = "accepted" if advice.accepted else (advice.problems[0] if advice.problems else "llm_schema_reject")
    result["processed"] = 1
    result["accepted"] = 1 if advice.accepted else 0
    result["blocked"] = 0 if advice.accepted else 1
    result["reason_counts"] = {reason: 1}
    result["advisor_ref"] = advice.advisor_ref
    result["feature_packet_id"] = packet.feature_packet_id
    result["provider"] = advice.provider
    result["model"] = advice.model
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
        journal_path = Path(getattr(build_journal, "JOURNAL_PATH", ROOT / "scripts" / "journal.xlsx"))
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


def _write_loop_status(
    private_root: Path,
    *,
    stage: str,
    apply: bool,
    loop: bool,
    cycle_started_at: float,
    details: dict | None = None,
) -> None:
    """Write a private heartbeat for long visible runs.

    The loop prints a full summary only after a cycle finishes. Some stages can take
    minutes, so this status file is the operator-facing "where is it now" signal.
    """
    if not apply:
        return
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
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _run_once(args, tasks: FarmTasksDB, profiles, policy, private_root: Path, apply: bool) -> dict:
    cycle_started_at = time.time()
    loop = bool(getattr(args, "loop", False))
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
    events = _read_intake(args.max_plan_events)
    if apply and events:
        from src.research_lab.lineage_contract import scanner_event_from_intake, write_scanner_event
        for event in events[: max(0, int(getattr(args, "max_plan_events", 20)))]:
            write_scanner_event(private_root, scanner_event_from_intake(event, mode="live"))
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
    out = run_coordinator_cycle(
        tasks, private_root=private_root, profiles=profiles, policy=policy, intake_events=events,
        families=DEFAULT_FAMILIES, provider=provider, flow_provider=flow_provider,
        oi_provider=oi_provider, apply=apply,
        backend=args.backend, data_days=args.data_days, max_plan_events=args.max_plan_events,
        max_prepares=args.max_prepares, max_enrich=args.max_enrich, max_sweeps=args.max_sweeps,
        run_worker=args.run_worker, max_worker_jobs=args.max_worker_jobs, night_mode=args.night_mode,
        allow_public_output=args.allow_public_output, discovery_snapshot=snapshot,
        max_discovery=args.max_plan_events,
        max_validations=int(getattr(args, "max_validations", 10)),
        run_validation=args.run_validation, run_followups=not getattr(args, "no_followups", False),
        max_followups=getattr(args, "max_followups", 10), sweep_tier=args.sweep_tier,
    )
    out["discovery"] = discovery_info
    if args.run_paper:
        _write_loop_status(
            private_root,
            stage="paper_runtime",
            apply=apply,
            loop=loop,
            cycle_started_at=cycle_started_at,
        )
        from src.research_lab.paper_runtime import run_paper_cycle
        paper = run_paper_cycle(private_root, apply=apply, limit=args.max_paper_cards)
        out["paper"] = {
            "counters": paper.get("counters", {}),
            "readiness": paper.get("readiness", {}),
            "results": (paper.get("results") or [])[:10],
        }
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
                tf_res = true_forward.collect_once(private_root, max_candidates=tf_limit)
                out["true_forward"] = tf_res.get("summary", {})
            except Exception as exc:  # noqa: BLE001 - research lane must never break the cycle
                out.setdefault("errors", []).append({"where": "true_forward", "error": str(exc)})
        else:
            out["true_forward"] = {"skipped": "true_forward_max_candidates=0"}
        if getattr(args, "run_paper_signals", False):
            # Operational paper-watch lane: one bounded cycle (observe armed -> close -> remember ->
            # generate new). Crash-isolated; paper/research-only, never an order.
            try:
                from src.research_lab.paper_signals import cycle as paper_cycle
                from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
                _pfr_db = Path(getattr(args, "pfr_db_path", "") or "")
                _pfr_db = _pfr_db if _pfr_db.as_posix() not in ("", ".") else None
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
                        from src.research_lab.ready_strategy_catalog import build_ready_strategy_catalog
                        out["ready_strategy_catalog"] = build_ready_strategy_catalog(private_root, _pfr_db)
                    except Exception as exc:  # noqa: BLE001 - catalog must not break the cycle
                        out.setdefault("errors", []).append({
                            "where": "ready_strategy_catalog",
                            "error": str(exc),
                        })
                paper_provider = OkxPublicMarketDataProvider(
                    timeout=float(getattr(args, "paper_signals_fetch_timeout", 10.0))
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
                        "max_pfr_scan": int(getattr(args, "paper_signals_max_pfr_scan", 30)),
                        "max_pfr_fetches": int(getattr(args, "paper_signals_max_pfr_fetches", 8)),
                        "max_live_fetches": int(getattr(args, "paper_signals_max_live_fetches", 12)),
                        "max_network_fetches": int(getattr(args, "paper_signals_max_network_fetches", 16)),
                    },
                )
                out["paper_signals"] = paper_cycle.run_cycle(
                    private_root, mode="live", timeframes=paper_timeframes,
                    max_new=int(getattr(args, "paper_signals_max_new", 5)), apply=True,
                    pfr_db_path=_pfr_db, provider=paper_provider,
                    max_pfr_scan=int(getattr(args, "paper_signals_max_pfr_scan", 30)),
                    max_pfr_fetches=int(getattr(args, "paper_signals_max_pfr_fetches", 8)),
                    pfr_reserved_new=int(getattr(args, "paper_signals_pfr_reserved", 0)),
                    max_observe=getattr(args, "paper_signals_max_observe", None),
                    max_live_fetches=int(getattr(args, "paper_signals_max_live_fetches", 12)),
                    max_network_fetches=int(getattr(args, "paper_signals_max_network_fetches", 16)))
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
                except Exception as exc:  # noqa: BLE001 - derived bridge must not break the cycle
                    out.setdefault("errors", []).append({"where": "main_paper_bridge", "error": str(exc)})
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
                    out.setdefault("errors", []).append({"where": "main_paper_consumer", "error": str(exc)})
                try:
                    _write_loop_status(
                        private_root,
                        stage="main_paper_runtime_queue",
                        apply=apply,
                        loop=loop,
                        cycle_started_at=cycle_started_at,
                    )
                    from src.research_lab.main_paper_runtime_adapter import build_main_paper_runtime_queue
                    out["main_paper_runtime_queue"] = build_main_paper_runtime_queue(private_root)
                except Exception as exc:  # noqa: BLE001 - runtime queue must not break the cycle
                    out.setdefault("errors", []).append({"where": "main_paper_runtime_queue", "error": str(exc)})
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
                        provider=paper_provider,
                    )
                except Exception as exc:  # noqa: BLE001 - paper observer must not break the cycle
                    out.setdefault("errors", []).append({
                        "where": "main_paper_runtime_observation",
                        "error": str(exc),
                    })
                try:
                    _write_loop_status(
                        private_root,
                        stage="main_paper_trade_ledger",
                        apply=apply,
                        loop=loop,
                        cycle_started_at=cycle_started_at,
                    )
                    from src.research_lab.main_paper_trade_ledger import build_main_paper_trade_ledger
                    out["main_paper_trade_ledger"] = build_main_paper_trade_ledger(private_root)
                except Exception as exc:  # noqa: BLE001 - paper trade ledger must not break the cycle
                    out.setdefault("errors", []).append({"where": "main_paper_trade_ledger", "error": str(exc)})
                try:
                    _write_loop_status(
                        private_root,
                        stage="paper_product_trade_ledger",
                        apply=apply,
                        loop=loop,
                        cycle_started_at=cycle_started_at,
                    )
                    from src.research_lab.paper_product_trade_ledger import build_paper_product_trade_ledger
                    out["paper_product_trade_ledger"] = build_paper_product_trade_ledger(private_root)
                except Exception as exc:  # noqa: BLE001 - product ledger must not break the cycle
                    out.setdefault("errors", []).append({"where": "paper_product_trade_ledger", "error": str(exc)})
                try:
                    _write_loop_status(
                        private_root,
                        stage="paper_telegram_preview",
                        apply=apply,
                        loop=loop,
                        cycle_started_at=cycle_started_at,
                    )
                    from src.research_lab.paper_telegram_preview import build_paper_telegram_preview
                    out["paper_telegram_preview"] = build_paper_telegram_preview(private_root)
                except Exception as exc:  # noqa: BLE001 - preview surface must not break the cycle
                    out.setdefault("errors", []).append({"where": "paper_telegram_preview", "error": str(exc)})
                try:
                    _write_loop_status(
                        private_root,
                        stage="paper_telegram_delivery",
                        apply=apply,
                        loop=loop,
                        cycle_started_at=cycle_started_at,
                    )
                    from src.research_lab.paper_telegram_sender import send_paper_telegram_previews
                    delivery_config = _paper_telegram_delivery_config(args, apply=apply)
                    out["paper_telegram_delivery"] = send_paper_telegram_previews(
                        private_root,
                        limit=int(getattr(args, "paper_telegram_limit", 20)),
                        apply=bool(delivery_config["apply"]),
                        paper_chat_configured=bool(delivery_config["configured"]),
                        paper_chat_ids_count=len(delivery_config["ids"]),
                        recipient_ids=delivery_config["ids"],
                        send_text=delivery_config["send_text"],
                    )
                    if delivery_config.get("config_error"):
                        out["paper_telegram_delivery"]["config_error"] = delivery_config["config_error"]
                except Exception as exc:  # noqa: BLE001 - delivery audit must not break the cycle
                    out.setdefault("errors", []).append({"where": "paper_telegram_delivery", "error": str(exc)})
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
                    out.setdefault("errors", []).append({
                        "where": "paper_signal_training_export",
                        "error": str(exc),
                    })
                try:
                    _write_loop_status(
                        private_root,
                        stage="product_signal_training_export",
                        apply=apply,
                        loop=loop,
                        cycle_started_at=cycle_started_at,
                    )
                    from src.research_lab.product_signal_training import export_product_signal_training
                    out["product_signal_training_export"] = export_product_signal_training(private_root)
                except Exception as exc:  # noqa: BLE001 - product training export must not break the cycle
                    out.setdefault("errors", []).append({
                        "where": "product_signal_training_export",
                        "error": str(exc),
                    })
                if getattr(args, "run_journal_export", False):
                    try:
                        _write_loop_status(
                            private_root,
                            stage="journal_export",
                            apply=apply,
                            loop=loop,
                            cycle_started_at=cycle_started_at,
                        )
                        out["journal_export"] = _run_journal_export_stage(private_root, apply)
                    except Exception as exc:  # noqa: BLE001 - journal export must not break the cycle
                        out.setdefault("errors", []).append({"where": "journal_export", "error": str(exc)})
                if getattr(args, "run_calculator_advisor", False):
                    try:
                        _write_loop_status(
                            private_root,
                            stage="calculator_advisor",
                            apply=apply,
                            loop=loop,
                            cycle_started_at=cycle_started_at,
                            details={"max_calls": int(getattr(args, "calculator_advisor_max_calls", 1))},
                        )
                        out["calculator_advisor"] = _run_calculator_advisor_stage(args, private_root, apply)
                    except Exception as exc:  # noqa: BLE001 - advisory stage must not break the cycle
                        out.setdefault("errors", []).append({"where": "calculator_advisor", "error": str(exc)})
                if getattr(args, "run_agent_role_reviews", False):
                    try:
                        _write_loop_status(
                            private_root,
                            stage="agent_role_reviews",
                            apply=apply,
                            loop=loop,
                            cycle_started_at=cycle_started_at,
                        )
                        from argparse import Namespace
                        from scripts.strategy_lab.agent_role_review_cycle import run_cycle as run_role_review_cycle
                        out["agent_role_reviews"] = run_role_review_cycle(Namespace(
                            private_root=private_root,
                            provider=getattr(args, "agent_role_provider", "alibaba"),
                            base_url=getattr(args, "agent_role_base_url", ""),
                            api_key_env=getattr(args, "agent_role_api_key_env", "ALIBABA_API_KEY"),
                            model=getattr(args, "agent_role_model", ""),
                            timeout=float(getattr(args, "agent_role_timeout", 60.0)),
                            rate_rub_per_1k=float(getattr(args, "agent_role_rate_rub_per_1k", 0.0)),
                            max_outcomes=int(getattr(args, "agent_role_max_outcomes", 1)),
                            max_validator=int(getattr(args, "agent_role_max_validator", 1)),
                            max_sources=int(getattr(args, "agent_role_max_sources", 1)),
                            sleep_seconds=float(getattr(args, "agent_role_sleep_seconds", 0.0)),
                        ))
                    except Exception as exc:  # noqa: BLE001 - advisory reviews must not break the cycle
                        out.setdefault("errors", []).append({"where": "agent_role_reviews", "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - paper lane must never break the cycle
                out.setdefault("errors", []).append({"where": "paper_signals", "error": str(exc)})
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
        farm_journal.log_cycle(private_root, ts=time.time(), mode="apply", result=out,
                               stages=stages, discovery=discovery_info)
        for e in out.get("errors") or []:
            farm_journal.log_error(private_root, where=e.get("where", "cycle"),
                                   error=e.get("error", ""), ts=time.time())
    _write_loop_status(
        private_root,
        stage="storage_maintenance",
        apply=apply,
        loop=loop,
        cycle_started_at=cycle_started_at,
    )
    _maybe_storage_maintain(private_root, apply)
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
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan only; write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="prepare/enrich/queue/classify/validate")
    run = ap.add_mutually_exclusive_group()
    run.add_argument("--once", action="store_true", help="one cycle then exit (default)")
    run.add_argument("--loop", action="store_true", help="run until stop-file / Ctrl+C")
    ap.add_argument("--run-worker", action="store_true", help="drain a few compute jobs each cycle")
    ap.add_argument("--run-validation", action="store_true", help="export + honest-backtest + stamp-back")
    ap.add_argument("--run-paper", action="store_true", help="simulate paper outcomes from validated setup cards")
    ap.add_argument("--run-paper-signals", action="store_true",
                    help="run one bounded operational paper-watch cycle (observe+generate; research-only)")
    ap.add_argument("--run-calculator-advisor", action="store_true",
                    help="run bounded calculator advisor over the latest feature packet (requires paper signals)")
    ap.add_argument("--run-agent-role-reviews", action="store_true",
                    help="run bounded advisory LLM reviews over outcomes/validator/source artifacts")
    ap.add_argument("--calculator-advisor-max-calls", type=int, default=1,
                    help="max calculator advisor calls per cycle")
    ap.add_argument("--calculator-provider", default="",
                    help="optional LLM provider override for calculator advisor, e.g. ollama")
    ap.add_argument("--calculator-model", default="",
                    help="optional calculator model override, e.g. calculator")
    ap.add_argument("--calculator-base-url", default="",
                    help="optional OpenAI-compatible base URL for calculator advisor")
    ap.add_argument("--calculator-timeout", type=float, default=0.0,
                    help="optional calculator advisor timeout seconds")
    ap.add_argument("--agent-role-provider", default="alibaba",
                    help="provider for advisory role reviews")
    ap.add_argument("--agent-role-base-url", default="",
                    help="optional OpenAI-compatible base URL for role reviews")
    ap.add_argument("--agent-role-api-key-env", default="ALIBABA_API_KEY",
                    help="environment variable that holds the role-review provider key")
    ap.add_argument("--agent-role-model", default="",
                    help="model for advisory role reviews")
    ap.add_argument("--agent-role-timeout", type=float, default=60.0,
                    help="role-review provider timeout seconds")
    ap.add_argument("--agent-role-rate-rub-per-1k", type=float, default=0.0,
                    help="optional accounting rate for role-review provider")
    ap.add_argument("--agent-role-max-outcomes", type=int, default=1,
                    help="max outcome rows reviewed per cycle")
    ap.add_argument("--agent-role-max-validator", type=int, default=1,
                    help="max validator rows reviewed per cycle")
    ap.add_argument("--agent-role-max-sources", type=int, default=1,
                    help="max scanner/source rows reviewed per cycle")
    ap.add_argument("--agent-role-sleep-seconds", type=float, default=0.0,
                    help="sleep between role-review provider calls")
    ap.add_argument("--true-forward-max-candidates", type=int, default=20,
                    help="max true-forward records collected per apply cycle; set 0 for wiring smoke checks")
    ap.add_argument("--paper-signals-max-new", type=int, default=5,
                    help="max new paper-watch cards generated per cycle; set 0 for wiring smoke checks")
    ap.add_argument("--pfr-db-path", default="",
                    help="path to strategy_lab.sqlite for PFR forward-watch lane "
                         "(requires --run-paper-signals; OFF by default — must be explicit)")
    ap.add_argument("--paper-signals-max-pfr-scan", type=int, default=30,
                    help="max PFR records inspected by --run-paper-signals per farm cycle")
    ap.add_argument("--paper-signals-max-pfr-fetches", type=int,
                    default=int(os.getenv("STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES", "8")),
                    help="max PFR candle fetch attempts per paper-signal cycle")
    ap.add_argument("--paper-signals-pfr-reserved", type=int, default=0,
                    help=("reserve this many new paper-watch card slots for PFR records when "
                          "--pfr-db-path is provided"))
    ap.add_argument("--paper-signals-max-observe", type=int, default=50,
                    help=("max active paper signals observed by --run-paper-signals per cycle "
                          "(set 0 for smoke checks)"))
    ap.add_argument("--paper-signals-max-live-fetches", type=int,
                    default=int(os.getenv("STRATEGY_LAB_PAPER_SIGNALS_MAX_LIVE_FETCHES", "12")),
                    help="max live-mover candle fetch attempts per paper-signal cycle")
    ap.add_argument("--paper-signals-max-network-fetches", type=int,
                    default=int(os.getenv("STRATEGY_LAB_PAPER_SIGNALS_MAX_NETWORK_FETCHES", "16")),
                    help="max observe+live candle fetch attempts per paper-signal cycle")
    ap.add_argument("--paper-signals-fetch-timeout", type=float, default=10.0,
                    help="per-request public OKX timeout used by --run-paper-signals")
    ap.add_argument("--paper-signals-timeframes", default="15m,1h,4h",
                    help="comma-separated paper-signal timeframes; default includes validator-heavy 4h PFR")
    ap.add_argument("--main-paper-runtime-limit", type=int, default=50,
                    help="max main-paper runtime queue items observed per --run-paper-signals cycle")
    ap.add_argument("--send-paper-telegram", action="store_true",
                    default=_env_flag("STRATEGY_LAB_PAPER_TELEGRAM_SEND"),
                    help=("opt-in network delivery of validated paper Telegram previews to active "
                          "subscription users; default is dry-run/preview only"))
    ap.add_argument("--run-journal-export", action="store_true",
                    default=_env_flag("STRATEGY_LAB_RUN_JOURNAL_EXPORT"),
                    help=("rebuild scripts/journal.xlsx after paper/training export; private fills are "
                          "forced off inside the farm loop"))
    ap.add_argument("--paper-telegram-limit", type=int,
                    default=int(os.getenv("STRATEGY_LAB_PAPER_TELEGRAM_SEND_LIMIT", "20")),
                    help="max paper Telegram previews delivered/audited per cycle")
    ap.add_argument("--enrich-funding", action="store_true", help="enable public funding enrichment tasks")
    ap.add_argument("--enrich-oi", action="store_true", help="enable public open-interest enrichment tasks")
    ap.add_argument("--backend", choices=["cpu", "auto", "gpu"], default="auto")
    ap.add_argument("--sweep-tier", choices=["smoke", "normal", "deep"], default="normal",
                    help="parameter-search depth: smoke=profile cap; normal=x2; deep=x4 (abs-capped)")
    ap.add_argument("--provider", choices=["okx-public", "synthetic"], default="okx-public")
    ap.add_argument("--max-plan-events", type=int, default=20)
    ap.add_argument("--max-prepares", type=int, default=4)
    ap.add_argument("--max-enrich", type=int, default=4)
    ap.add_argument("--max-sweeps", type=int, default=4)
    ap.add_argument("--max-worker-jobs", type=int, default=4)
    ap.add_argument("--max-validations", type=int, default=10)
    ap.add_argument("--max-paper-cards", type=int, default=20)
    ap.add_argument("--max-followups", type=int, default=10)
    ap.add_argument("--no-followups", action="store_true", help="disable automatic bounded feedback follow-ups")
    ap.add_argument("--data-days", type=int, default=None)
    ap.add_argument("--discovery-ttl-seconds", type=int, default=6 * 3600,
                    help="treat the discovery snapshot as fresh for this many seconds; refresh when older")
    ap.add_argument("--no-discovery-refresh", action="store_true",
                    help="never auto-refresh the discovery snapshot in apply mode (warn loudly if stale)")
    ap.add_argument("--night-mode", action="store_true")
    ap.add_argument("--sleep-seconds", type=int, default=180)
    ap.add_argument("--stop-file", default="")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    verb = ap.add_mutually_exclusive_group()
    verb.add_argument("--verbose", action="store_true", help="print the full cycle block every tick")
    verb.add_argument("--quiet", action="store_true", help="print only on change/error (loop heartbeat)")
    args = ap.parse_args()
    apply = bool(args.apply)

    print(f"farm_loop mode={'APPLY' if apply else 'DRY-RUN'} run={'loop' if args.loop else 'once'} "
          f"private_root={args.private_root}")
    print("safety: paper-only; public OKX market data; no orders / AUTO_TRADE / private endpoints; "
          "Telegram send is explicit opt-in")
    _print_stages(_stage_status(args, apply), apply)

    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=args.night_mode)
    if apply:
        private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
        tasks = FarmTasksDB(tasks_db_path(private_root))
        from src.research_lab.farm_journal import make_transition_sink
        tasks.on_transition = make_transition_sink(private_root)  # durable task-transition audit
        # A single-process loop has no live worker at boot, so any 'running' task is stale from a
        # previous stop. Requeue it once so the next cycle re-drains it instead of masking it as work.
        # Single-process guard: reconcile_orphan_running assumes no other live loop. A fresh lock
        # (younger than 2 cycles) means a second loop is active -> abort rather than corrupt state.
        lock_path = private_root / "state" / "farm_loop.lock"
        if args.loop and lock_path.exists():
            age = time.time() - lock_path.stat().st_mtime
            try:
                lock_pid = int(lock_path.read_text(encoding="utf-8").strip() or "0")
            except (OSError, ValueError):
                lock_pid = 0
            if lock_pid and not _pid_is_alive(lock_pid):
                print(f"  lock: removed stale farm_loop lock from dead pid={lock_pid}")
                lock_path.unlink(missing_ok=True)
            elif not lock_pid and age >= 2 * max(60, args.sleep_seconds):
                print(f"  lock: removed stale unreadable farm_loop lock age={age:.0f}s")
                lock_path.unlink(missing_ok=True)
            elif not lock_pid:
                print(f"ABORT: another farm_loop lock exists but pid is unreadable "
                      f"(lock {lock_path}, age {age:.0f}s); stop it or delete the lock to override.")
                return
            elif age < 2 * max(60, args.sleep_seconds):
                print(f"ABORT: another farm_loop appears active (lock {lock_path}, pid={lock_pid}, "
                      f"age {age:.0f}s); stop it or delete the lock to override.")
                return
            else:
                print(f"  lock: removed stale farm_loop lock pid={lock_pid} age={age:.0f}s")
                lock_path.unlink(missing_ok=True)
        if args.loop:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text(str(os.getpid()), encoding="utf-8")
        n_orphan = tasks.reconcile_orphan_running()
        if n_orphan:
            print(f"  reconcile: requeued {n_orphan} orphan running task(s) from a previous stop")
    else:
        private_root = Path(args.private_root)
        lock_path = None
        tasks = FarmTasksDB(":memory:")  # dry-run persists nothing

    try:
        if not args.loop:
            out = _run_once(args, tasks, profiles, policy, private_root, apply)
            _print_cycle(out)  # a single explicit cycle is always shown
            return
        prev_sig = None
        while True:
            if args.stop_file and Path(args.stop_file).exists():
                print(f"stop-file present ({args.stop_file}) - exiting loop")
                break
            out = _run_once(args, tasks, profiles, policy, private_root, apply)
            sig = _cycle_signature(out)
            # Always show a CHANGED cycle or any error (never hide a changed block, even with
            # --quiet). Unchanged cycle: heartbeat by default; with --quiet, print nothing.
            show_full = args.verbose or sig != prev_sig or bool(out.get("errors"))
            if show_full:
                print(f"\n=== farm cycle @ {int(time.time())} ===")
                _print_cycle(out)
            elif not args.quiet:
                print(f"  heartbeat @ {int(time.time())} pivot={out['pivot']} active={out['active_tasks']}")
            prev_sig = sig
            if apply and lock_path is not None:
                lock_path.write_text(str(os.getpid()), encoding="utf-8")  # keep the lock fresh
                _write_loop_status(
                    private_root,
                    stage="sleep",
                    apply=apply,
                    loop=True,
                    cycle_started_at=time.time(),
                    details={"sleep_seconds": args.sleep_seconds},
                )
            if not _sleep_until_next_cycle(args.sleep_seconds, args.stop_file):
                print(f"stop-file present ({args.stop_file}) - exiting loop")
                break
    except KeyboardInterrupt:
        print("\ninterrupted - graceful stop")
    finally:
        tasks.close()
        if apply and args.loop and lock_path is not None:
            try:
                lock_path.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
