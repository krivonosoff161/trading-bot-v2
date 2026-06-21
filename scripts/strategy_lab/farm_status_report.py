# -*- coding: utf-8 -*-
"""Read-only farm status report - what the calculation farm computed and what's next.

Reads strategy_lab.sqlite (runs / candidates / farm_results / runtime_stats) and
prints a clear operator summary: how much was computed, which assets/groups/timeframes,
the CPU/GPU backend split, the decision + validation breakdown, what was promoted vs
rejected and why, the queue state + age, flow coverage, and which candidates are ready
for hard validation (deduped). Migration-safe: it runs init_db so an old DB without the
v3 tables is upgraded non-destructively instead of crashing.

    python -m scripts.strategy_lab.farm_status_report
    python -m scripts.strategy_lab.farm_status_report --json

Never writes results, never touches the network, orders, .env, or private endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
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


def collect(db_path: Path) -> dict:
    if not db_path.exists():
        return {"exists": False, "db": str(db_path)}
    conn = _connect(db_path)
    init_db(conn)  # migration-safe: upgrade old DBs (adds v3 tables), non-destructive
    try:
        unique_table = "farm_results" if _has_rows(conn, "farm_results") else "candidates"
        report: dict = {
            "exists": True,
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
        try:
            from src.research_lab.setup_lifecycle import summarize_setup_lifecycle
            report["setup_lifecycle"] = summarize_setup_lifecycle(db_path.parent.parent)
        except Exception:  # noqa: BLE001 - optional derived view must not break status
            report["setup_lifecycle"] = {"available": False}
        try:  # Setup Outcome Memory: rejected-as-knowledge sub-views (derived, research-only)
            from src.research_lab.setup_outcome_memory import build_memory_index, summarize_memory
            report["outcome_memory"] = summarize_memory(build_memory_index(db_path.parent.parent))
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
        try:  # completion verdict + two-DB reconciliation (read-only, T2)
            from src.research_lab.farm_reconcile import completion_verdict, reconcile_dbs
            report["completion"] = completion_verdict(db_path.parent.parent)
            report["reconcile"] = reconcile_dbs(db_path.parent.parent)
        except Exception:  # noqa: BLE001 - optional reconcile must not break status
            report["completion"] = {"available": False}
            report["reconcile"] = {"available": False}
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
    print(f"FARM STATUS - schema v{report['schema_version']} | runs={t['runs']} candidates={t['candidates']} "
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
            line += (f" - eligible_now={rs.get('eligible_now', 0)} running={rs.get('running', 0)} "
                     f"deferred_future={rs.get('deferred_future', 0)} blocked={sum((rs.get('blocked') or {}).values())}"
                     " (loop stopped with claimable work; run farm_loop --apply --run-worker to drain)")
        print(line)
    rc = report.get("reconcile") or {}
    for name, info in (rc.get("orphans") or {}).items():
        print(f"  reconcile orphan: {name}={info.get('count')} owner={info.get('owner')} "
              f"{'-> ' + info.get('relabel') if info.get('relabel') else ''}")
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
    kb = report.get("knowledge_base") or {}
    if kb:
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
    args = ap.parse_args()
    report = collect(default_db_path(Path(args.private_root)))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print(report)


if __name__ == "__main__":
    main()
