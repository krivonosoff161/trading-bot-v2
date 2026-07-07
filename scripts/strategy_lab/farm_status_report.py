# -*- coding: utf-8 -*-
"""Read-only farm status report - what the calculation farm computed and what's next.

Reads strategy_lab.sqlite (runs / candidates / farm_results / runtime_stats) and
prints a clear operator summary: how much was computed, which assets/groups/timeframes,
the CPU/GPU backend split, the decision + validation breakdown, what was promoted vs
rejected and why, the queue state + age, flow coverage, and which candidates are ready
for hard validation (deduped). Migration-safe: it runs init_db so an old DB without the
v3 tables is upgraded non-destructively instead of crashing.

    python -m scripts.strategy_lab.farm_status_report --fast
    python -m scripts.strategy_lab.farm_status_report
    python -m scripts.strategy_lab.farm_status_report --fast --json

Never writes results, never touches the network, orders, .env, or private endpoints.
Use --fast for visible operator monitors; the default full report may rebuild derived
research views from many JSON artifacts and is intended for audit/drilldown.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402
from src.research_lab.state_db import default_db_path, init_db  # noqa: E402

READY_FOR_VALIDATION = ("FORWARD_PAPER", "REGIME_SPECIFIC")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_for_report(db_path: Path) -> tuple[sqlite3.Connection, str | None]:
    conn = _connect(db_path)
    try:
        init_db(conn)  # migration-safe: upgrade old DBs, non-destructive
        return conn, None
    except sqlite3.OperationalError as exc:
        conn.close()
        if "locked" not in str(exc).lower():
            raise
        return _connect_readonly(db_path), "database_locked_readonly"


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except (OSError, SystemError, ValueError):
        return False
    return True


def _label(value) -> str:
    # Distinguish a real NULL (UNKNOWN) from a literal empty status (legacy unscored rows).
    if value is None:
        return "UNKNOWN"
    if value == "":
        return "LEGACY_UNSCORED"
    return str(value)


def _counts(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    return {_label(r[0]): int(r[1]) for r in conn.execute(sql)}


def _scalar(conn: sqlite3.Connection, sql: str, default=0):
    row = conn.execute(sql).fetchone()
    return row[0] if row and row[0] is not None else default


def _has_rows(conn: sqlite3.Connection, table: str) -> bool:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) > 0
    except sqlite3.Error:
        return False


def _flow_coverage(db_path: Path) -> dict:
    """Funding/OI enrichment coverage from the loop's state file (if present)."""
    path = db_path.parent / "flow_enrich_state.json"
    if not path.exists():
        return {"tracked": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"tracked": False}
    counts: dict[str, int] = {}
    for entry in (data.get("entries") or {}).values():
        s = str(entry.get("status") or "unknown")
        counts[s] = counts.get(s, 0) + 1
    return {"tracked": True, "funding": counts}


def _farm_loop_status(private_root: Path, *, now: float | None = None) -> dict[str, object]:
    """Read the loop heartbeat so operator text does not imply a running loop stopped."""
    path = private_root / "state" / "farm_loop_status.json"
    if not path.exists():
        return {"available": False}
    now = time.time() if now is None else now
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "error": "unreadable"}
    updated_at = float(data.get("updated_at") or 0.0)
    details = data.get("details") if isinstance(data.get("details"), dict) else {}
    sleep_seconds = int(details.get("sleep_seconds") or 0)
    freshness_limit = max((sleep_seconds * 3) + 60, 180)
    age_seconds = max(0, int(now - updated_at)) if updated_at else None
    pid = int(data.get("pid") or 0)
    fresh = bool(updated_at and age_seconds is not None and age_seconds <= freshness_limit)
    pid_alive = _pid_is_alive(pid)
    active = bool(data.get("loop") and (pid_alive or (pid <= 0 and fresh)))
    return {
        "available": True,
        "active": active,
        "fresh": fresh,
        "age_seconds": age_seconds,
        "stage": data.get("stage"),
        "loop": bool(data.get("loop")),
        "paper_only": data.get("paper_only"),
        "execution_allowed": data.get("execution_allowed"),
        "sleep_seconds": sleep_seconds,
        "pid": pid,
        "pid_alive": pid_alive,
    }


def _ready_for_validation(conn: sqlite3.Connection) -> list[dict]:
    """Deduped FORWARD_PAPER/REGIME_SPECIFIC candidates, latest per (symbol, family, tf)."""
    if _has_rows(conn, "farm_results"):
        rows = conn.execute(
            "SELECT symbol, family, timeframe, validation_status, next_action, MAX(created_at) latest "
            "FROM farm_results WHERE validation_status IN (?, ?) "
            "GROUP BY symbol, family, timeframe ORDER BY validation_status DESC, latest DESC LIMIT 40",
            READY_FOR_VALIDATION).fetchall()
        return [dict(r) for r in rows]
    rows = conn.execute(
        "SELECT symbol, family, '' timeframe, validation_status, next_action "
        "FROM candidates WHERE validation_status IN (?, ?) "
        "GROUP BY symbol, family ORDER BY validation_status DESC LIMIT 40",
        READY_FOR_VALIDATION).fetchall()
    return [dict(r) for r in rows]


def _skipped_fast() -> dict[str, object]:
    return {"available": False, "skipped": "fast_mode"}


def collect(db_path: Path, *, fast: bool = False) -> dict:
    if not db_path.exists():
        return {"exists": False, "db": str(db_path)}
    conn, migration_note = _connect_for_report(db_path)
    try:
        unique_table = "farm_results" if _has_rows(conn, "farm_results") else "candidates"
        report: dict = {
            "exists": True,
            "report_mode": "fast" if fast else "full",
            "migration_note": migration_note,
            "schema_version": int(_scalar(conn, "SELECT value FROM meta WHERE key='schema_version'", 0)),
            "totals": {
                "runs": int(_scalar(conn, "SELECT COUNT(*) FROM runs")),
                "candidates": int(_scalar(conn, "SELECT COUNT(*) FROM candidates")),
                "farm_results": int(_scalar(conn, "SELECT COUNT(*) FROM farm_results")),
                "unique_candidates": int(_scalar(
                    conn, f"SELECT COUNT(DISTINCT symbol || '|' || family) FROM {unique_table}")),
            },
            "queue": _counts(conn, "SELECT status, COUNT(*) FROM queue GROUP BY status"),
            "queue_age": {
                "oldest_queued": _scalar(conn, "SELECT MIN(created_at) FROM queue WHERE status='queued'", None),
                "newest_queued": _scalar(conn, "SELECT MAX(created_at) FROM queue WHERE status='queued'", None),
            },
            "latest_run": dict(conn.execute(
                "SELECT run_id, created_at FROM runs ORDER BY run_id DESC LIMIT 1").fetchone() or {}),
            "decisions": _counts(conn, "SELECT decision, COUNT(*) FROM candidates GROUP BY decision"),
            "validation": _counts(conn, "SELECT validation_status, COUNT(*) FROM candidates GROUP BY validation_status"),
            "flow_coverage": _flow_coverage(db_path),
        }
        if _has_rows(conn, "farm_results"):
            report["by_group"] = [dict(r) for r in conn.execute(
                "SELECT asset_group, family, decision, COUNT(*) n, ROUND(AVG(avg_net_pct),4) avg_net "
                "FROM farm_results GROUP BY asset_group, family, decision ORDER BY asset_group, family")]
            report["by_timeframe"] = _counts(conn, "SELECT timeframe, COUNT(*) FROM farm_results GROUP BY timeframe")
            report["data_quality"] = _counts(conn, "SELECT data_quality, COUNT(*) FROM farm_results GROUP BY data_quality")
            report["handoff"] = {
                "validation_exported": int(_scalar(
                    conn, "SELECT COUNT(*) FROM farm_results WHERE validation_exported = 1")),
                "hard_status": _counts(
                    conn, "SELECT hard_status, COUNT(*) FROM farm_results WHERE hard_status <> '' GROUP BY hard_status"),
                "paper_status": _counts(
                    conn, "SELECT paper_status, COUNT(*) FROM farm_results WHERE paper_status <> '' GROUP BY paper_status"),
                "paper_outcomes": int(_scalar(conn, "SELECT COUNT(*) FROM paper_outcomes")),
                "gpu_signal_rows": int(_scalar(
                    conn, "SELECT COALESCE(SUM(gpu_signal_supported), 0) FROM farm_results")),
                "needs_data": _counts(
                    conn, "SELECT decision, COUNT(*) FROM farm_results WHERE decision LIKE 'NEEDS_%' GROUP BY decision"),
            }
        if _has_rows(conn, "runtime_stats"):
            report["backend"] = [dict(r) for r in conn.execute(
                "SELECT effective_backend, signal_backend, simulation_backend, "
                "SUM(gpu_available) gpu_runs, COUNT(*) runs, "
                "GROUP_CONCAT(DISTINCT fallback_reason) fallbacks "
                "FROM runtime_stats GROUP BY effective_backend, signal_backend, simulation_backend")]
        report["ready_for_validation"] = _ready_for_validation(conn)
        # Real totals behind the capped preview (the LIMIT 40 list masked the backlog).
        if _has_rows(conn, "farm_results"):
            report["ready_total_groups"] = int(_scalar(conn,
                "SELECT COUNT(*) FROM (SELECT 1 FROM farm_results WHERE validation_status IN "
                "('FORWARD_PAPER','REGIME_SPECIFIC') GROUP BY symbol, family, timeframe)"))
            report["unvalidated_count"] = int(_scalar(conn,
                "SELECT COUNT(*) FROM farm_results WHERE validation_status IN "
                "('FORWARD_PAPER','REGIME_SPECIFIC') AND (hard_status IS NULL OR hard_status='')"))
        try:  # the continuous-farm lifecycle (farm_tasks.sqlite), if the new loop has run
            from src.research_lab.farm_cockpit import _lifecycle_section
            report["lifecycle"] = _lifecycle_section(db_path.parent.parent)
        except Exception:  # noqa: BLE001 - report must never crash on the optional new DB
            report["lifecycle"] = {"available": False}
        if fast:
            report["setup_lifecycle"] = _skipped_fast()
            report["outcome_memory"] = _skipped_fast()
        else:
            try:
                from src.research_lab.setup_lifecycle import summarize_setup_lifecycle
                report["setup_lifecycle"] = summarize_setup_lifecycle(db_path.parent.parent)
            except Exception:  # noqa: BLE001 - optional derived view must not break status
                report["setup_lifecycle"] = {"available": False}
            try:  # Setup Outcome Memory: rejected-as-knowledge sub-views (derived, research-only)
                from src.research_lab.setup_outcome_memory import (
                    build_memory_index,
                    summarize_memory,
                    summarize_product_training_memory,
                )
                report["outcome_memory"] = {
                    **summarize_memory(build_memory_index(db_path.parent.parent)),
                    "product_paper_memory": summarize_product_training_memory(db_path.parent.parent)["summary"],
                }
            except Exception:  # noqa: BLE001 - optional derived view must not break status
                report["outcome_memory"] = {"available": False}
        try:  # shadow-forward watch lane (research-only; survivors observed on new bars, never traded)
            from src.research_lab.shadow_forward import summarize_shadow
            report["shadow_forward"] = summarize_shadow(db_path.parent.parent)
        except Exception:  # noqa: BLE001 - optional derived view must not break status
            report["shadow_forward"] = {}
        try:  # Exit Phase-2 dynamic-exit re-sim summary (research-only)
            from src.research_lab.exit_phase2 import summarize_exit_phase2
            deriv = db_path.parent.parent / "state" / "derived" / "exit_phase2.json"
            data = json.loads(deriv.read_text(encoding="utf-8")) if deriv.exists() else {}
            report["exit_phase2"] = data.get("summary") or summarize_exit_phase2([])
        except Exception:  # noqa: BLE001 - optional derived view must not break status
            report["exit_phase2"] = {}
        try:  # OI-family research summary (separate oi_* namespace; 1h/4h only)
            oi_f = db_path.parent.parent / "state" / "derived" / "oi_family_research.json"
            report["oi_family"] = (json.loads(oi_f.read_text(encoding="utf-8")).get("summary") or {}) \
                if oi_f.exists() else {}
        except Exception:  # noqa: BLE001 - optional derived view must not break status
            report["oi_family"] = {}
        try:  # bounded OOS / shadow-forward verdict on survivors (held-out-tail pseudo-OOS, research-only)
            oos_f = db_path.parent.parent / "state" / "derived" / "shadow_oos.json"
            report["shadow_oos"] = (json.loads(oos_f.read_text(encoding="utf-8")).get("summary") or {}) \
                if oos_f.exists() else {}
        except Exception:  # noqa: BLE001 - optional derived view must not break status
            report["shadow_oos"] = {}
        try:  # true-forward collector (new bars, not held-out tail) — pending until data-prepare fetches
            tf_f = db_path.parent.parent / "state" / "derived" / "true_forward.json"
            report["true_forward"] = (json.loads(tf_f.read_text(encoding="utf-8")).get("summary") or {}) \
                if tf_f.exists() else {}
        except Exception:  # noqa: BLE001 - optional derived view must not break status
            report["true_forward"] = {}
        try:  # tactical-probe characterization of sub-power net-positive setups (research-only, not edge)
            tp_f = db_path.parent.parent / "state" / "derived" / "tactical_probe.json"
            report["tactical_probe"] = (json.loads(tp_f.read_text(encoding="utf-8")).get("probe") or {}) \
                if tp_f.exists() else {}
        except Exception:  # noqa: BLE001 - optional derived view must not break status
            report["tactical_probe"] = {}
        try:  # 15m OI delta_coarse diagnostic bucket (never edge)
            od_f = db_path.parent.parent / "state" / "derived" / "oi_diagnostic_15m.json"
            report["oi_diagnostic_15m"] = (json.loads(od_f.read_text(encoding="utf-8")).get("summary") or {}) \
                if od_f.exists() else {}
        except Exception:  # noqa: BLE001 - optional derived view must not break status
            report["oi_diagnostic_15m"] = {}
        try:  # latest bounded discovery cycle (live universe -> validate -> tactical -> exit -> memory)
            dc_f = db_path.parent.parent / "state" / "derived" / "discovery_cycle.json"
            report["discovery_cycle"] = json.loads(dc_f.read_text(encoding="utf-8")) if dc_f.exists() else {}
        except Exception:  # noqa: BLE001 - optional derived view must not break status
            report["discovery_cycle"] = {}
        try:  # tactical track (parallel verdict lane; NO_EVENT != bad; leads = forward-watch, never paper-ready)
            tt_f = db_path.parent.parent / "state" / "derived" / "tactical_track.json"
            report["tactical_track"] = (json.loads(tt_f.read_text(encoding="utf-8")).get("summary") or {}) \
                if tt_f.exists() else {}
        except Exception:  # noqa: BLE001 - optional derived view must not break status
            report["tactical_track"] = {}
        try:  # Theme 40 microstructure lane (separate research-only lane; tape replay + orderbook recorder)
            mm_f = db_path.parent.parent / "state" / "derived" / "micro_memory.json"
            report["microstructure"] = (json.loads(mm_f.read_text(encoding="utf-8")).get("summary") or {}) \
                if mm_f.exists() else {}
        except Exception:  # noqa: BLE001 - optional derived view must not break status
            report["microstructure"] = {}
        if fast:
            report["knowledge_base"] = _skipped_fast()
        else:
            try:  # the owner's six knowledge-base counts in one line (gate + survived + tactical + recyclable)
                from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
                from src.research_lab.setup_outcome_memory import (
                    build_gate_index, build_memory_index, knowledge_base_counts)
                db = FarmTasksDB(tasks_db_path(db_path.parent.parent))
                try:
                    gate_idx = build_gate_index(db.unique_candidates_for_gate())
                finally:
                    db.close()
                report["knowledge_base"] = knowledge_base_counts(
                    build_memory_index(db_path.parent.parent), gate_idx,
                    survived_shadow=len((report.get("shadow_oos") or {}).get("survived") or []),
                    tactical_probe=int((report.get("tactical_probe") or {}).get("thin_positive") or 0))
            except Exception:  # noqa: BLE001 - optional derived view must not break status
                report["knowledge_base"] = {}
        try:  # last cycle row - surfaces skipped active stages (0.2)
            from src.research_lab import farm_journal
            cycles = farm_journal.read_recent_cycles(db_path.parent.parent, limit=1)
            report["last_cycle"] = cycles[-1] if cycles else None
        except Exception:  # noqa: BLE001 - optional log read must not break status
            report["last_cycle"] = None
        report["farm_loop_status"] = _farm_loop_status(db_path.parent.parent)
        try:  # completion verdict + two-DB reconciliation (read-only, T2)
            from src.research_lab.farm_reconcile import completion_verdict, reconcile_dbs
            report["completion"] = completion_verdict(db_path.parent.parent)
            report["reconcile"] = reconcile_dbs(db_path.parent.parent)
        except Exception:  # noqa: BLE001 - optional reconcile must not break status
            report["completion"] = {"available": False}
            report["reconcile"] = {"available": False}
        try:  # decode blocked/deferred tails into structural reasons (read-only)
            from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
            from src.research_lab.tail_diagnostics import load_universe_symbols, summarize_tails
            db = FarmTasksDB(tasks_db_path(db_path.parent.parent))
            try:
                blocked = db.tasks_in_state("blocked")
                deferred = db.tasks_in_state("deferred")
            finally:
                db.close()
            uni = load_universe_symbols(db_path.parent.parent)
            needs_oi = int((report.get("decisions") or {}).get("NEEDS_OI_DATA") or 0)
            report["tail_diagnostics"] = summarize_tails(blocked, deferred, uni, needs_oi)
        except Exception:  # noqa: BLE001 - optional decode must not break status
            report["tail_diagnostics"] = {}
        try:  # operational paper-watch lane snapshot (read-only surface)
            ps_path = db_path.parent.parent / "state" / "derived" / "paper_signals.json"
            report["paper_signals"] = json.loads(ps_path.read_text(encoding="utf-8")) if ps_path.exists() else {}
        except Exception:  # noqa: BLE001 - optional surface must not break status
            report["paper_signals"] = {}
        try:  # main-readable paper instruction view (derived, never orders)
            mp_path = db_path.parent.parent / "state" / "derived" / "main_paper_instructions.json"
            report["main_paper_bridge"] = json.loads(mp_path.read_text(encoding="utf-8")) if mp_path.exists() else {}
        except Exception:  # noqa: BLE001 - optional surface must not break status
            report["main_paper_bridge"] = {}
        try:  # paper-only consumer audit for main-readable instructions (never orders)
            mc_path = db_path.parent.parent / "state" / "derived" / "main_paper_consumed.json"
            report["main_paper_consumer"] = json.loads(mc_path.read_text(encoding="utf-8")) if mc_path.exists() else {}
        except Exception:  # noqa: BLE001 - optional surface must not break status
            report["main_paper_consumer"] = {}
        try:  # main-compatible paper runtime queue (never orders)
            rt_path = db_path.parent.parent / "state" / "derived" / "main_paper_runtime_queue.json"
            report["main_paper_runtime_queue"] = json.loads(rt_path.read_text(encoding="utf-8")) if rt_path.exists() else {}
        except Exception:  # noqa: BLE001 - optional surface must not break status
            report["main_paper_runtime_queue"] = {}
        try:  # adaptive policy selected for main paper runtime rows (never prices/orders)
            ap_path = db_path.parent.parent / "state" / "derived" / "main_adaptive_policy.json"
            report["main_adaptive_policy"] = json.loads(ap_path.read_text(encoding="utf-8")) if ap_path.exists() else {}
        except Exception:  # noqa: BLE001 - optional surface must not break status
            report["main_adaptive_policy"] = {}
        try:  # paper-only runtime observation of the main-compatible queue (never orders)
            rto_path = db_path.parent.parent / "state" / "derived" / "main_paper_runtime_observation.json"
            report["main_paper_runtime_observation"] = (
                json.loads(rto_path.read_text(encoding="utf-8")) if rto_path.exists() else {}
            )
        except Exception:  # noqa: BLE001 - optional surface must not break status
            report["main_paper_runtime_observation"] = {}
        try:  # validated main-paper trade ledger (never orders)
            tr_path = db_path.parent.parent / "state" / "derived" / "main_paper_trades.json"
            report["main_paper_trade_ledger"] = json.loads(tr_path.read_text(encoding="utf-8")) if tr_path.exists() else {}
        except Exception:  # noqa: BLE001 - optional surface must not break status
            report["main_paper_trade_ledger"] = {}
        try:  # broad subscriber-facing paper product ledger (never orders)
            ptr_path = db_path.parent.parent / "state" / "derived" / "paper_product_trades.json"
            report["paper_product_trade_ledger"] = (
                json.loads(ptr_path.read_text(encoding="utf-8")) if ptr_path.exists() else {}
            )
        except Exception:  # noqa: BLE001 - optional surface must not break status
            report["paper_product_trade_ledger"] = {}
        try:  # offline Telegram-card preview for paper-watch instructions (never sends)
            pt_path = db_path.parent.parent / "state" / "derived" / "paper_telegram_preview.json"
            report["paper_telegram_preview"] = json.loads(pt_path.read_text(encoding="utf-8")) if pt_path.exists() else {}
        except Exception:  # noqa: BLE001 - optional surface must not break status
            report["paper_telegram_preview"] = {}
        try:  # Telegram delivery audit for paper cards; reading this never sends.
            pd_path = db_path.parent.parent / "state" / "derived" / "paper_telegram_delivery.json"
            report["paper_telegram_delivery"] = json.loads(pd_path.read_text(encoding="utf-8")) if pd_path.exists() else {}
        except Exception:  # noqa: BLE001 - optional surface must not break status
            report["paper_telegram_delivery"] = {}
        try:  # Aggregate paper-product quality digest; raw private rows stay in the derived report.
            pq_path = db_path.parent.parent / "state" / "derived" / "paper_product_quality_report.json"
            report["paper_product_quality"] = json.loads(pq_path.read_text(encoding="utf-8")) if pq_path.exists() else {}
        except Exception:  # noqa: BLE001 - optional surface must not break status
            report["paper_product_quality"] = {}
        try:  # PFR bridge: how many canonical records survive quality + risk gates
            from src.research_lab.paper_signals import pfr_bridge
            from src.research_lab.paper_signals.lane import MAX_RISK_PCT
            pfr_recs = pfr_bridge.load_pfr_records(db_path)
            pfr_passed, pfr_rej = pfr_bridge.apply_quality_policy(pfr_recs)
            report["pfr_bridge"] = {
                "records_loaded": len(pfr_recs),
                "passed_quality": len(pfr_passed),
                "rejected_quality": len(pfr_rej),
                "unique_setups": len({(r["symbol"], r["timeframe"], r["family"]) for r in pfr_passed}),
                "risk_too_wide": sum(
                    1 for r in pfr_passed
                    if float(r["params"].get("stop_pct") or 0) > MAX_RISK_PCT
                ),
            }
        except Exception:  # noqa: BLE001 - optional bridge audit must not break status
            report["pfr_bridge"] = {}
        report["recent_runs"] = [dict(r) for r in conn.execute(
            "SELECT run_id, candidate_count, promote_count, observe_count, reject_count "
            "FROM runs ORDER BY run_id DESC LIMIT 8")]
        return report
    finally:
        conn.close()


def _print(report: dict) -> None:
    if not report.get("exists"):
        print(f"no farm DB yet at {report.get('db')} - run the farm first: "
              "python -m scripts.strategy_lab.farm_loop --once --apply --run-worker")
        return
    t = report["totals"]
    mode = report.get("report_mode") or "full"
    print(f"FARM STATUS ({mode}) - schema v{report['schema_version']} | runs={t['runs']} candidates={t['candidates']} "
          f"farm_results={t['farm_results']} unique={t['unique_candidates']}")
    lr = report.get("latest_run") or {}
    print(f"  latest run: {lr.get('run_id', '-')} @ {lr.get('created_at', '-')}")
    print(f"  queue: {report['queue'] or '(empty)'}  age[{report['queue_age']['oldest_queued'] or '-'} .. "
          f"{report['queue_age']['newest_queued'] or '-'}]")
    print(f"  decisions: {report['decisions'] or '(none)'}")
    print(f"  validation: {report['validation'] or '(none)'}")
    if report.get("by_timeframe"):
        print(f"  timeframes: {report['by_timeframe']}")
    if report.get("data_quality"):
        print(f"  data quality: {report['data_quality']}")
    fc = report.get("flow_coverage") or {}
    print(f"  flow coverage: {fc.get('funding') if fc.get('tracked') else 'not tracked yet'}")
    lc = report.get("lifecycle") or {}
    if lc.get("available"):
        print(f"  lifecycle tasks: {lc.get('by_state') or '(none)'}")
        print(f"    by type: {lc.get('by_task_type') or '(none)'}")
        if lc.get("blocked_reasons"):
            print(f"    blocked: {lc['blocked_reasons']}")
        if lc.get("deferred_reasons"):
            print(f"    deferred: {lc['deferred_reasons']}")
        print(f"    intake_unconsumed={lc.get('intake_unconsumed', 0)} "
              f"calcs_today={lc.get('calcs_completed_today', 0)} "
              f"unique_candidates={lc.get('unique_candidates', 0)} "
              f"validation={lc.get('validation') or '(none)'}")
    last = report.get("last_cycle")
    if last:
        import time as _time

        from src.research_lab.farm_journal import skipped_stages
        age = int(_time.time() - float(last.get("ts") or 0))
        print(f"  last cycle: mode={last.get('mode')} pivot={last.get('pivot')} age={age}s ago")
        skipped = skipped_stages(last)
        if skipped:
            print(f"  WARNING missing active stages last cycle: {', '.join(skipped)} "
                  "(loop queued work but did not run them)")
        disc = last.get("discovery") or {}
        if disc.get("status") in {"stale_no_refresh", "missing"}:
            print(f"  WARNING discovery universe {disc.get('status')} "
                  f"(age={disc.get('age_seconds')}s) - refresh via discover_okx_universe --apply")
    ho = report.get("handoff")
    if ho:
        print(f"  validation handoff: exported={ho['validation_exported']} "
              f"hard_status={ho['hard_status'] or '(none)'} needs_data={ho['needs_data'] or '(none)'} "
              f"gpu_signal_rows={ho['gpu_signal_rows']}")
        print(f"  paper handoff: outcomes={ho.get('paper_outcomes', 0)} "
              f"paper_status={ho.get('paper_status') or '(none)'}")
    comp = report.get("completion") or {}
    if comp.get("available"):
        rs = comp.get("reasons") or {}
        line = f"  COMPLETION: {comp.get('state')}"
        if comp.get("state") != "DRAINED":
            loop_status = report.get("farm_loop_status") or {}
            loop_running = bool(loop_status.get("active"))
            if loop_running:
                loop_hint = (
                    f" (loop running stage={loop_status.get('stage')}, "
                    f"heartbeat_age={loop_status.get('age_seconds')}s; worker will continue draining)"
                )
            else:
                loop_hint = " (loop stopped with claimable work; run farm_loop --apply --run-worker to drain)"
            line += (f" - eligible_now={rs.get('eligible_now', 0)} running={rs.get('running', 0)} "
                     f"deferred_future={rs.get('deferred_future', 0)} blocked={sum((rs.get('blocked') or {}).values())}"
                     f"{loop_hint}")
        print(line)
    rc = report.get("reconcile") or {}
    for name, info in (rc.get("orphans") or {}).items():
        print(f"  reconcile orphan: {name}={info.get('count')} owner={info.get('owner')} "
              f"{'-> ' + info.get('relabel') if info.get('relabel') else ''}")
    td = report.get("tail_diagnostics") or {}
    for kind in ("provider_error", "too_short"):
        for reason, syms in (td.get(kind) or {}).items():
            preview = ", ".join(syms[:6]) + (f" +{len(syms) - 6}" if len(syms) > 6 else "")
            print(f"  tail {kind}: {reason} -> [{preview}]")
    if td.get("needs_oi", {}).get("count"):
        oi = td["needs_oi"]
        print(f"  tail needs_oi: {oi['reason']} (n={oi['count']}) -> {oi['next_action']}")
    ps = report.get("paper_signals") or {}
    if ps.get("total"):
        print(f"  paper signals (operational watch lane, research-only NOT orders): total={ps['total']} "
              f"by_status={ps.get('by_status') or '(none)'}")
    mp = report.get("main_paper_bridge") or {}
    if mp.get("instructions") is not None:
        print("  main paper bridge: "
              f"instructions={mp.get('instructions', 0)} "
              f"paper_only={mp.get('paper_only')} execution_allowed={mp.get('execution_allowed')} "
              "(derived view; main runtime not live-consuming)")
        if mp.get("skipped_unvalidated"):
            print("    main paper skip: "
                  f"skipped_unvalidated={mp.get('skipped_unvalidated', 0)} "
                  f"reasons={mp.get('skip_reasons') or {}}")
            examples = mp.get("skipped_examples") or []
            if examples:
                first = examples[0]
                print("    main paper skip example: "
                      f"{first.get('symbol')} {first.get('timeframe')} "
                      f"{first.get('family')} -> {first.get('reason')}")
    mc = report.get("main_paper_consumer") or {}
    if mc.get("instructions_read") is not None:
        print("  main paper consumer: "
              f"read={mc.get('instructions_read', 0)} "
              f"accepted={mc.get('accepted', 0)} rejected={mc.get('rejected', 0)} "
              f"paper_only={mc.get('paper_only')} execution_allowed={mc.get('execution_allowed')} "
              "(paper-watch audit; no order path)")
    rtq = report.get("main_paper_runtime_queue") or {}
    if rtq.get("rows_read") is not None:
        print("  main paper runtime queue: "
              f"read={rtq.get('rows_read', 0)} queued={rtq.get('queued', 0)} "
              f"invalid={rtq.get('invalid', 0)} action={rtq.get('runtime_action')} "
              f"execution_allowed={rtq.get('execution_allowed')} "
              "(paper watch queue; old main executor still disabled)")
    ap = report.get("main_adaptive_policy") or {}
    if ap.get("policies") is not None:
        print("  main adaptive policy: "
              f"policies={ap.get('policies', 0)} "
              f"by_profile={ap.get('by_execution_profile') or '(none)'} "
              f"execution_allowed={ap.get('execution_allowed')} "
              "(LLM/profile lane; code still owns prices and outcomes)")
    rto = report.get("main_paper_runtime_observation") or {}
    if rto.get("rows_read") is not None:
        print("  main paper runtime observation: "
              f"read={rto.get('rows_read', 0)} observed={rto.get('observed', 0)} "
              f"reviewed={rto.get('reviewed', 0)} pending={rto.get('pending', 0)} "
              f"invalid={rto.get('invalid', 0)} provider_error={rto.get('provider_error', 0)} "
              f"execution_allowed={rto.get('execution_allowed')} "
              "(paper lifecycle observer; no order path)")
    tl = report.get("main_paper_trade_ledger") or {}
    if tl.get("trades") is not None:
        print("  main paper trade ledger: "
              f"trades={tl.get('trades', 0)} invalid={tl.get('invalid', 0)} "
              f"by_status={tl.get('by_status') or '(none)'} "
              f"execution_allowed={tl.get('execution_allowed')} "
              "(validated/farm-calculated paper trades; no order path)")
        money = tl.get("paper_money") if isinstance(tl.get("paper_money"), dict) else {}
        if money:
            print("    main paper money: "
                  f"terminal={money.get('terminal_trades', 0)} "
                  f"wins={money.get('wins', 0)} losses={money.get('losses', 0)} "
                  f"pnl_usdt={money.get('total_pnl_usdt', 0)} "
                  f"avg_usdt={money.get('avg_pnl_usdt', 0)}")
    ptl = report.get("paper_product_trade_ledger") or {}
    if ptl.get("trades") is not None:
        money = ptl.get("paper_money") if isinstance(ptl.get("paper_money"), dict) else {}
        print("  paper product trade ledger: "
              f"trades={ptl.get('trades', 0)} active={ptl.get('active_trades', 0)} "
              f"live_ready={ptl.get('live_ready', 0)} live_blocked={ptl.get('live_blocked', 0)} "
              f"by_status={ptl.get('by_status') or '(none)'} "
              f"paper_money_pnl={money.get('total_pnl_usdt', 0) if money else 0} "
              "(subscriber paper ledger; no order path)")
    pt = report.get("paper_telegram_preview") or {}
    if pt.get("rendered") is not None:
        print("  paper Telegram preview: "
              f"rendered={pt.get('rendered', 0)} invalid={pt.get('invalid', 0)} "
              f"sends_network={pt.get('sends_network')} "
              "(offline cards; no Telegram API call)")
    ptd = report.get("paper_telegram_delivery") or {}
    if ptd.get("eligible_cards") is not None or ptd.get("eligible") is not None:
        eligible = ptd.get("eligible_cards", ptd.get("eligible", 0))
        print("  paper Telegram delivery: "
              f"eligible_cards={eligible} targets={ptd.get('targets', 0)} "
              f"sent_messages={ptd.get('sent_messages', ptd.get('sent', 0))} "
              f"sent_cards={ptd.get('sent_cards', 0)} "
              f"duplicate_messages={ptd.get('duplicate_messages', ptd.get('duplicates', 0))} "
              f"duplicate_cards={ptd.get('duplicate_cards', 0)} "
              f"errors={ptd.get('errors', 0)} dry_run={ptd.get('dry_run')} "
              f"status_digest_sent={ptd.get('status_digest_sent_messages', 0)} "
              f"status_digest_reason={ptd.get('status_digest_reason') or '-'} "
              f"sends_network={ptd.get('sends_network')} "
              "(audit only; no order path)")
    pq = report.get("paper_product_quality") or {}
    if pq.get("schema") == "paper_product_quality_report.v1":
        print("  paper product quality: "
              f"action={pq.get('operator_action') or 'unknown'} "
              f"active={pq.get('active_trades', 0)} "
              f"active_live_ready={pq.get('active_live_ready', 0)} "
              f"active_live_blocked={pq.get('active_live_blocked', 0)} "
              f"labels={pq.get('quality_labels') or {}} "
              f"training_rows={pq.get('training_rows', 0)} "
              f"results={pq.get('training_by_result') or {}} "
              "(aggregate only)")
        families = pq.get("families") if isinstance(pq.get("families"), list) else []
        preview: list[str] = []
        for item in families[:3]:
            if not isinstance(item, dict):
                continue
            preview.append(
                f"{item.get('family')}:{item.get('quality_label')} "
                f"rows={item.get('rows', 0)} take_rate={item.get('take_rate', 0)} "
                f"avg_net_r={item.get('avg_net_r', 0)}"
            )
        if preview:
            print(f"    paper family quality: {'; '.join(preview)}")
        lifecycle = pq.get("active_signal_lifecycle") if isinstance(pq.get("active_signal_lifecycle"), dict) else {}
        if lifecycle:
            print("    paper active lifecycle: "
                  f"active={lifecycle.get('active', 0)} "
                  f"pending={lifecycle.get('pending_outcomes', 0)} "
                  f"by_status={lifecycle.get('by_status') or {}} "
                  f"outcomes={lifecycle.get('by_outcome_result') or {}}")
            print("    paper active timing: "
                  f"oldest_h={lifecycle.get('oldest_age_hours', 0)} "
                  f"next_expiry_h={lifecycle.get('next_expiry_hours')} "
                  f"overdue={lifecycle.get('overdue_expiry', 0)} "
                  f"expiry_buckets={lifecycle.get('expiry_buckets') or {}}")
        pfr_state = pq.get("pfr_trigger_state") if isinstance(pq.get("pfr_trigger_state"), dict) else {}
        if pfr_state:
            print("    PFR live-trigger state: "
                  f"state={pfr_state.get('state') or 'unknown'} "
                  f"catalog_ready={pfr_state.get('catalog_ready', 0)} "
                  f"validated_instructions={pfr_state.get('bridge_validated_instructions', 0)} "
                  f"pfr_generated={pfr_state.get('last_cycle_pfr_generated', 0)} "
                  f"generated={pfr_state.get('last_cycle_generated', 0)} "
                  f"reasons={pfr_state.get('top_reasons') or {}}")
        pfr_funnel = pq.get("pfr_funnel") if isinstance(pq.get("pfr_funnel"), dict) else {}
        near_reasons = pfr_funnel.get("near_trigger_counts") if isinstance(pfr_funnel, dict) else {}
        if near_reasons:
            print(f"    PFR near-trigger buckets: {near_reasons}")
        resource_reasons = pfr_funnel.get("cycle_resource_reasons") if isinstance(pfr_funnel, dict) else {}
        if resource_reasons:
            print(f"    cycle resource/data blockers: {resource_reasons}")
    pfr_snap = report.get("pfr_bridge") or {}
    if pfr_snap.get("records_loaded") is not None:
        print(f"  PFR bridge: records_loaded={pfr_snap['records_loaded']} "
              f"passed_quality={pfr_snap.get('passed_quality', 0)} "
              f"rejected_quality={pfr_snap.get('rejected_quality', 0)} "
              f"unique_setups={pfr_snap.get('unique_setups', 0)}")
        if pfr_snap.get("risk_too_wide"):
            print(f"    geometry gate: stop_pct>MAX_RISK_PCT rejected_approx={pfr_snap['risk_too_wide']} "
                  f"(exact gate runs at signal-time)")
        print("    activation: farm_loop --run-paper-signals --pfr-db-path <path>  "
              "OR  paper_signals_run --pfr-db-path <path>")
        print("    NOTE: PFR = farm backtest validated; forward observation only, NOT edge, NOT order")
    sl = report.get("setup_lifecycle") or {}
    if sl.get("available"):
        print(f"  setup lifecycle: total={sl.get('total', 0)} states={sl.get('by_state') or '(none)'}")
        if sl.get("by_tactical"):
            print(f"    tactical shelf (research-only, never tradeable): {sl['by_tactical']}")
        print(f"    paper groups: positive={sl.get('positive_setups', 0)} "
              f"negative={sl.get('negative_setups', 0)} mixed={sl.get('mixed_or_flat', 0)} "
              f"no_sample={sl.get('no_paper_sample', 0)}")
    om = report.get("outcome_memory") or {}
    if om.get("total"):
        print(f"  outcome memory (rejected-as-knowledge, research-only): total={om['total']} "
              f"by_class={om.get('by_outcome_class') or '(none)'}")
        print(f"    sub-DBs: positive={om.get('positive', 0)} recovered={om.get('recovered', 0)} "
              f"statistical={om.get('statistical_candidates', 0)} tactical={om.get('tactical', 0)} "
              f"rejected_research={om.get('rejected_research', 0)} confirmed_bad={om.get('confirmed_bad', 0)} "
              f"needs_data={om.get('needs_data', 0)}")
        if om.get("revalidated"):
            print(f"    re-validated={om['revalidated']} survivors={om.get('revalidation_survivors', 0)} "
                  "(survivor = research-only, needs human GO + OOS, never auto paper-ready)")
        if om.get("by_cost_class"):
            print(f"    tactical library: cost={om.get('by_cost_class')} "
                  f"one_shot={om.get('one_shot_candidates', 0)} "
                  f"maker_unlock={om.get('cost_bound_maker_unlock', 0)} (maker = hypothesis, not edge)")
            print(f"    next_action queue: {om.get('by_next_action')}")
        if om.get("paper_memory_rows"):
            print(f"    paper memory: rows={om.get('paper_memory_rows', 0)} "
                  f"terminal={om.get('paper_terminal_rows', 0)} "
                  f"pnl_usdt={om.get('paper_pnl_usdt', 0)} "
                  f"avg_usdt={om.get('paper_avg_pnl_usdt', 0)} "
                  f"gave_back={om.get('paper_gave_back_rows', 0)}")
        product_memory = om.get("product_paper_memory") if isinstance(om.get("product_paper_memory"), dict) else {}
        if product_memory:
            print(f"    product paper memory: rows={product_memory.get('rows', 0)} "
                  f"terminal={product_memory.get('terminal_rows', 0)} "
                  f"pnl_usdt={product_memory.get('paper_pnl_usdt', 0)} "
                  f"avg_usdt={product_memory.get('avg_paper_pnl_usdt', 0)} "
                  f"gave_back={product_memory.get('gave_back_rows', 0)} "
                  "(broad cards; not validator promotion)")
    sh = report.get("shadow_forward") or {}
    if sh.get("shadow_candidates"):
        print(f"  shadow-forward watch: candidates={sh['shadow_candidates']} by_family={sh.get('by_family')} "
              f"(forward-only, no execution; all_research_only={sh.get('all_research_only')})")
    ep = report.get("exit_phase2") or {}
    if ep.get("evaluated"):
        print(f"  exit phase-2 (mean_rev, research-only): evaluated={ep['evaluated']} by_class={ep.get('by_class')} "
              f"needs_forward_only={ep.get('needs_forward_only', 0)} (recovered != edge; never auto paper-ready)")
    oif = report.get("oi_family") or {}
    if oif.get("evaluated"):
        print(f"  oi families (1h/4h only, separate oi_* class): evaluated={oif['evaluated']} "
              f"by_class={oif.get('by_class')} honest_passed={oif.get('honest_passed', 0)} (OI availability != edge)")
        if om.get("paper_ready_without_hard_pass"):
            print(f"    WARNING invariant breach: {om['paper_ready_without_hard_pass']} rows "
                  "paper_forward_ready WITHOUT a hard PAPER_FORWARD_READY verdict")
    so = report.get("shadow_oos") or {}
    if so.get("evaluated"):
        print(f"  shadow OOS (held-out-tail, research-only): evaluated={so['evaluated']} "
              f"by_class={so.get('by_class')} survived={len(so.get('survived') or [])} "
              "(pseudo-OOS, not new bars; shadow_survived != edge)")
    tf = report.get("true_forward") or {}
    if tf.get("watched"):
        print(f"  true-forward (new bars, research-only): watched={tf['watched']} "
              f"by_status={tf.get('by_status')} matured={tf.get('matured', 0)} "
              "(pending until data-prepare fetches newer candles; matured != edge)")
    tp = report.get("tactical_probe") or {}
    if tp.get("thin_total"):
        print(f"  tactical probe (n<{tp.get('power_floor')}, research-only): thin={tp['thin_total']} "
              f"pos={tp.get('thin_positive')} rate={tp.get('overall_positive_rate')} "
              f"probe_families={tp.get('probe_families')} (thin_positive_skew != edge)")
    od = report.get("oi_diagnostic_15m") or {}
    if od.get("evaluated"):
        print(f"  oi 15m DIAGNOSTIC (delta_coarse, never edge): evaluated={od['evaluated']} "
              f"by_class={od.get('by_class')}")
    dc = report.get("discovery_cycle") or {}
    if dc.get("steps"):
        oks = sum(1 for s in dc["steps"] if s.get("status") == "ok")
        worked = (dc.get("what_worked_failed") or {}).get("worked") or []
        print(f"  discovery cycle (latest, research-only): steps_ok={oks}/{len(dc['steps'])} "
              f"held_oos={len(worked)} {worked[:1]}")
    tt = report.get("tactical_track") or {}
    if tt.get("total"):
        print(f"  tactical track (parallel lane, never paper-ready): leads={tt.get('tactical_leads')} "
              f"underpowered_pos={tt.get('underpowered_positive')} exit_problem={tt.get('exit_problem')} "
              f"no_event={tt.get('no_event')} known_bad={tt.get('known_bad')} "
              f"forward_watch={tt.get('forward_watch')} (NO_EVENT != bad; paper_ready_leak={tt.get('paper_ready_leak')})")
    mic = report.get("microstructure") or {}
    if mic:
        tape = mic.get("tape_sub_lane") or {}
        ob = mic.get("orderbook_sub_lane") or {}
        rec = ob.get("recorder") or {}
        print(f"  micro lane (Theme 40, research-only): tape={tape.get('events')}ev "
              f"bucket={tape.get('overall_bucket')} | orderbook events={ob.get('events')} "
              f"recorder={rec.get('readiness')} (tape-pressure no follow-through; walls pending data)")
    kb = report.get("knowledge_base") or {}
    if kb:
        if kb.get("skipped") == "fast_mode":
            print("  KNOWLEDGE BASE (research-only): skipped in --fast (run without --fast for audit rebuild)")
        else:
            print(f"  KNOWLEDGE BASE (research-only): known_bad={kb.get('known_bad')} revisit={kb.get('revisit')} "
                  f"survived_shadow={kb.get('survived_shadow')} tactical_probe={kb.get('tactical_probe')} "
                  f"recyclable={kb.get('rejected_recyclable')} confirmed_bad={kb.get('rejected_confirmed_bad')}")
    for b in report.get("backend", []):
        print(f"  backend eff={b['effective_backend'] or '?'} signal={b['signal_backend'] or '?'} "
              f"sim={b['simulation_backend'] or '?'} gpu_runs={b['gpu_runs']}/{b['runs']} "
              f"fallback={b.get('fallbacks') or '-'}")
    rows = report.get("by_group") or []
    if rows:
        print("  by group/family/decision:")
        for r in rows[:30]:
            print(f"    {r['asset_group'] or '-':16s} {r['family']:32s} {r['decision']:24s} "
                  f"n={r['n']} avg_net={r['avg_net']}")
    ready = report.get("ready_for_validation") or []
    total_groups = report.get("ready_total_groups")
    unval = report.get("unvalidated_count")
    extra = (f" | total eligible groups={total_groups} unvalidated_rows={unval}"
             if total_groups is not None else "")
    print(f"  ready for hard validation (deduped preview): {len(ready)}{extra}")
    for r in ready[:12]:
        print(f"    {r['symbol']} {r['family']} {r.get('timeframe') or ''} -> {r['validation_status']}")
    if not ready:
        print("    (none yet - all candidates rejected/observe; expected on weak/synthetic data)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--json", action="store_true", help="emit the raw report as JSON")
    ap.add_argument("--fast", action="store_true",
                    help="skip expensive derived research rebuilds; use for visible operator monitors")
    args = ap.parse_args()
    report = collect(default_db_path(Path(args.private_root)), fast=args.fast)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print(report)


if __name__ == "__main__":
    main()
