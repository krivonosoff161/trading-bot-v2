"""Reconcile completed farm sweeps back to outcome-review retest decisions."""

from __future__ import annotations

import json
import sqlite3
import datetime as dt
from pathlib import Path
from typing import Any

from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.state_db import default_db_path

SCHEMA = "OutcomeRetestResult.v1"
SUMMARY_SCHEMA = "outcome_retest_results.v1"
MIN_EVIDENCE_TRADES = 5


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def _training_index(private_root: Path) -> dict[str, dict[str, str]]:
    path = Path(private_root) / "state" / "derived" / "paper_signal_training.jsonl"
    if not path.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        source_ref = str(row.get("training_row_id") or "")
        candidate_id = str(
            row.get("candidate_id") or row.get("setup_candidate_id") or ""
        )
        if source_ref:
            out[source_ref] = {
                "candidate_id": candidate_id,
                "symbol": str(row.get("symbol") or row.get("okx_inst_id") or ""),
                "timeframe": str(row.get("timeframe") or ""),
                "family": str(row.get("family") or row.get("setup_family") or ""),
            }
    return out


def _completed_tasks(private_root: Path) -> list[dict[str, Any]]:
    path = tasks_db_path(private_root)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT task_id, state, symbol, timeframe, family, source_event_id,
                      materialized_queue_job_id, last_result_ref, run_dir_label,
                      payload_json, updated_at
                 FROM tasks
                WHERE task_type='run_sweep' AND state='completed'
                  AND payload_json LIKE '%outcome_retest%'"""
        ).fetchall()
    finally:
        conn.close()
    out = [dict(row) for row in rows]
    queue_path = default_db_path(Path(private_root))
    if not queue_path.exists():
        return out
    queue = sqlite3.connect(str(queue_path))
    queue.row_factory = sqlite3.Row
    try:
        for row in out:
            if row.get("run_dir_label") or row.get("last_result_ref"):
                continue
            job_id = row.get("materialized_queue_job_id")
            if not job_id:
                continue
            resolved = queue.execute(
                "SELECT run_dir_label FROM queue WHERE job_id=?",
                (int(job_id),),
            ).fetchone()
            if resolved is not None and resolved["run_dir_label"]:
                row["run_dir_label"] = str(resolved["run_dir_label"])
    finally:
        queue.close()
    return out


def _best_result(payload: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in payload.get("results") or [] if isinstance(row, dict)]
    if not rows:
        return {}
    return max(
        rows,
        key=lambda row: (
            _float((row.get("metrics") or {}).get("avg_net_pct")),
            int((row.get("metrics") or {}).get("n_trades") or 0),
            str(row.get("run_id") or ""),
        ),
    )


def _verdict(*, n_trades: int, best_net: float) -> str:
    if n_trades < MIN_EVIDENCE_TRADES:
        return "insufficient_evidence"
    if best_net > 0:
        return "selection_only"
    return "no_selection_signal"


def _iso_from_epoch(value: Any) -> str:
    timestamp = _float(value)
    if timestamp <= 0:
        return ""
    return dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).isoformat()


def build_outcome_retest_results(private_root: Path) -> dict[str, Any]:
    private_root = Path(private_root)
    training_index = _training_index(private_root)
    latest: dict[str, dict[str, Any]] = {}
    unreadable = 0
    for task in _completed_tasks(private_root):
        try:
            task_payload = json.loads(task.get("payload_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        retest_id = str(
            task_payload.get("retest_id") or task.get("source_event_id") or ""
        )
        label = str(task.get("run_dir_label") or task.get("last_result_ref") or "")
        metrics_path = private_root / label / "metrics.json"
        try:
            metrics_payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            unreadable += 1
            continue
        context = (
            metrics_payload.get("event_context")
            if isinstance(metrics_payload.get("event_context"), dict)
            else task_payload
        )
        if not retest_id:
            retest_id = str(context.get("retest_id") or "")
        if not retest_id:
            continue
        best = _best_result(metrics_payload)
        metrics = (
            raw_metrics if isinstance(raw_metrics := best.get("metrics"), dict) else {}
        )
        n_trades = int(metrics.get("n_trades") or 0)
        best_net = _float(metrics.get("avg_net_pct"))
        source_ref = str(context.get("source_ref") or "")
        source_training = training_index.get(source_ref) or {}
        row = {
            "schema": SCHEMA,
            "retest_id": retest_id,
            "review_id": str(context.get("review_id") or ""),
            "source_ref": source_ref,
            "paper_signal_id": str(context.get("paper_signal_id") or ""),
            "source_candidate_id": str(
                context.get("source_candidate_id")
                or source_training.get("candidate_id")
                or ""
            ),
            "source_symbol": str(
                source_training.get("symbol")
                or best.get("symbol")
                or task.get("symbol")
                or ""
            ),
            "source_timeframe": str(
                source_training.get("timeframe")
                or metrics_payload.get("timeframe")
                or ""
            ),
            "source_family": str(
                source_training.get("family")
                or context.get("source_family")
                or best.get("family")
                or ""
            ),
            "symbol": str(best.get("symbol") or task.get("symbol") or ""),
            "timeframe": str(metrics_payload.get("timeframe") or ""),
            "family": str(best.get("family") or ""),
            "best_avg_net_pct": best_net,
            "best_n_trades": n_trades,
            "best_validation_status": str(best.get("validation_status") or ""),
            "verdict": _verdict(n_trades=n_trades, best_net=best_net),
            "evidence_stage": "selection",
            "required_evaluation": "untouched_out_of_sample",
            "untouched_evaluation_required": True,
            "selection_window_start": str(context.get("selection_window_start") or ""),
            "selection_window_end": str(context.get("selection_window_end") or ""),
            "evaluated_at": _iso_from_epoch(task.get("updated_at")),
            "comparison_kind": "within_sweep_selection_only_no_single_trade_baseline",
            "run_dir_label": label,
            "task_id": int(task.get("task_id") or 0),
            "completed_at": float(task.get("updated_at") or 0.0),
            "paper_only": True,
            "execution_allowed": False,
        }
        prior = latest.get(retest_id)
        if prior is None or row["completed_at"] >= prior["completed_at"]:
            latest[retest_id] = row

    items = sorted(
        latest.values(), key=lambda row: (row["completed_at"], row["retest_id"])
    )
    by_verdict: dict[str, int] = {}
    for row in items:
        verdict = str(row.get("verdict") or "")
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
    derived = private_root / "state" / "derived"
    out_jsonl = derived / "outcome_retest_results.jsonl"
    out_snapshot = derived / "outcome_retest_results.json"
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for row in items:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    summary = {
        "schema": SUMMARY_SCHEMA,
        "results": len(items),
        "by_verdict": dict(sorted(by_verdict.items())),
        "unreadable_completed_tasks": unreadable,
        "items": items,
        "paper_only": True,
        "execution_allowed": False,
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
    }
    out_snapshot.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
