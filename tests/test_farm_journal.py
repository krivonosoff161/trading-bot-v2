# -*- coding: utf-8 -*-
"""Structured farm logs: cycle summary, task-transition audit hook, error log, rotation paths."""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import farm_journal as J  # noqa: E402
from src.research_lab.farm_tasks_db import FarmTasksDB  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_log_cycle_writes_summary(tmp_path):
    result = {"counters": {"runs_completed": 2, "classified": 2, "events_ingested": 0},
              "pivot": "advanced_lifecycle", "active_tasks": 5,
              "status": {"by_state": {"completed": 4, "queued": 1},
                         "blocked_reasons": {"NEEDS_MICRO_DATA": 1}, "deferred_reasons": {}}}
    J.log_cycle(tmp_path, ts=1234.5, mode="apply", result=result)
    rows = _read_jsonl(J.cycle_log_path(tmp_path))
    assert len(rows) == 1
    r = rows[0]
    assert r["schema"] == "farm_journal.v1" and r["pivot"] == "advanced_lifecycle"
    assert r["counters"] == {"runs_completed": 2, "classified": 2}  # zero counters dropped
    assert r["by_state"]["completed"] == 4 and r["blocked_reasons"]["NEEDS_MICRO_DATA"] == 1


def test_transition_hook_records_every_state_change(tmp_path):
    tasks = FarmTasksDB(tmp_path / "ft.sqlite")
    tasks.on_transition = J.make_transition_sink(tmp_path)
    tid, _ = tasks.enqueue_task(task_type="run_sweep", task_key="k", symbol="BTC", now=1.0)
    tasks.claim_next_task(now=2.0)            # queued -> running
    tasks.block_task(tid, "NEEDS_OI_DATA", now=3.0)  # running -> blocked
    tasks.requeue_task(tid, reason="gate_cleared", now=4.0)  # blocked -> queued
    tasks.complete_task(tid, reason="done", now=5.0)  # queued -> completed
    tasks.close()
    rows = _read_jsonl(J.transitions_path(tmp_path))
    states = [r["to_state"] for r in rows]
    assert states == ["running", "blocked", "queued", "completed"]
    blocked = next(r for r in rows if r["to_state"] == "blocked")
    assert blocked["reason"] == "NEEDS_OI_DATA" and blocked["task_key"] == "k"


def test_no_transition_hook_is_noop(tmp_path):
    tasks = FarmTasksDB(tmp_path / "ft.sqlite")  # on_transition stays None
    tid, _ = tasks.enqueue_task(task_type="run_sweep", task_key="k", now=1.0)
    tasks.complete_task(tid, now=2.0)
    tasks.close()
    assert not J.transitions_path(tmp_path).exists()  # nothing written without a sink


def test_log_error_and_paths(tmp_path):
    J.log_error(tmp_path, where="worker", error="RuntimeError: boom", ts=9.0, job_id=7)
    rows = _read_jsonl(J.errors_path(tmp_path))
    assert rows[0]["where"] == "worker" and rows[0]["job_id"] == 7
    paths = J.farm_log_paths(tmp_path)
    assert J.cycle_log_path(tmp_path) in paths and J.errors_path(tmp_path) in paths
    assert all(p.parent.name == "farm" for p in paths)
