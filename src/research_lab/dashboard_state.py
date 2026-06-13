# -*- coding: utf-8 -*-
"""Read-only state model for the local strategy-lab dashboard."""

from __future__ import annotations

import csv
import datetime as dt
import glob
import json
from pathlib import Path
from typing import Any

from src.research_lab.candidate_registry import registry_path, registry_summary
from src.research_lab.data_prepare import read_prepare_report
from src.research_lab.event_microscope import plan_microscope
from src.research_lab.llm_review_sender import daily_cap, env_enabled
from src.research_lab.llm_proposals import load_llm_loop_config
from src.research_lab.llm_provider import today_usage
from src.research_lab.paths import one_minute_glob
from src.research_lab.prepare_workflow import load_prepare_workflow_config
from src.research_lab.research_cycle import cycle_summary, read_cycle_report
from src.research_lab.research_loop import loop_summary, read_loop_report
from src.research_lab.research_session import read_session_report, session_summary
from src.research_lab.obsidian_graph import count_notes
from src.research_lab.proposal_schema import QUEUED, VALIDATED
from src.research_lab.proposal_store import load_proposals, proposals_path, status_counts
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.runtime_policy import (
    cadence_path,
    evaluate_cadence,
    load_recent_starts,
    read_worker_status,
    worker_status_path,
)
from src.research_lab.state_db import dashboard_snapshot, default_db_path
from src.research_lab.timeframes import load_timeframe_profiles
from src.research_lab.universe import load_universe

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRIVATE_ROOT = Path.home() / "github_projects" / "trading-bot-research" / "strategy-lab"
SCOUT_BUDGET_LOG = ROOT / "logs" / "scout" / "llm_budget.jsonl"


def resolve_allowed_path(path: Path, allowed_roots: list[Path]) -> Path:
    """Resolve a path and ensure it stays inside one of the allowed roots."""
    resolved = path.resolve()
    for root in allowed_roots:
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    raise ValueError(f"path outside allowed roots: {path}")


def private_root_from_env(value: str | None = None) -> Path:
    raw = value or ""
    return Path(raw).expanduser() if raw.strip() else DEFAULT_PRIVATE_ROOT


def load_dashboard_state(private_root: Path = DEFAULT_PRIVATE_ROOT) -> dict[str, Any]:
    private_root = private_root.expanduser().resolve()
    completed_root = private_root / "experiments" / "completed"
    runs = load_completed_runs(completed_root, private_root)
    state_db = dashboard_snapshot(default_db_path(private_root))
    return {
        "schema": "strategy_lab_dashboard.v1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "private_root_label": "strategy-lab",
        "obsidian_graph_label": "strategy-lab/obsidian",
        "obsidian_vault_label": "strategy-lab/obsidian-vault",
        "state_db": state_db,
        "queue_capacity": queue_capacity((state_db or {}).get("queue_counts") or {}),
        "candidate_registry": registry_summary(registry_path(private_root)),
        "lab_config": load_lab_config_summary(private_root),
        "worker_status": read_worker_status(worker_status_path(private_root)),
        "llm_review": llm_review_status(private_root),
        "event_microscope": load_microscope_summary(private_root),
        "last_prepare_1m": load_data_prep_summary(private_root),
        "prepare_workflow": load_prepare_workflow_config().to_summary(),
        "last_cycle": cycle_summary(read_cycle_report(private_root)),
        "last_session": session_summary(read_session_report(private_root)),
        "last_loop": loop_summary(read_loop_report(private_root)),
        "llm_loop": load_llm_loop_summary(private_root),
        "proposals": load_proposal_summary(private_root),
        "obsidian_notes": count_notes(private_root),
        "next_run": next_run_hint(private_root),
        "runs": runs,
        "latest_run": runs[0] if runs else None,
        "totals": aggregate_runs(runs),
        "llm_cost": load_llm_cost_summary(SCOUT_BUDGET_LOG),
        "safety": {
            "bind_host": "127.0.0.1",
            "mode": "read_only",
            "shell_execution": False,
            "env_values_exposed": False,
            "live_trading": False,
        },
    }


def load_completed_runs(completed_root: Path, private_root: Path) -> list[dict[str, Any]]:
    completed_root = resolve_allowed_path(completed_root, [private_root])
    if not completed_root.exists():
        return []
    runs = []
    for run_dir in sorted([p for p in completed_root.iterdir() if p.is_dir()], reverse=True):
        try:
            run_dir = resolve_allowed_path(run_dir, [completed_root])
        except ValueError:
            continue
        runs.append(load_run(run_dir))
    return runs


def load_run(run_dir: Path) -> dict[str, Any]:
    metrics_path = run_dir / "metrics.json"
    candidates_path = run_dir / "candidates.csv"
    summary_path = run_dir / "summary.md"
    prompt_path = run_dir / "llm_review_prompt.md"
    payload = _load_json(metrics_path)
    reducer = _load_json(run_dir / "reducer_report.json")
    pack = _load_json(run_dir / "llm_review_pack.json")
    candidates = _load_candidates(candidates_path)
    counts: dict[str, int] = {}
    validation_counts: dict[str, int] = {}
    for row in candidates:
        decision = str(row.get("decision") or "UNKNOWN")
        counts[decision] = counts.get(decision, 0) + 1
        status = str(row.get("validation_status") or "UNKNOWN")
        validation_counts[status] = validation_counts.get(status, 0) + 1
    return {
        "run_id": run_dir.name,
        "experiment_id": payload.get("experiment_id") or _experiment_from_run_name(run_dir.name),
        "created_at": payload.get("created_at") or "",
        "artifact_label": f"experiments/completed/{run_dir.name}",
        "summary_label": f"experiments/completed/{run_dir.name}/summary.md" if summary_path.exists() else "",
        "llm_review_prompt_label": (
            f"experiments/completed/{run_dir.name}/llm_review_prompt.md" if prompt_path.exists() else ""
        ),
        "candidate_count": len(candidates),
        "counts": counts,
        "validation_counts": validation_counts,
        "reducer_verdicts": reducer.get("verdict_counts") or {},
        "entry_timing": pack.get("entry_timing_aggregate") or {},
        "top_candidates": candidates[:12],
    }


def _experiment_from_run_name(name: str) -> str:
    parts = name.split("_", 2)
    return parts[2] if len(parts) == 3 else name


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_candidates(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                out.append(row)
    except OSError:
        return []
    return sorted(out, key=lambda r: _candidate_sort_key(r), reverse=True)


def _candidate_sort_key(row: dict[str, Any]) -> tuple[int, int, float, float]:
    validation_rank = {"FORWARD_PAPER": 4, "REGIME_SPECIFIC": 3, "OBSERVE": 2, "REJECT": 1}
    decision_rank = {"PROMOTE_FOR_PRESSURE_TEST": 3, "OBSERVE": 2, "REJECT": 1}
    return (
        validation_rank.get(str(row.get("validation_status")), 0),
        decision_rank.get(str(row.get("decision")), 0),
        _float(row.get("avg_net_pct")),
        _float(row.get("test_avg_net_pct")),
    )


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def aggregate_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    validation_counts: dict[str, int] = {}
    for run in runs:
        for key, value in (run.get("counts") or {}).items():
            counts[key] = counts.get(key, 0) + int(value or 0)
        for key, value in (run.get("validation_counts") or {}).items():
            validation_counts[key] = validation_counts.get(key, 0) + int(value or 0)
    return {
        "run_count": len(runs),
        "candidate_count": sum(int(r.get("candidate_count") or 0) for r in runs),
        "decision_counts": counts,
        "validation_counts": validation_counts,
    }


def load_lab_config_summary(private_root: Path) -> dict[str, Any]:
    """Public-safe summary of the research-machine config (counts/mode only)."""
    summary: dict[str, Any] = {
        "universe_groups": 0,
        "universe_symbols": 0,
        "timeframe_profiles": [],
        "resource_mode": "unknown",
        "max_workers": 0,
        "allow_heavy_jobs": False,
        "allow_1m_jobs": "unknown",
        "proposal_specs": _count_proposal_specs(private_root),
    }
    try:
        universe = load_universe()
        summary["universe_groups"] = len(universe.groups)
        summary["universe_symbols"] = len(universe.all_symbols())
    except Exception as exc:  # config optional; never break the dashboard
        summary["universe_error"] = type(exc).__name__
    try:
        summary["timeframe_profiles"] = list(load_timeframe_profiles().names())
    except Exception as exc:
        summary["timeframe_error"] = type(exc).__name__
    try:
        policy = load_resource_policy()
        summary["resource_mode"] = policy.mode
        summary["max_workers"] = policy.max_workers
        summary["allow_heavy_jobs"] = policy.allow_heavy_jobs
        summary["allow_1m_jobs"] = policy.allow_1m_jobs
    except Exception as exc:
        summary["resource_error"] = type(exc).__name__
    return summary


def load_llm_loop_summary(private_root: Path) -> dict[str, Any]:
    """Config gate state (disabled/export_only/ready) + today's PRIVATE lab spend.

    The spend is the lab's own usage log (reports/llm_usage), never the scanner budget.
    No keys are read; today_usage returns counts/cost only.
    """
    summary = load_llm_loop_config().to_summary()
    summary["today_spend"] = today_usage(private_root)
    return summary


def load_proposal_summary(private_root: Path) -> dict[str, Any]:
    """Public-safe proposal summary: status counts + latest reasons (no payloads)."""
    proposals = load_proposals(proposals_path(private_root))
    counts = status_counts(proposals)
    latest = sorted(proposals, key=lambda p: p.created_at, reverse=True)[:5]
    return {
        "total": len(proposals),
        "by_status": counts,
        "validated_waiting": counts.get(VALIDATED, 0),
        "queued_from_proposals": counts.get(QUEUED, 0),
        "latest_reasons": [{"id": p.proposal_id, "status": p.status, "reasons": p.reason_codes} for p in latest],
        "last_created_at": latest[0].created_at if latest else "",
        "store_label": "strategy-lab/proposals/proposals.jsonl",
    }


def load_microscope_summary(private_root: Path = DEFAULT_PRIVATE_ROOT) -> dict[str, Any]:
    """Public-safe 1m event-microscope availability for a high-volatility group."""
    try:
        universe = load_universe()
        profiles = load_timeframe_profiles()
        policy = load_resource_policy()
        group = "l2_high_beta" if "l2_high_beta" in universe.groups else next(iter(universe.groups), "")
        symbols = list(universe.symbols_in(group)) if group else []
        plan = plan_microscope(
            symbols, data_glob=one_minute_glob(private_root), profiles=profiles, policy=policy,
        )
        summary = plan.to_summary()
        summary["scanned_group"] = group
        summary["data_glob_label"] = "strategy-lab/market_data/1m (prepared on demand; no downloader)"
        return summary
    except Exception as exc:  # config optional; never break the dashboard
        return {"error": type(exc).__name__, "enabled": False}


def load_data_prep_summary(private_root: Path = DEFAULT_PRIVATE_ROOT) -> dict[str, Any]:
    """Public-safe summary of the last 1m prepare run (counts/labels only)."""
    rep = read_prepare_report(private_root)
    if not rep:
        return {"available": False}
    return {
        "available": True,
        "provider": str(rep.get("provider", "null")),
        "provider_configured": bool(rep.get("provider_configured", False)),
        "mode": str(rep.get("mode", "dry_run")),
        "missing": int(rep.get("missing", 0) or 0),
        "would_download": int(rep.get("would_download", 0) or 0),
        "downloaded": int(rep.get("downloaded", 0) or 0),
        "files_written": len(rep.get("files_written", []) or []),
        "generated_at": str(rep.get("generated_at", "")),
    }


def queue_capacity(queue_counts: dict[str, Any]) -> dict[str, Any]:
    """Whether the queue is at the policy cap (a proxy for skipped_queue_full)."""
    try:
        max_size = int(load_resource_policy().max_queue_size)
    except Exception:
        return {}
    pending = int((queue_counts or {}).get("queued", 0) or 0)
    return {"max_queue_size": max_size, "queued": pending, "full": pending >= max_size}


def next_run_hint(private_root: Path) -> dict[str, Any]:
    """Whether the worker could run now, or the throttle reason and wait time."""
    try:
        policy = load_resource_policy()
        starts = load_recent_starts(cadence_path(private_root))
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        decision = evaluate_cadence(policy, starts, now)
        return {"allowed": decision.allowed, "wait_seconds": decision.wait_seconds, "reason": decision.reason}
    except Exception as exc:
        return {"error": type(exc).__name__}


def llm_review_status(private_root: Path | None = None) -> dict[str, Any]:
    """Whether sending review packs to an LLM is enabled (export is always allowed).

    Today the only sender is NullReviewSender (no provider), so even with the env
    flag a send is export-only. Surfaces the gate state and the last pack label.
    """
    enabled = env_enabled()
    cap = daily_cap()
    out: dict[str, Any] = {
        "enabled": enabled,
        "auto_execute": False,
        "auto_send": False,
        "provider_configured": False,  # only NullReviewSender is shipped today
        "daily_cap_present": cap is not None,
        "would_send": "blocked_no_provider" if enabled else "export_only_env_disabled",
        "pack_schema": "strategy_lab_registry_review_pack.v3",
        "queue_requires_apply": True,
        "note": "export-only; no automatic API call or spend; queue requires explicit apply",
        "last_pack_label": "",
        "last_send_status": "none",
    }
    if private_root is not None:
        review_dir = private_root / "reports" / "llm_review"
        packs = sorted(review_dir.glob("registry_review_pack_*.json"))
        if packs:
            out["last_pack_label"] = f"reports/llm_review/{packs[-1].name}"
        send_log = review_dir / "send_log.jsonl"
        if send_log.exists():
            lines = [ln for ln in send_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                try:
                    last = json.loads(lines[-1])
                    out["last_send_status"] = str(last.get("reason") or last.get("result_status") or "none")
                except json.JSONDecodeError:
                    pass
    return out


def _count_proposal_specs(private_root: Path) -> int:
    specs_dir = private_root / "proposals" / "specs"
    if not specs_dir.exists():
        return 0
    return len(glob.glob(str(specs_dir / "*.json")))


def load_llm_cost_summary(path: Path) -> dict[str, Any]:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    total_rub = 0.0
    today_rub = 0.0
    total_tokens = 0
    today_tokens = 0
    passes = 0
    latest = None
    if not path.exists():
        return {
            "log_label": "logs/scout/llm_budget.jsonl",
            "passes": 0,
            "total_rub": 0.0,
            "today_rub": 0.0,
            "total_tokens": 0,
            "today_tokens": 0,
        }
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        passes += 1
        latest = row
        rub = float(row.get("cost_rub") or 0.0)
        tokens = int(row.get("total_tokens") or 0)
        total_rub += rub
        total_tokens += tokens
        if str(row.get("ts") or "")[:10] == today:
            today_rub += rub
            today_tokens += tokens
    return {
        "log_label": "logs/scout/llm_budget.jsonl",
        "passes": passes,
        "total_rub": round(total_rub, 4),
        "today_rub": round(today_rub, 4),
        "total_tokens": total_tokens,
        "today_tokens": today_tokens,
        "latest": latest,
    }
