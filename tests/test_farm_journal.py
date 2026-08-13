# -*- coding: utf-8 -*-
"""Structured farm logs: cycle summary, task-transition audit hook, error log, rotation paths."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_storage_maintain_reports_farm_logs_without_rotation(tmp_path, monkeypatch):
    # Apply-mode research cycles must not grant storage mutation authority.
    from scripts.strategy_lab import farm_loop
    from src.research_lab import storage_policy
    calls = {}

    def fake_maintain(paths=None, *, apply=False):
        calls["paths"] = list(paths or [])
        calls["apply"] = apply
        return {}

    def fake_bound(private_root, *, apply=False, **_kw):
        calls["bound_apply"] = apply
        return {}

    monkeypatch.setattr(storage_policy, "maintain", fake_maintain)
    monkeypatch.setattr(storage_policy, "bound_farm_artifacts", fake_bound)
    farm_loop._maybe_storage_maintain(tmp_path, apply=True)
    assert calls["apply"] is False and calls["bound_apply"] is False
    assert set(calls["paths"]) == set(J.farm_log_paths(tmp_path))


def test_storage_maintain_noop_in_dry_run(tmp_path, monkeypatch):
    from scripts.strategy_lab import farm_loop
    from src.research_lab import storage_policy

    def boom(*_a, **_k):
        raise AssertionError("storage maintenance must not run in dry-run")

    monkeypatch.setattr(storage_policy, "maintain", boom)
    monkeypatch.setattr(storage_policy, "bound_farm_artifacts", boom)
    farm_loop._maybe_storage_maintain(tmp_path, apply=False)  # no exception == no call


def test_farm_loop_can_run_paper_step_in_dry_run(tmp_path, monkeypatch):
    from scripts.strategy_lab import farm_loop

    monkeypatch.setattr(farm_loop, "_read_intake", lambda _limit, **_kwargs: [])
    monkeypatch.setattr(farm_loop, "_discovery",
                        lambda _args, _root, _apply: (None, {"status": "missing"}))

    def fake_cycle(*_args, **_kwargs):
        return {
            "counters": {},
            "pivot": "blocked:no_eligible_tasks",
            "active_tasks": 0,
            "status": {"by_state": {}, "blocked_reasons": {}, "deferred_reasons": {}},
            "errors": [],
        }

    def fake_paper(private_root, *, apply=False, limit=20, **_kwargs):
        return {
            "counters": {"cards": 0, "written": 0},
            "readiness": {"checked_cards": 1, "paper_forward_ready": 0,
                          "blocked_reasons": {"hard_status:NEEDS_MORE_DATA": 1}},
            "results": [],
        }

    monkeypatch.setattr(farm_loop, "run_coordinator_cycle", fake_cycle)
    monkeypatch.setattr("src.research_lab.paper_runtime.run_paper_cycle", fake_paper)
    tasks = FarmTasksDB(":memory:")
    args = SimpleNamespace(
        backend="cpu",
        data_days=None,
        max_plan_events=1,
        max_prepares=1,
        max_enrich=1,
        max_sweeps=1,
        run_worker=False,
        max_worker_jobs=1,
        night_mode=False,
        allow_public_output=False,
        run_validation=False,
        provider="synthetic",
        enrich_funding=False,
        enrich_oi=False,
        run_paper=True,
        max_paper_cards=5,
        sweep_tier="normal",
    )
    out = farm_loop._run_once(args, tasks, profiles={}, policy={}, private_root=tmp_path, apply=False)
    tasks.close()
    assert out["paper"]["counters"]["cards"] == 0
    assert out["paper"]["readiness"]["blocked_reasons"]["hard_status:NEEDS_MORE_DATA"] == 1


def test_log_error_and_paths(tmp_path):
    J.log_error(tmp_path, where="worker", error="RuntimeError: boom", ts=9.0, job_id=7)
    rows = _read_jsonl(J.errors_path(tmp_path))
    assert rows[0]["where"] == "worker" and rows[0]["job_id"] == 7
    paths = J.farm_log_paths(tmp_path)
    assert J.cycle_log_path(tmp_path) in paths and J.errors_path(tmp_path) in paths
    assert all(p.parent.name == "farm" for p in paths)
