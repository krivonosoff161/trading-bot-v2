# -*- coding: utf-8 -*-
"""Phase 0.2 — off-by-default stage visibility.

A bare apply/loop run with --run-worker/--run-validation/--run-paper off only QUEUES
work. That must be visible: a loud warning on stdout and a `stages` block in cycle_log,
so an operator never mistakes a partial loop for a working one.
"""
from __future__ import annotations

import json
import asyncio
import os
import threading
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.strategy_lab import farm_loop
from src.research_lab import farm_journal, product_progress
from src.research_lab.farm_coordinator import PriorityWorkerFatalError


def test_priority_worker_uses_independent_db_and_stops_cleanly(monkeypatch, tmp_path) -> None:
    seen = {"closed": False, "slots": 0, "statuses": [], "db_kwargs": None}

    class FakeTasks:
        on_transition = None

        def eligible_count(self):
            return 0

        def close(self):
            seen["closed"] = True

    stop = threading.Event()

    def fake_slot(*args, **kwargs):
        seen["slots"] += 1
        stop.set()
        return {"pivot": "idle", "active_tasks": 0, "counters": {}, "status": {}, "errors": []}

    def fake_tasks(_path, **kwargs):
        seen["db_kwargs"] = kwargs
        return FakeTasks()

    monkeypatch.setattr(farm_loop, "FarmTasksDB", fake_tasks)
    monkeypatch.setattr(farm_loop, "_run_priority_slot", fake_slot)
    monkeypatch.setattr(farm_loop, "_write_priority_checkpoint", lambda *args, **kwargs: tmp_path / "cp")
    monkeypatch.setattr(
        farm_loop, "_write_priority_worker_status",
        lambda root, **kwargs: seen["statuses"].append(kwargs["stage"]),
    )
    monkeypatch.setattr(farm_journal, "make_transition_sink", lambda root: None)

    farm_loop._priority_worker_loop(
        Namespace(stop_file="", busy_slot_seconds=0.1, idle_poll_seconds=0.1),
        {}, {}, tmp_path, stop,
    )

    assert seen["slots"] == 1
    assert seen["closed"] is True
    assert seen["db_kwargs"] == {"lease_seconds": farm_loop.TASK_CLAIM_LEASE_SECONDS}
    assert seen["statuses"] == ["running_slot", "idle", "stopped"]


@pytest.mark.parametrize(
    ("generation_published", "expected_wakeup"), ((1, True), (0, False))
)
def test_priority_worker_wakes_product_only_for_current_generation_publication(
    monkeypatch, tmp_path, generation_published, expected_wakeup
) -> None:
    seen = {"validation": 0, "slots": 0}
    product_wakeup = threading.Event()

    class FakeTasks:
        on_transition = None

        def eligible_count(self, *, task_types=None):
            return 1 if task_types == ("export_validation",) else 0

        def close(self):
            pass

    stop = threading.Event()

    def fake_validation(*_args, **kwargs):
        seen["validation"] += 1
        assert kwargs["status_target"] == "priority_worker"
        return {
            "validated": 2,
            "exported": 2,
            "generation_published": generation_published,
        }

    def fake_slot(*_args, **_kwargs):
        seen["slots"] += 1
        stop.set()
        return {
            "pivot": "idle",
            "active_tasks": 0,
            "counters": {},
            "status": {},
            "errors": [],
        }

    monkeypatch.setattr(farm_loop, "FarmTasksDB", lambda *_args, **_kwargs: FakeTasks())
    monkeypatch.setattr(farm_loop, "_run_validation_maintenance", fake_validation)
    monkeypatch.setattr(farm_loop, "_run_priority_slot", fake_slot)
    monkeypatch.setattr(farm_loop, "_write_priority_checkpoint", lambda *_a, **_k: tmp_path / "cp")
    monkeypatch.setattr(farm_loop, "_write_priority_worker_status", lambda *_a, **_k: None)
    monkeypatch.setattr(farm_journal, "make_transition_sink", lambda _root: None)

    farm_loop._priority_worker_loop(
        Namespace(
            stop_file="",
            run_validation=True,
            busy_slot_seconds=0.1,
            idle_poll_seconds=0.1,
            product_cycle_wakeup_event=product_wakeup,
        ),
        {},
        {},
        tmp_path,
        stop,
    )

    assert seen == {"validation": 1, "slots": 1}
    assert product_wakeup.is_set() is expected_wakeup


def test_current_generation_wakeup_reenters_only_a_waiting_v2_pass() -> None:
    wake = threading.Event()
    wake.set()
    args = Namespace(
        paper_evidence_v2_required=True,
        product_cycle_wakeup_event=wake,
    )

    assert farm_loop._yield_waiting_v2_generation_to_current_publication(
        args,
        {
            "paper_generation_v2": {
                "state": "waiting_validation_generation",
                "paper_only": True,
                "execution_allowed": False,
            }
        },
    ) is True
    assert farm_loop._yield_waiting_v2_generation_to_current_publication(
        args,
        {"paper_generation_v2": {"state": "ready"}},
    ) is False
    assert farm_loop._yield_waiting_v2_generation_to_current_publication(
        Namespace(
            paper_evidence_v2_required=False,
            product_cycle_wakeup_event=wake,
        ),
        {"paper_generation_v2": {"state": "waiting_validation_generation"}},
    ) is False


def test_claim_failure_signal_stops_worker_and_interrupts_foreground(tmp_path) -> None:
    stop = threading.Event()
    interrupted = []
    signal = farm_loop._TaskClaimFailureSignal(
        tmp_path, stop, interrupt_main=lambda: interrupted.append(True),
    )

    signal.notify(
        RuntimeError("claim lost"),
        {
            "task_id": 7,
            "owner_id": "must-not-leak",
            "task_fencing_token": 3,
            "process_fencing_token": 8,
            "last_progress_stage": "grid_validation:1024/157464",
            "last_progress_age_seconds": 301.0,
            "failure": "TaskClaimProgressStalled",
        },
    )
    signal.notify(RuntimeError("duplicate"), {})

    assert stop.is_set()
    assert interrupted == [True]
    status = json.loads(
        (tmp_path / "state" / "farm_priority_worker_status.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["stage"] == "claim_failed"
    assert status["execution_allowed"] is False
    assert "owner_id" not in json.dumps(status)
    with pytest.raises(RuntimeError, match="priority task claim heartbeat failed"):
        signal.raise_if_failed()


def test_claim_failure_interrupts_even_when_status_write_fails(monkeypatch, tmp_path) -> None:
    stop = threading.Event()
    interrupted = []
    signal = farm_loop._TaskClaimFailureSignal(
        tmp_path, stop, interrupt_main=lambda: interrupted.append(True),
    )
    monkeypatch.setattr(
        farm_loop,
        "_write_priority_worker_status",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("status unavailable")),
    )

    with pytest.raises(OSError, match="status unavailable"):
        signal.notify(RuntimeError("claim lost"), {"task_id": 7})

    assert stop.is_set()
    assert interrupted == [True]


def test_compute_worker_lifecycle_failure_interrupts_foreground(
    monkeypatch,
    tmp_path,
) -> None:
    stop = threading.Event()
    interrupted = []
    signal = farm_loop._TaskClaimFailureSignal(
        tmp_path,
        stop,
        interrupt_main=lambda: interrupted.append(True),
    )
    args = SimpleNamespace(
        stop_file="",
        busy_slot_seconds=0.1,
        idle_poll_seconds=0.1,
        canonical_owner_id=None,
        task_claim_failure_signal=signal,
    )

    class FakeTasks:
        on_transition = None

        def close(self):
            pass

    monkeypatch.setattr(farm_loop, "FarmTasksDB", lambda *_args, **_kwargs: FakeTasks())
    monkeypatch.setattr(
        farm_loop,
        "_run_priority_slot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PriorityWorkerFatalError("synthetic process lease failure")
        ),
    )
    monkeypatch.setattr(
        farm_journal,
        "make_transition_sink",
        lambda _root: None,
    )

    farm_loop._priority_worker_loop(args, {}, {}, tmp_path, stop)

    status = json.loads(
        (tmp_path / "state" / "farm_priority_worker_status.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["stage"] == "worker_failed"
    assert status["details"]["failure_kind"] == "compute_worker_lifecycle"
    assert interrupted == [True]
    with pytest.raises(RuntimeError, match="priority compute worker failed closed"):
        signal.raise_if_failed()


def _args(**over) -> Namespace:
    base = dict(run_worker=False, run_validation=False, run_paper=False,
                enrich_funding=False, enrich_oi=False, run_journal_export=False)
    base.update(over)
    return Namespace(**base)


def test_loop_status_transient_windows_contention_recovers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "farm_loop_status.json"
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(path: Path, destination: Path):
        nonlocal attempts
        if destination == target:
            attempts += 1
            if attempts < 3:
                raise PermissionError(5, "synthetic sharing violation")
        return original_replace(path, destination)

    farm_loop._LOOP_STATUS_PUBLISHERS.clear()
    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(farm_loop.time, "sleep", lambda _seconds: None)

    written = farm_loop._write_loop_status(
        tmp_path,
        stage="paper_signals",
        apply=True,
        loop=True,
        cycle_started_at=1.0,
    )

    assert written is True
    assert attempts == 3
    assert json.loads(target.read_text(encoding="utf-8"))["stage"] == "paper_signals"
    assert farm_loop._loop_status_publisher(tmp_path).consecutive_failures == 0


def test_production_farm_milestone_survives_product_progress_contention(
    monkeypatch,
    tmp_path: Path,
) -> None:
    progress_target = (
        tmp_path / "state" / "product_progress" / "farm_progress.json"
    )
    real_replace = os.replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        if Path(target) == progress_target:
            attempts += 1
            if attempts <= 2:
                exc = PermissionError("synthetic product-progress sharing denial")
                exc.winerror = 5
                raise exc
        real_replace(source, target)

    farm_loop._LOOP_STATUS_PUBLISHERS.clear()
    monkeypatch.setattr(product_progress.os, "replace", flaky_replace)
    monkeypatch.setattr(product_progress.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(farm_loop.time, "time", lambda: 100.0)

    assert farm_loop._write_loop_status(
        tmp_path,
        stage="paper_generation_v2",
        apply=True,
        loop=True,
        cycle_started_at=90.0,
        details={"milestone": "generation_promoted", "completed": 1, "total": 1},
    )

    checkpoint = json.loads(progress_target.read_text(encoding="utf-8"))
    assert attempts == 3
    assert checkpoint["status"] == "progress"
    assert checkpoint["metrics"] == {
        "completed": 1,
        "milestone": "generation_promoted",
        "stage": "paper_generation_v2",
        "total": 1,
    }
    assert not list(progress_target.parent.glob(".*.tmp"))


def test_durable_farm_milestones_advance_process_lease_progress(
    monkeypatch,
    tmp_path: Path,
) -> None:
    stages: list[str] = []
    supervisor = SimpleNamespace(record_progress=stages.append)
    ownership_path = tmp_path / "state" / "ownership.sqlite"
    farm_loop._LOOP_STATUS_PUBLISHERS.clear()
    farm_loop._PROCESS_LEASE_SUPERVISORS[ownership_path] = supervisor
    monkeypatch.setattr(farm_loop.time, "time", lambda: 100.0)
    try:
        assert farm_loop._write_loop_status(
            tmp_path,
            stage="setup_outcome_memory_refresh",
            apply=True,
            loop=True,
            cycle_started_at=90.0,
        )
        farm_loop._write_priority_checkpoint(
            tmp_path,
            {"pivot": "idle", "active_tasks": 0},
            sequence=1,
        )
        farm_loop._write_priority_worker_status(
            tmp_path,
            stage="idle",
            started_at=90.0,
        )
    finally:
        farm_loop._PROCESS_LEASE_SUPERVISORS.pop(ownership_path, None)

    assert stages == [
        "setup_outcome_memory_refresh",
        "priority_worker:checkpoint",
        "priority_worker:idle",
    ]


def test_failed_status_publication_does_not_claim_process_lease_progress(
    monkeypatch,
    tmp_path: Path,
) -> None:
    stages: list[str] = []
    supervisor = SimpleNamespace(record_progress=stages.append)
    ownership_path = tmp_path / "state" / "ownership.sqlite"
    farm_loop._PROCESS_LEASE_SUPERVISORS[ownership_path] = supervisor
    monkeypatch.setattr(
        farm_loop,
        "_loop_status_publisher",
        lambda _root: SimpleNamespace(publish=lambda _payload: False),
    )
    try:
        assert (
            farm_loop._write_loop_status(
                tmp_path,
                stage="setup_outcome_memory_refresh",
                apply=True,
                loop=True,
                cycle_started_at=90.0,
            )
            is False
        )
    finally:
        farm_loop._PROCESS_LEASE_SUPERVISORS.pop(ownership_path, None)

    assert stages == []


def test_validation_maintenance_binds_real_milestones_to_process_lease(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import validation_orchestrator

    stages: list[str] = []
    supervisor = SimpleNamespace(record_progress=stages.append)
    ownership_path = tmp_path / "state" / "ownership.sqlite"
    farm_loop._LOOP_STATUS_PUBLISHERS.clear()
    farm_loop._PROCESS_LEASE_SUPERVISORS[ownership_path] = supervisor
    seen: dict[str, object] = {}

    def fake_run_due(*args, **kwargs):
        seen["limit"] = kwargs["limit"]
        kwargs["check_active"]()
        kwargs["progress"]("requests_exported", 1, 1)
        kwargs["progress"]("validations_completed", 1, 1)
        return {"validated": 1}

    monkeypatch.setattr(validation_orchestrator, "run_due_validations", fake_run_due)
    monkeypatch.setattr(farm_loop.time, "time", lambda: 100.0)
    try:
        result = farm_loop._run_validation_maintenance(
            Namespace(
                stop_file="",
                task_claim_failure_signal=None,
                max_validations=7,
                validation_backlog_high_water=11,
                validation_backlog_slo_seconds=22.0,
            ),
            object(),
            tmp_path,
            apply=True,
            loop=True,
            cycle_started_at=90.0,
        )
    finally:
        farm_loop._PROCESS_LEASE_SUPERVISORS.pop(ownership_path, None)

    status = json.loads(
        (tmp_path / "state" / "farm_loop_status.json").read_text(encoding="utf-8")
    )
    assert result["validated"] == 1
    assert result["backlog_after"]["active"] == 0
    assert seen["limit"] == 7
    assert status["details"]["milestone"] == "validations_completed"
    assert status["details"]["completed"] == 1
    assert status["details"]["total"] == 1
    assert status["details"]["max_validations"] == 7
    assert status["details"]["backlog_high_water"] == 11
    assert status["details"]["backlog_slo_seconds"] == 22.0
    assert status["details"]["backlog"]["active"] == 0
    assert stages == [
        "validation_maintenance:starting",
        "validation_maintenance:requests_exported",
        "validation_maintenance:validations_completed",
    ]


def test_priority_validation_worker_publishes_only_completed_product_milestones(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import validation_orchestrator

    clock = [100.0]

    def fake_run_due(*_args, **kwargs):
        clock[0] = 101.0
        kwargs["progress"]("requests_exported", 1, 2)
        clock[0] = 102.0
        kwargs["progress"]("validations_completed", 2, 2)
        return {"validated": 2}

    monkeypatch.setattr(validation_orchestrator, "run_due_validations", fake_run_due)
    monkeypatch.setattr(farm_loop.time, "time", lambda: clock[0])

    farm_loop._run_validation_maintenance(
        Namespace(stop_file="", task_claim_failure_signal=None),
        object(),
        tmp_path,
        apply=True,
        loop=True,
        cycle_started_at=100.0,
        status_target="priority_worker",
    )

    progress = json.loads(
        (
            tmp_path
            / "state"
            / "product_progress"
            / "validation_progress.json"
        ).read_text(encoding="utf-8")
    )
    assert progress["status"] == "progress"
    assert progress["completed_at"] == 102.0
    assert progress["metrics"] == {
        "completed": 2,
        "milestone": "validations_completed",
        "stage": "validation_maintenance",
        "total": 2,
        "validation_active": 0,
        "validation_arrival_rate_per_hour": 0.0,
        "validation_eligible": 0,
        "validation_fresh_eligible": 0,
        "validation_fresh_oldest_age_seconds": 0.0,
        "validation_net_drain_rate_per_hour": 0.0,
        "validation_service_rate_per_hour": 0.0,
    }


def test_priority_validation_starting_marker_is_not_product_progress(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import validation_orchestrator

    monkeypatch.setattr(
        validation_orchestrator,
        "run_due_validations",
        lambda *_args, **_kwargs: {"validated": 0},
    )
    monkeypatch.setattr(farm_loop.time, "time", lambda: 100.0)

    farm_loop._run_validation_maintenance(
        Namespace(stop_file="", task_claim_failure_signal=None),
        object(),
        tmp_path,
        apply=True,
        loop=True,
        cycle_started_at=100.0,
        status_target="priority_worker",
    )

    assert not (
        tmp_path / "state" / "product_progress" / "validation_progress.json"
    ).exists()


def test_priority_validation_progress_binds_bounded_pre_marker_successor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import validation_generation, validation_orchestrator

    clock = [100.0]

    def fake_run_due(*_args, **kwargs):
        clock[0] = 101.0
        kwargs["progress"]("validation_request_loaded", 1, 2)
        return {"validated": 0}

    monkeypatch.setattr(validation_orchestrator, "run_due_validations", fake_run_due)
    monkeypatch.setattr(
        validation_generation,
        "current_generation_manifest_status",
        lambda _root: "code_stale",
    )
    monkeypatch.setattr(
        validation_generation,
        "pending_generation_manifest_status",
        lambda _root: "absent",
    )
    monkeypatch.setattr(
        validation_generation,
        "validation_producer_code_digest",
        lambda: "a" * 64,
    )
    monkeypatch.setattr(farm_loop.time, "time", lambda: clock[0])
    args = Namespace(stop_file="", task_claim_failure_signal=None)

    farm_loop._run_validation_maintenance(
        args,
        object(),
        tmp_path,
        apply=True,
        loop=True,
        cycle_started_at=100.0,
        status_target="priority_worker",
    )

    progress = json.loads(
        (
            tmp_path
            / "state"
            / "product_progress"
            / "validation_progress.json"
        ).read_text(encoding="utf-8")
    )
    assert progress["metrics"]["successor_build_phase"] == "pre_marker"
    assert progress["metrics"]["successor_build_started_at"] == 100.0
    assert progress["metrics"]["successor_code_digest"] == "a" * 64
    assert progress["metrics"]["successor_marker_code_status"] == "absent"

    clock[0] = 120.0
    farm_loop._run_validation_maintenance(
        args,
        object(),
        tmp_path,
        apply=True,
        loop=True,
        cycle_started_at=120.0,
        status_target="priority_worker",
    )
    progress = json.loads(
        (
            tmp_path
            / "state"
            / "product_progress"
            / "validation_progress.json"
        ).read_text(encoding="utf-8")
    )
    assert progress["metrics"]["successor_build_started_at"] == 100.0


def test_priority_validation_progress_marks_current_publication_before_farm_checkpoint(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import validation_generation, validation_orchestrator

    clock = [100.0]
    generation_status = ["code_stale"]

    def fake_run_due(*_args, **kwargs):
        clock[0] = 101.0
        kwargs["progress"]("validation_request_loaded", 1, 2)
        generation_status[0] = "code_current"
        clock[0] = 102.0
        kwargs["progress"]("empty_generation_published", 1, 1)
        return {"validated": 0}

    monkeypatch.setattr(validation_orchestrator, "run_due_validations", fake_run_due)
    monkeypatch.setattr(
        validation_generation,
        "current_generation_manifest_status",
        lambda _root: generation_status[0],
    )
    monkeypatch.setattr(
        validation_generation,
        "pending_generation_manifest_status",
        lambda _root: "absent",
    )
    monkeypatch.setattr(
        validation_generation,
        "validation_producer_code_digest",
        lambda: "a" * 64,
    )
    monkeypatch.setattr(farm_loop.time, "time", lambda: clock[0])

    farm_loop._run_validation_maintenance(
        Namespace(stop_file="", task_claim_failure_signal=None),
        object(),
        tmp_path,
        apply=True,
        loop=True,
        cycle_started_at=100.0,
        status_target="priority_worker",
    )

    progress = json.loads(
        (
            tmp_path
            / "state"
            / "product_progress"
            / "validation_progress.json"
        ).read_text(encoding="utf-8")
    )
    assert progress["metrics"]["milestone"] == "empty_generation_published"
    assert progress["metrics"]["successor_build_phase"] == "current_published"
    assert progress["metrics"]["successor_current_code_status"] == "code_current"
    assert progress["metrics"]["successor_marker_code_status"] == "absent"


def test_paper_runtime_binds_completed_chunks_to_process_lease(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import paper_runtime

    stages: list[str] = []
    supervisor = SimpleNamespace(record_progress=stages.append)
    ownership_path = tmp_path / "state" / "ownership.sqlite"
    farm_loop._LOOP_STATUS_PUBLISHERS.clear()
    farm_loop._PROCESS_LEASE_SUPERVISORS[ownership_path] = supervisor

    def fake_cycle(*_args, **kwargs):
        kwargs["progress"]("generation_candidate_verified", 1, 1)
        kwargs["progress"]("signal_history_chunk_completed", 250, 250)
        return {"counters": {"cards": 1}, "readiness": {}, "results": []}

    monkeypatch.setattr(paper_runtime, "run_paper_cycle", fake_cycle)
    monkeypatch.setattr(farm_loop.time, "time", lambda: 1_000.0)
    try:
        result = farm_loop._run_paper_runtime(
            Namespace(
                stop_file="",
                task_claim_failure_signal=None,
                max_paper_cards=1,
            ),
            tmp_path,
            apply=True,
            loop=True,
            cycle_started_at=1.0,
        )
    finally:
        farm_loop._PROCESS_LEASE_SUPERVISORS.pop(ownership_path, None)

    assert result["counters"]["cards"] == 1
    assert stages == [
        "paper_runtime:generation_candidate_verified",
        "paper_runtime:signal_history_chunk_completed",
    ]


def test_paper_runtime_owner_failure_is_visible_before_next_chunk(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import paper_runtime

    class FailureSignal:
        checks = 0

        def raise_if_failed(self):
            self.checks += 1
            if self.checks >= 3:
                raise RuntimeError("paper owner lost")

    def fake_cycle(*_args, **kwargs):
        kwargs["progress"]("generation_candidate_verified", 1, 2)
        raise AssertionError("owner failure must interrupt the foreground callback")

    monkeypatch.setattr(paper_runtime, "run_paper_cycle", fake_cycle)

    with pytest.raises(RuntimeError, match="paper owner lost"):
        farm_loop._run_paper_runtime(
            Namespace(
                stop_file="",
                task_claim_failure_signal=FailureSignal(),
                max_paper_cards=1,
            ),
            tmp_path,
            apply=True,
            loop=True,
            cycle_started_at=1.0,
        )


def test_validation_maintenance_propagates_owner_failure_before_work(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import validation_orchestrator

    monkeypatch.setattr(
        validation_orchestrator,
        "run_due_validations",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("validation must not start after owner failure")
        ),
    )

    class FailedSignal:
        def raise_if_failed(self) -> None:
            raise RuntimeError("canonical process ownership heartbeat failed")

    with pytest.raises(
        RuntimeError, match="canonical process ownership heartbeat failed"
    ):
        farm_loop._run_validation_maintenance(
            Namespace(stop_file="", task_claim_failure_signal=FailedSignal()),
            object(),
            tmp_path,
            apply=True,
            loop=True,
            cycle_started_at=90.0,
        )


def test_validation_maintenance_over_900_logical_seconds_keeps_real_progress(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import validation_orchestrator

    clock = [0.0]
    progress_times: list[float] = []

    class TrackingSupervisor:
        def record_progress(self, _stage: str) -> None:
            progress_times.append(clock[0])

    ownership_path = tmp_path / "state" / "ownership.sqlite"
    farm_loop._LOOP_STATUS_PUBLISHERS.clear()
    farm_loop._PROCESS_LEASE_SUPERVISORS[ownership_path] = TrackingSupervisor()

    def fake_run_due(*_args, **kwargs):
        for completed in range(1, 11):
            clock[0] += 120.0
            kwargs["progress"]("validation_chunk_completed", completed, 10)
        return {"validated": 1}

    monkeypatch.setattr(validation_orchestrator, "run_due_validations", fake_run_due)
    monkeypatch.setattr(farm_loop.time, "time", lambda: clock[0])
    try:
        result = farm_loop._run_validation_maintenance(
            Namespace(stop_file="", task_claim_failure_signal=None),
            object(),
            tmp_path,
            apply=True,
            loop=True,
            cycle_started_at=0.0,
        )
    finally:
        farm_loop._PROCESS_LEASE_SUPERVISORS.pop(ownership_path, None)

    assert result["validated"] == 1
    assert result["backlog_after"]["active"] == 0
    assert clock[0] == 1200.0
    assert len(progress_times) == 11  # initial durable stage plus ten real chunks
    assert max(
        later - earlier for earlier, later in zip(progress_times, progress_times[1:])
    ) == 120.0


def test_loop_status_persistent_contention_keeps_last_good_and_requests_safe_stop(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    target = tmp_path / "state" / "farm_loop_status.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"stage":"last_good"}\n', encoding="utf-8")
    original_replace = Path.replace

    def locked_replace(path: Path, destination: Path):
        if destination == target:
            raise PermissionError(5, "synthetic sharing violation")
        return original_replace(path, destination)

    farm_loop._LOOP_STATUS_PUBLISHERS.clear()
    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr(farm_loop.time, "sleep", lambda _seconds: None)
    publisher = farm_loop._loop_status_publisher(tmp_path)

    assert publisher.publish({"stage": "new"}, now_monotonic=100.0) is False
    assert json.loads(target.read_text(encoding="utf-8")) == {"stage": "last_good"}
    assert not farm_loop._status_publication_requires_stop(
        tmp_path,
        max_outage_seconds=120.0,
        now_monotonic=220.0,
    )
    assert farm_loop._status_publication_requires_stop(
        tmp_path,
        max_outage_seconds=120.0,
        now_monotonic=220.001,
    )
    monkeypatch.setattr(farm_loop.time, "monotonic", lambda: 220.001)
    assert farm_loop._leave_for_status_publication_outage(
        tmp_path,
        max_outage_seconds=120.0,
    )
    assert "completed-cycle boundary without restart" in capsys.readouterr().out
    assert list(target.parent.glob("farm_loop_status.json.*.tmp")) == []


def test_loop_status_non_transient_storage_error_remains_fatal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "farm_loop_status.json"
    original_replace = Path.replace

    def failed_replace(path: Path, destination: Path):
        if destination == target:
            raise OSError(28, "synthetic no space")
        return original_replace(path, destination)

    farm_loop._LOOP_STATUS_PUBLISHERS.clear()
    monkeypatch.setattr(Path, "replace", failed_replace)
    publisher = farm_loop._loop_status_publisher(tmp_path)
    target.parent.mkdir(parents=True)

    with pytest.raises(OSError, match="synthetic no space"):
        publisher.publish({"stage": "new"}, now_monotonic=100.0)


class TestStageStatus:
    def test_priority_checkpoint_is_resumable_and_paper_only(self, tmp_path: Path) -> None:
        target = farm_loop._write_priority_checkpoint(
            tmp_path,
            {
                "pivot": "advanced_lifecycle",
                "active_tasks": 3,
                "status": {"by_state": {"queued": 2, "running": 1}},
                "counters": {"runs_completed": 1},
                "errors": [],
            },
            sequence=7,
        )
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["sequence"] == 7
        assert payload["resume_mode"] == "requeue_atomic_slot_from_durable_ledgers"
        assert payload["paper_only"] is True
        assert payload["execution_allowed"] is False

    def test_slot_did_work_uses_real_transition_counters(self) -> None:
        assert farm_loop._slot_did_work({"counters": {"runs_completed": 1}}) is True
        assert farm_loop._slot_did_work({"counters": {"runs_completed": 0}}) is False

    def test_pid_probe_treats_windows_system_error_as_dead(self, monkeypatch) -> None:
        def bad_kill(_pid: int, _sig: int) -> None:
            raise SystemError("<built-in function kill> returned a result with an exception set")

        monkeypatch.setattr(farm_loop.os, "kill", bad_kill)

        assert farm_loop._pid_is_alive(123456789) is False

    def test_critical_flags_marked(self) -> None:
        s = farm_loop._stage_status(_args(), apply=True)
        for name in ("worker", "validation", "paper"):
            assert s[name]["critical"] is True
        for name in ("enrich_funding", "enrich_oi"):
            assert s[name]["critical"] is False

    def test_skipped_reason_present_when_off(self) -> None:
        s = farm_loop._stage_status(_args(run_worker=False), apply=True)
        assert s["worker"]["enabled"] is False
        assert "--run-worker" in s["worker"]["skipped_reason"]

    def test_no_reason_when_on(self) -> None:
        s = farm_loop._stage_status(_args(run_validation=True), apply=True)
        assert s["validation"]["enabled"] is True
        assert s["validation"]["skipped_reason"] is None

    def test_journal_export_is_non_critical(self) -> None:
        s = farm_loop._stage_status(_args(run_journal_export=False), apply=True)

        assert s["journal_export"]["enabled"] is False
        assert s["journal_export"]["critical"] is False
        assert "--run-journal-export" in s["journal_export"]["skipped_reason"]


class TestPrintWarning:
    def test_warns_when_critical_off_in_apply(self, capsys) -> None:
        farm_loop._print_stages(farm_loop._stage_status(_args(), apply=True), apply=True)
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "worker" in out and "validation" in out and "paper" in out

    def test_no_warning_when_all_critical_on(self, capsys) -> None:
        s = farm_loop._stage_status(
            _args(run_worker=True, run_validation=True, run_paper=True), apply=True)
        farm_loop._print_stages(s, apply=True)
        out = capsys.readouterr().out
        assert "WARNING" not in out

    def test_no_warning_in_dry_run(self, capsys) -> None:
        farm_loop._print_stages(farm_loop._stage_status(_args(), apply=False), apply=False)
        out = capsys.readouterr().out
        assert "WARNING" not in out

    def test_cycle_print_separates_delivery_cards_from_messages(self, capsys) -> None:
        farm_loop._print_cycle({
            "pivot": "work_available",
            "active_tasks": 1,
            "counters": {},
            "status": {},
            "paper_telegram_delivery": {
                "eligible_cards": 3,
                "target_recipients": 2,
                "sent_messages": 4,
                "sent_cards": 2,
                "duplicate_messages": 2,
                "duplicate_cards": 1,
                "skipped_messages": 0,
                "error_messages": 0,
                "dry_run": False,
                "sends_network": True,
            },
        })

        out = capsys.readouterr().out

        assert "eligible_cards=3" in out
        assert "targets=2" in out
        assert "sent_messages=4" in out
        assert "sent_cards=2" in out
        assert "duplicate_messages=2" in out
        assert "duplicate_cards=1" in out

    def test_run_paper_refreshes_main_paper_chain_without_signal_lane(self, tmp_path, monkeypatch) -> None:
        seen: dict[str, object] = {}

        monkeypatch.setattr(farm_loop, "_providers", lambda args, apply: ("provider", None, None))
        monkeypatch.setattr(farm_loop, "_discovery", lambda args, root, apply: (None, {"status": "test"}))
        monkeypatch.setattr(
            farm_loop,
            "run_coordinator_cycle",
            lambda *args, **kwargs: {"pivot": "idle", "active_tasks": 0, "counters": {}, "status": {}},
        )

        from src.research_lab import paper_runtime

        monkeypatch.setattr(
            paper_runtime,
            "run_paper_cycle",
            lambda root, apply, limit, **kwargs: {"counters": {"cards": 1}, "readiness": {}, "results": []},
        )

        def fake_refresh(args, private_root, *, tasks, apply, loop, cycle_started_at, out, provider=None):
            seen["called"] = True
            seen["provider"] = provider
            seen["tasks"] = tasks
            out["main_paper_bridge"] = {"instructions": 1}

        monkeypatch.setattr(farm_loop, "_run_main_paper_derived_chain", fake_refresh)

        args = Namespace(
            max_plan_events=0,
            max_prepares=0,
            max_enrich=0,
            max_sweeps=0,
            run_worker=False,
            max_worker_jobs=0,
            max_validations=0,
            night_mode=False,
            allow_public_output=False,
            run_validation=False,
            no_followups=True,
            max_followups=0,
            sweep_tier="smoke",
            run_paper=True,
            max_paper_cards=1,
            true_forward_max_candidates=0,
            run_paper_signals=False,
            provider="synthetic",
            backend="cpu",
            data_days=None,
            enrich_funding=False,
            enrich_oi=False,
            run_journal_export=False,
            discovery_ttl_seconds=3600,
            no_discovery_refresh=True,
            loop=False,
        )

        out = farm_loop._run_once(args, object(), {}, {}, tmp_path, apply=True)

        assert seen["called"] is True
        assert seen["provider"] == "provider"
        assert seen["tasks"] is not None
        assert out["paper"]["counters"]["cards"] == 1
        assert out["main_paper_bridge"]["instructions"] == 1

    def test_run_paper_refreshes_main_chain_once_when_signal_lane_runs(self, tmp_path, monkeypatch) -> None:
        seen: dict[str, int] = {"refresh_calls": 0}

        monkeypatch.setattr(farm_loop, "_providers", lambda args, apply: ("provider", None, None))
        monkeypatch.setattr(farm_loop, "_discovery", lambda args, root, apply: (None, {"status": "test"}))
        monkeypatch.setattr(
            farm_loop,
            "run_coordinator_cycle",
            lambda *args, **kwargs: {"pivot": "idle", "active_tasks": 0, "counters": {}, "status": {}},
        )

        from src.research_lab import paper_runtime

        monkeypatch.setattr(
            paper_runtime,
            "run_paper_cycle",
            lambda root, apply, limit, **kwargs: {"counters": {"cards": 1}, "readiness": {}, "results": []},
        )

        def fake_refresh(*args, **kwargs):
            seen["refresh_calls"] += 1

        monkeypatch.setattr(farm_loop, "_run_main_paper_derived_chain", fake_refresh)

        args = Namespace(
            max_plan_events=0,
            max_prepares=0,
            max_enrich=0,
            max_sweeps=0,
            run_worker=False,
            max_worker_jobs=0,
            max_validations=0,
            night_mode=False,
            allow_public_output=False,
            run_validation=False,
            no_followups=True,
            max_followups=0,
            sweep_tier="smoke",
            run_paper=True,
            max_paper_cards=1,
            true_forward_max_candidates=0,
            run_paper_signals=True,
            pfr_db_path="",
            paper_signals_fetch_timeout=1.0,
            paper_signals_timeframes="15m",
            paper_signals_max_new=0,
            paper_signals_max_pfr_scan=0,
            paper_signals_max_pfr_fetches=0,
            paper_signals_pfr_reserved=0,
            paper_signals_max_observe=0,
            paper_signals_max_live_fetches=0,
            paper_signals_max_network_fetches=0,
            main_paper_runtime_limit=0,
            provider="synthetic",
            backend="cpu",
            data_days=None,
            enrich_funding=False,
            enrich_oi=False,
            run_journal_export=False,
            discovery_ttl_seconds=3600,
            no_discovery_refresh=True,
            loop=False,
            no_live_universe_refresh=True,
            live_universe_ttl_seconds=3600,
            live_universe_top_n=1,
            send_paper_telegram=False,
            paper_telegram_limit=0,
            paper_telegram_status_digest=False,
            paper_telegram_status_digest_hours=12,
            run_calculator_advisor=False,
            run_agent_role_reviews=False,
        )

        farm_loop._run_once(args, object(), {}, {}, tmp_path, apply=True)

        assert seen["refresh_calls"] == 1

    def test_canonical_v2_startup_runs_current_chain_before_deferred_research(
        self, tmp_path, monkeypatch
    ) -> None:
        """A startup optimization must never skip the authoritative V2 chain."""

        seen: list[str] = []
        monkeypatch.setattr(farm_loop, "_providers", lambda *_a, **_k: ("provider", None, None))
        monkeypatch.setattr(farm_loop, "_read_intake", lambda *_a, **_k: [])
        monkeypatch.setattr(farm_loop, "_discovery", lambda *_a, **_k: (None, {"status": "test"}))
        monkeypatch.setattr(
            farm_loop,
            "run_coordinator_cycle",
            lambda *_a, **_k: {"pivot": "idle", "active_tasks": 0, "counters": {}, "status": {}},
        )
        monkeypatch.setattr(farm_loop, "_refresh_live_universe", lambda *_a, **_k: {"status": "test"})
        monkeypatch.setattr(farm_loop, "_maybe_storage_maintain", lambda *_a, **_k: {"state": "ready"})
        monkeypatch.setattr(farm_loop, "_stage_status", lambda *_a, **_k: {})

        from src.research_lab.paper_signals import cycle as paper_cycle
        from src.research_lab import paper_telegram_sender

        monkeypatch.setattr(
            paper_cycle,
            "run_cycle",
            lambda *_a, **_k: seen.append("paper_signals") or {"paper_only": True},
        )
        monkeypatch.setattr(
            farm_loop,
            "_run_paper_runtime",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("legacy paper runtime must be deferred until after T+0")
            ),
        )

        def current_chain(*_a, out, **_k):
            seen.append("current_v2")
            out["paper_generation_v2"] = {"state": "ready", "run_id": "run-current"}

        monkeypatch.setattr(farm_loop, "_run_main_paper_derived_chain", current_chain)
        monkeypatch.setattr(
            farm_loop,
            "_paper_telegram_delivery_config",
            lambda *_a, **_k: {"apply": False, "configured": False, "ids": [], "send_text": None, "send_photo": None},
        )
        monkeypatch.setattr(
            paper_telegram_sender,
            "send_paper_telegram_previews",
            lambda *_a, **_k: seen.append("delivery")
            or {"paper_generation_run_id": "run-current", "current_generation_compatible": True},
        )

        def completed_delivery(*_a, out, **_k):
            seen.append("mandatory_checkpoint")
            out["mandatory_product_cycle_complete"] = True

        monkeypatch.setattr(farm_loop, "_run_v2_post_delivery_maintenance_chain", completed_delivery)
        monkeypatch.setattr(
            farm_loop,
            "_run_post_t0_research_lanes",
            lambda *_a, **_k: seen.append("post_t0_research") or False,
        )

        args = Namespace(
            max_plan_events=0,
            max_prepares=0,
            max_enrich=0,
            max_sweeps=0,
            run_worker=False,
            max_worker_jobs=0,
            max_validations=0,
            night_mode=False,
            allow_public_output=False,
            run_validation=False,
            no_followups=True,
            max_followups=0,
            sweep_tier="smoke",
            run_paper=True,
            max_paper_cards=0,
            true_forward_max_candidates=0,
            run_paper_signals=True,
            paper_evidence_v2_required=True,
            pfr_db_path="",
            paper_signals_fetch_timeout=1.0,
            paper_signals_timeframes="15m",
            paper_signals_max_new=0,
            paper_signals_max_pfr_scan=0,
            paper_signals_max_pfr_fetches=0,
            paper_signals_pfr_reserved=0,
            paper_signals_max_observe=0,
            paper_signals_max_live_fetches=0,
            paper_signals_max_network_fetches=0,
            main_paper_runtime_limit=0,
            provider="synthetic",
            backend="cpu",
            data_days=None,
            enrich_funding=False,
            enrich_oi=False,
            run_journal_export=False,
            discovery_ttl_seconds=3600,
            no_discovery_refresh=True,
            loop=False,
            no_live_universe_refresh=True,
            live_universe_ttl_seconds=3600,
            live_universe_top_n=1,
            paper_telegram_limit=0,
            paper_telegram_status_digest=False,
            paper_telegram_status_digest_hours=12,
            run_calculator_advisor=False,
            run_agent_role_reviews=False,
            validation_backlog_slo_seconds=3600.0,
        )

        farm_loop._run_once(args, object(), {}, {}, tmp_path, apply=True)

        assert seen == [
            "paper_signals",
            "current_v2",
            "delivery",
            "mandatory_checkpoint",
            "post_t0_research",
        ]

    def test_published_successor_yields_waiting_pass_without_false_checkpoint(
        self, tmp_path, monkeypatch
    ) -> None:
        """Publication is not itself T+0; it must trigger a fresh V2 pass."""

        wake = threading.Event()
        wake.set()
        monkeypatch.setattr(farm_loop, "_providers", lambda *_a, **_k: ("provider", None, None))
        monkeypatch.setattr(farm_loop, "_read_intake", lambda *_a, **_k: [])
        monkeypatch.setattr(farm_loop, "_discovery", lambda *_a, **_k: (None, {"status": "test"}))
        monkeypatch.setattr(
            farm_loop,
            "run_coordinator_cycle",
            lambda *_a, **_k: {"pivot": "idle", "active_tasks": 0, "counters": {}, "status": {}},
        )
        monkeypatch.setattr(farm_loop, "_refresh_live_universe", lambda *_a, **_k: {"status": "test"})

        from src.research_lab.paper_signals import cycle as paper_cycle

        monkeypatch.setattr(paper_cycle, "run_cycle", lambda *_a, **_k: {"paper_only": True})

        def waiting_current_chain(*_a, out, **_k):
            out["paper_generation_v2"] = {"state": "waiting_validation_generation"}
            raise farm_loop._ValidationGenerationWaiting("pending")

        monkeypatch.setattr(farm_loop, "_run_main_paper_derived_chain", waiting_current_chain)

        args = Namespace(
            max_plan_events=0,
            max_prepares=0,
            max_enrich=0,
            max_sweeps=0,
            run_worker=False,
            max_worker_jobs=0,
            max_validations=0,
            night_mode=False,
            allow_public_output=False,
            run_validation=False,
            no_followups=True,
            max_followups=0,
            sweep_tier="smoke",
            run_paper=False,
            max_paper_cards=0,
            true_forward_max_candidates=0,
            run_paper_signals=True,
            paper_evidence_v2_required=True,
            product_cycle_wakeup_event=wake,
            pfr_db_path="",
            paper_signals_fetch_timeout=1.0,
            paper_signals_timeframes="15m",
            paper_signals_max_new=0,
            paper_signals_max_pfr_scan=0,
            paper_signals_max_pfr_fetches=0,
            paper_signals_pfr_reserved=0,
            paper_signals_max_observe=0,
            paper_signals_max_live_fetches=0,
            paper_signals_max_network_fetches=0,
            main_paper_runtime_limit=0,
            provider="synthetic",
            backend="cpu",
            data_days=None,
            enrich_funding=False,
            enrich_oi=False,
            run_journal_export=False,
            discovery_ttl_seconds=3600,
            no_discovery_refresh=True,
            loop=False,
            no_live_universe_refresh=True,
            live_universe_ttl_seconds=3600,
            live_universe_top_n=1,
            paper_telegram_limit=0,
            paper_telegram_status_digest=False,
            paper_telegram_status_digest_hours=12,
            run_calculator_advisor=False,
            run_agent_role_reviews=False,
        )

        out = farm_loop._run_once(args, object(), {}, {}, tmp_path, apply=True)

        assert out["startup_product_reentry"]["state"] == "current_generation_published"
        assert out["paper_telegram_delivery"]["skipped"] == "validation_generation_waiting"
        assert not (tmp_path / "state" / "product_progress" / "farm.json").exists()

    def test_post_t0_legacy_research_failure_is_degraded_not_product_revocation(
        self, tmp_path, monkeypatch
    ) -> None:
        class HealthyRuntime:
            def raise_if_failed(self) -> None:
                return None

        monkeypatch.setattr(
            farm_loop,
            "_run_paper_runtime",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("synthetic legacy failure")),
        )
        out: dict[str, object] = {}

        stopped = farm_loop._run_post_t0_research_lanes(
            Namespace(
                run_paper=True,
                true_forward_max_candidates=0,
                paper_generation_runtime=HealthyRuntime(),
            ),
            tmp_path,
            apply=True,
            loop=True,
            cycle_started_at=1.0,
            out=out,
            should_stop=lambda: False,
        )

        assert stopped is False
        assert out["errors"] == [
            {"where": "paper_runtime_post_t0", "error": "synthetic legacy failure"}
        ]

    def test_post_t0_research_never_hides_fence_or_owner_failure(
        self, tmp_path, monkeypatch
    ) -> None:
        class FailedRuntime:
            def raise_if_failed(self) -> None:
                raise RuntimeError("synthetic owner/fence loss")

        monkeypatch.setattr(
            farm_loop,
            "_run_paper_runtime",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("legacy failure")),
        )

        with pytest.raises(RuntimeError, match="owner/fence loss"):
            farm_loop._run_post_t0_research_lanes(
                Namespace(
                    run_paper=True,
                    true_forward_max_candidates=0,
                    paper_generation_runtime=FailedRuntime(),
                ),
                tmp_path,
                apply=True,
                loop=True,
                cycle_started_at=1.0,
                out={},
                should_stop=lambda: False,
            )


class TestCycleLogStages:
    def test_product_checkpoint_is_generation_bound_before_historical_maintenance(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(farm_loop.time, "time", lambda: 123.0)
        monkeypatch.setenv("TRADING_BOT_RCC_ATTEMPT_ID", "rccstartup_" + "b" * 32)
        monkeypatch.setenv("TRADING_BOT_RCC_REVISION", "b" * 40)
        monkeypatch.setenv("TRADING_BOT_RCC_PID", "4100")
        monkeypatch.setenv("TRADING_BOT_RCC_PROCESS_STARTED_AT", "100.0")
        bound = {
            "paper_generation_run_id": "run-current",
            "current_generation_compatible": True,
        }
        out = {
            "counters": {},
            "paper_generation_v2": {
                "state": "ready",
                "run_id": "run-current",
                "producer_membership": {},
            },
            "main_paper_bridge": bound,
            "main_paper_runtime_queue": bound,
            "main_paper_runtime_observation": bound,
            "paper_telegram_preview": bound,
            "paper_signal_training_export": bound,
            "outcome_retest_results": {"training_evidence": bound},
            "paper_telegram_delivery": bound,
        }

        farm_loop._publish_farm_product_checkpoint(tmp_path, out)

        checkpoint = json.loads(
            (tmp_path / "state" / "product_progress" / "farm.json").read_text(
                encoding="utf-8"
            )
        )
        assert checkpoint["status"] == "completed"
        assert checkpoint["metrics"]["generation_consistent"] is True
        assert checkpoint["metrics"]["paper_generation_run_id"] == "run-current"
        assert checkpoint["rcc_run"]["attempt_id"] == "rccstartup_" + "b" * 32
        assert "setup_outcome_memory_refresh" not in out
        assert "system_analyst_feedback" not in out

    def test_live_universe_refresh_skips_fresh_snapshot(self, tmp_path, monkeypatch) -> None:
        from src.research_lab import live_universe_selector

        now = 1000.0
        discovery = tmp_path / "discovery"
        discovery.mkdir()
        (discovery / "live_universe.json").write_text(
            json.dumps({
                "schema": "live_universe.v1",
                "generated_at": now - 60,
                "detail": {"fresh_movers": [{"symbol": "AAA_USDT_SWAP"}]},
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            live_universe_selector,
            "run",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("fresh snapshot must not refresh")),
        )

        out = farm_loop._refresh_live_universe(
            Namespace(live_universe_ttl_seconds=900, live_universe_top_n=12, no_live_universe_refresh=False),
            tmp_path,
            apply=True,
            now=now,
        )

        assert out["status"] == "fresh"
        assert out["refreshed"] is False
        assert out["count"] == 1

    def test_live_universe_refresh_updates_stale_snapshot(self, tmp_path, monkeypatch) -> None:
        from src.research_lab import live_universe_selector

        now = 10_000.0
        discovery = tmp_path / "discovery"
        discovery.mkdir()
        (discovery / "live_universe.json").write_text(
            json.dumps({
                "schema": "live_universe.v1",
                "generated_at": now - 10_000,
                "detail": {"fresh_movers": [{"symbol": "OLD_USDT_SWAP"}]},
            }),
            encoding="utf-8",
        )

        def fake_run(_root, *, top_n_per_group, now):
            assert top_n_per_group == 12
            return {
                "selected": {"fresh_movers": [{"symbol": "NEW_USDT_SWAP"}]},
                "intake_events": [{"event_id": "e1"}],
                "tickers_seen": 321,
            }

        def fake_write_snapshot(root, result, *, generated_at):
            (Path(root) / "discovery" / "live_universe.json").write_text(
                json.dumps({
                    "schema": "live_universe.v1",
                    "generated_at": generated_at,
                    "detail": result["selected"],
                }),
                encoding="utf-8",
            )

        monkeypatch.setattr(live_universe_selector, "run", fake_run)
        monkeypatch.setattr(live_universe_selector, "write_snapshot", fake_write_snapshot)
        monkeypatch.setattr(
            live_universe_selector,
            "apply_intake",
            lambda *_a, **_k: {"registered": 1, "duplicate": 0},
        )

        out = farm_loop._refresh_live_universe(
            Namespace(live_universe_ttl_seconds=900, live_universe_top_n=12, no_live_universe_refresh=False),
            tmp_path,
            apply=True,
            now=now,
        )

        assert out["status"] == "refreshed"
        assert out["refreshed"] is True
        assert out["count"] == 1
        assert out["tickers_seen"] == 321
        assert out["registered"] == 1

    def test_paper_telegram_config_default_is_dry_run(self) -> None:
        cfg = farm_loop._paper_telegram_delivery_config(
            Namespace(send_paper_telegram=False),
            apply=True,
        )

        assert cfg["apply"] is False
        assert cfg["configured"] is False
        assert cfg["ids"] == []
        assert cfg["send_text"] is None

    def test_paper_telegram_config_opt_in_uses_active_subscribers(self, monkeypatch) -> None:
        from scripts.strategy_lab import paper_telegram_transport
        from scripts import subscriptions
        from src.utils import telegram

        class FakeResponse:
            status = 200

            async def text(self) -> str:
                return '{"ok": true, "result": {"message_id": 42}}'

            async def json(self) -> dict:
                return {"ok": True, "result": {"message_id": 42}}

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, url: str, *, json: dict, timeout):
                assert url.startswith("https://api.telegram.org/bottoken/")
                assert json["chat_id"] == "111"
                assert json["text"] == "card"
                return FakeResponse()

        monkeypatch.setattr(
            subscriptions,
            "list_delivery_users",
            lambda: [
                {"chat_id": "111", "status": "active"},
                {"chat_id": "222", "status": "expired"},
                {"chat_id": "333", "status": "superadmin"},
            ],
        )

        async def fake_send_photo(
            chat_id: str,
            payload: bytes,
            caption: str = "",
            parse_mode: str | None = None,
        ) -> int:
            assert chat_id == "111"
            assert payload == b"chart-bytes"
            assert caption == "card"
            assert parse_mode == "HTML"
            return 43

        monkeypatch.setattr(telegram, "bot_token", lambda: "token")
        monkeypatch.setattr(telegram, "send_photo_bytes_to", fake_send_photo)
        monkeypatch.setattr(paper_telegram_transport.aiohttp, "ClientSession", FakeSession)

        cfg = farm_loop._paper_telegram_delivery_config(
            Namespace(send_paper_telegram=True),
            apply=True,
        )

        assert cfg["apply"] is True
        assert cfg["configured"] is True
        assert cfg["ids"] == ["111", "333"]
        assert asyncio.run(cfg["send_text"]("111", "card")) == 42
        assert asyncio.run(cfg["send_photo"]("111", b"chart-bytes", "card")) == 43

    def test_paper_telegram_config_marks_preconnect_failure_not_attempted(
        self, monkeypatch
    ) -> None:
        from scripts import subscriptions
        from scripts.strategy_lab import paper_telegram_transport
        from src.research_lab.paper_telegram_sender import DeliveryNotAttempted
        from src.utils import telegram

        class SyntheticConnectorError(Exception):
            pass

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, *_args, **_kwargs):
                raise SyntheticConnectorError("synthetic pre-connect failure")

        async def failed_photo(*_args, **_kwargs):
            raise SyntheticConnectorError("synthetic pre-connect failure")

        monkeypatch.setattr(
            subscriptions,
            "list_delivery_users",
            lambda: [{"chat_id": "111", "status": "active"}],
        )
        monkeypatch.setattr(telegram, "bot_token", lambda: "token")
        monkeypatch.setattr(telegram, "send_photo_bytes_to", failed_photo)
        monkeypatch.setattr(
            paper_telegram_transport.aiohttp,
            "ClientConnectorError",
            SyntheticConnectorError,
        )
        monkeypatch.setattr(
            paper_telegram_transport.aiohttp,
            "ClientSession",
            FakeSession,
        )

        cfg = farm_loop._paper_telegram_delivery_config(
            Namespace(send_paper_telegram=True),
            apply=True,
        )

        with pytest.raises(DeliveryNotAttempted):
            asyncio.run(cfg["send_text"]("111", "card"))
        with pytest.raises(DeliveryNotAttempted):
            asyncio.run(cfg["send_photo"]("111", b"chart-bytes", "card"))

    def test_log_cycle_records_stages_and_skipped(self, tmp_path) -> None:
        stages = farm_loop._stage_status(_args(run_worker=True), apply=True)
        result = {"pivot": "work_available", "active_tasks": 3, "counters": {"sweeps": 2},
                  "status": {"by_state": {"queued": 3}}}
        farm_journal.log_cycle(tmp_path, ts=1000.0, mode="apply", result=result, stages=stages)
        cycles = farm_journal.read_recent_cycles(tmp_path, limit=5)
        assert len(cycles) == 1
        assert cycles[-1]["stages"]["worker"]["enabled"] is True
        # validation + paper are off -> skipped_stages reports them
        skipped = farm_journal.skipped_stages(cycles[-1])
        assert set(skipped) == {"validation", "paper"}

    def test_skipped_stages_empty_when_no_stage_data(self) -> None:
        assert farm_journal.skipped_stages({"pivot": "x"}) == []

    def test_sleep_until_next_cycle_stops_immediately_when_stop_file_exists(self, tmp_path) -> None:
        stop_file = tmp_path / "STOP_FARM_FULL_CYCLE.txt"
        stop_file.write_text("stop", encoding="utf-8")

        assert farm_loop._sleep_until_next_cycle(600, str(stop_file)) is False

    def test_validation_generation_wakeup_bypasses_full_cycle_cadence(self) -> None:
        wake_event = threading.Event()
        wake_event.set()

        assert farm_loop._sleep_until_next_cycle(
            180,
            wake_event=wake_event,
        ) is True
        assert wake_event.is_set() is False

    def test_stop_intent_wins_over_generation_wakeup(self, tmp_path) -> None:
        stop_file = tmp_path / "STOP_FARM_FULL_CYCLE.txt"
        stop_file.write_text("stop", encoding="utf-8")
        wake_event = threading.Event()
        wake_event.set()

        assert farm_loop._sleep_until_next_cycle(
            180,
            str(stop_file),
            wake_event=wake_event,
        ) is False

    def test_smoke_caps_skip_forward_and_new_paper_generation(self, tmp_path, monkeypatch) -> None:
        from src.research_lab.paper_signals import cycle as paper_cycle
        from src.research_lab.providers import okx_public

        seen: dict[str, object] = {}

        def fake_cycle(*_args, **kwargs):
            seen["max_new"] = kwargs["max_new"]
            seen["max_pfr_scan"] = kwargs["max_pfr_scan"]
            seen["max_pfr_fetches"] = kwargs["max_pfr_fetches"]
            seen["pfr_reserved_new"] = kwargs["pfr_reserved_new"]
            seen["max_observe"] = kwargs["max_observe"]
            seen["max_live_fetches"] = kwargs["max_live_fetches"]
            seen["max_network_fetches"] = kwargs["max_network_fetches"]
            seen["timeframes"] = kwargs["timeframes"]
            seen["paper_provider_direct_http"] = (
                getattr(getattr(kwargs["provider"], "fallback", None), "http_get", None)
                is okx_public._httpx_get_direct
            )
            return {"generated": 0, "pfr_counts": {}, "state": {}, "gate_counts": {}}

        coordinator_seen: dict[str, int] = {}

        def fake_coordinator(*_args, **kwargs):
            coordinator_seen["max_plan_events"] = kwargs["max_plan_events"]
            coordinator_seen["max_discovery"] = kwargs["max_discovery"]
            coordinator_seen["max_validations"] = kwargs["max_validations"]
            return {
                "pivot": "smoke",
                "active_tasks": 0,
                "counters": {},
                "status": {},
                "errors": [],
            }

        monkeypatch.setattr(farm_loop, "_providers", lambda *_a, **_k: (None, None, None))
        monkeypatch.setattr(farm_loop, "_read_intake", lambda *_a, **_k: [])
        monkeypatch.setattr(farm_loop, "_discovery", lambda *_a, **_k: (None, {"status": "smoke"}))
        monkeypatch.setattr(farm_loop, "_refresh_live_universe", lambda *_a, **_k: {"status": "smoke"})
        monkeypatch.setattr(farm_loop, "_maybe_storage_maintain", lambda *_a, **_k: None)
        monkeypatch.setattr(farm_loop, "run_coordinator_cycle", fake_coordinator)
        monkeypatch.setattr(paper_cycle, "run_cycle", fake_cycle)

        class FakeOkxProvider:
            name = "fake-okx"
            configured = True

            def __init__(self, *, timeout, http_get=None) -> None:
                self.timeout = timeout
                self.http_get = http_get

        monkeypatch.setattr(okx_public, "OkxPublicMarketDataProvider", FakeOkxProvider)

        args = Namespace(
            max_plan_events=0,
            max_prepares=0,
            max_enrich=0,
            max_sweeps=0,
            run_worker=False,
            max_worker_jobs=0,
            max_validations=0,
            night_mode=False,
            allow_public_output=False,
            run_validation=False,
            no_followups=True,
            max_followups=0,
            sweep_tier="smoke",
            run_paper=False,
            max_paper_cards=0,
            true_forward_max_candidates=0,
            run_paper_signals=True,
            pfr_db_path="",
            paper_signals_fetch_timeout=1.0,
            paper_signals_timeframes="15m,1h,4h",
            paper_signals_max_new=0,
            paper_signals_max_pfr_scan=0,
            paper_signals_max_pfr_fetches=0,
            paper_signals_pfr_reserved=0,
            paper_signals_max_observe=0,
            paper_signals_max_live_fetches=0,
            paper_signals_max_network_fetches=0,
            main_paper_runtime_limit=0,
            provider="synthetic",
            backend="cpu",
            data_days=None,
            enrich_funding=False,
            enrich_oi=False,
            run_journal_export=False,
            discovery_ttl_seconds=3600,
            no_discovery_refresh=True,
        )
        derived = tmp_path / "state" / "derived"
        derived.mkdir(parents=True)
        existing_observation = derived / "main_paper_runtime_observation.json"
        existing_observation.write_text(
            json.dumps({
                "schema": "main_paper_runtime_observation.v1",
                "rows_read": 5,
                "observed": 5,
                "reviewed": 5,
                "invalid": 0,
                "provider_error": 0,
                "execution_allowed": False,
            }),
            encoding="utf-8",
        )
        before = existing_observation.read_text(encoding="utf-8")

        out = farm_loop._run_once(args, object(), {}, {}, tmp_path, apply=True)

        assert coordinator_seen == {"max_plan_events": 0, "max_discovery": 0, "max_validations": 0}
        assert out["true_forward"]["skipped"] == "true_forward_max_candidates=0"
        assert seen == {
            "max_new": 0,
            "max_pfr_scan": 0,
            "max_pfr_fetches": 0,
            "pfr_reserved_new": 0,
            "max_observe": 0,
            "max_live_fetches": 0,
            "max_network_fetches": 0,
            "timeframes": ("15m", "1h", "4h"),
            "paper_provider_direct_http": True,
        }
        assert out["main_paper_runtime_queue"]["queued"] == 0
        assert out["main_paper_runtime_queue"]["execution_allowed"] is False
        assert out["main_paper_runtime_observation"]["rows_read"] == 0
        assert out["main_paper_runtime_observation"]["execution_allowed"] is False
        assert out["trade_thesis_supervisor"]["paper_only"] is True
        assert out["trade_thesis_supervisor"]["execution_allowed"] is False
        assert out["paper_telegram_delivery"]["dry_run"] is True
        assert out["paper_telegram_delivery"]["sends_network"] is False
        assert out["paper_telegram_delivery"]["execution_allowed"] is False
        assert out["paper_telegram_preview"]["skipped_quality_gate"] == 0
        assert out["paper_signal_training_export"]["rows"] == 0
        assert out["paper_signal_training_export"]["terminal_only"] is True
        assert out["paper_signal_training_export"]["paper_only"] is True
        assert out["product_signal_training_export"]["paper_only"] is True
        assert out["product_signal_training_export"]["execution_allowed"] is False
        assert out["paper_product_quality_report"]["paper_only"] is True
        assert out["paper_product_quality_report"]["execution_allowed"] is False
        assert existing_observation.read_text(encoding="utf-8") == before

    def test_visible_full_cycle_bat_bounds_paper_signal_observation(self) -> None:
        bat = Path("bat/strategy_lab_farm_full_cycle_loop.bat").read_text(encoding="utf-8")

        assert "STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE=20" in bat
        assert "STRATEGY_LAB_PAPER_SIGNALS_MAX_LIVE_FETCHES=12" in bat
        assert "STRATEGY_LAB_PAPER_SIGNALS_MAX_NETWORK_FETCHES=44" in bat
        assert "STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES=12" in bat
        assert "STRATEGY_LAB_LIVE_UNIVERSE_TTL_SECONDS=900" in bat
        assert "STRATEGY_LAB_LIVE_UNIVERSE_TOP_N=12" in bat
        assert "STRATEGY_LAB_PAPER_SIGNALS_FETCH_TIMEOUT=3" in bat
        assert "STRATEGY_LAB_FARM_MAX_VALIDATIONS=10" in bat
        assert "live_fetches=%STRATEGY_LAB_PAPER_SIGNALS_MAX_LIVE_FETCHES%" in bat
        assert "network_fetches=%STRATEGY_LAB_PAPER_SIGNALS_MAX_NETWORK_FETCHES%" in bat
        assert "pfr_fetches=%STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES%" in bat
        assert "live universe: ttl=%STRATEGY_LAB_LIVE_UNIVERSE_TTL_SECONDS%s" in bat
        assert "'--paper-signals-max-observe','%STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE%'" in bat
        assert "'--paper-signals-max-live-fetches','%STRATEGY_LAB_PAPER_SIGNALS_MAX_LIVE_FETCHES%'" in bat
        assert "'--paper-signals-max-network-fetches','%STRATEGY_LAB_PAPER_SIGNALS_MAX_NETWORK_FETCHES%'" in bat
        assert "'--paper-signals-max-pfr-fetches','%STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES%'" in bat
        assert "'--paper-signals-max-seconds','%STRATEGY_LAB_PAPER_SIGNALS_MAX_SECONDS%'" in bat
        assert "'--live-universe-ttl-seconds','%STRATEGY_LAB_LIVE_UNIVERSE_TTL_SECONDS%'" in bat
        assert "'--live-universe-top-n','%STRATEGY_LAB_LIVE_UNIVERSE_TOP_N%'" in bat
        assert "'--max-validations','%STRATEGY_LAB_FARM_MAX_VALIDATIONS%'" in bat
        assert "STRATEGY_LAB_VALIDATION_BACKLOG_HIGH_WATER=256" in bat
        assert "STRATEGY_LAB_VALIDATION_BACKLOG_SLO_SECONDS=3600" in bat
        assert (
            "'--validation-backlog-high-water',"
            "'%STRATEGY_LAB_VALIDATION_BACKLOG_HIGH_WATER%'"
        ) in bat
        assert (
            "'--validation-backlog-slo-seconds',"
            "'%STRATEGY_LAB_VALIDATION_BACKLOG_SLO_SECONDS%'"
        ) in bat
        assert "STRATEGY_LAB_PAPER_SIGNALS_PFR_RESERVED=2" in bat
        assert "'--paper-signals-pfr-reserved','%STRATEGY_LAB_PAPER_SIGNALS_PFR_RESERVED%'" in bat
        assert "STRATEGY_LAB_RUN_CALCULATOR_ADVISOR=1" in bat
        assert (
            'if /I "%STRATEGY_LAB_RUN_CALCULATOR_ADVISOR%"=="1" '
            'set "STRATEGY_LAB_CALCULATOR_ADVISOR_ARG=--run-calculator-advisor"'
        ) in bat
        assert "STRATEGY_LAB_CALCULATOR_ADVISOR_MAX_CALLS=1" in bat
        assert "'--calculator-advisor-max-calls','%STRATEGY_LAB_CALCULATOR_ADVISOR_MAX_CALLS%'" in bat
        assert "STRATEGY_LAB_RUN_AGENT_ROLE_REVIEWS=0" in bat
        assert "STRATEGY_LAB_RUN_JOURNAL_EXPORT=1" in bat
        assert "'%STRATEGY_LAB_JOURNAL_EXPORT_ARG%'" in bat
        assert "private_fills=forced_off" in bat
        assert "'%STRATEGY_LAB_CALCULATOR_ADVISOR_ARG%'" in bat
        assert "'%STRATEGY_LAB_AGENT_ROLE_REVIEWS_ARG%'" in bat
        assert "'--agent-role-provider','%STRATEGY_LAB_AGENT_ROLE_PROVIDER%'" in bat
        assert "Tee-Object" not in bat
        assert "Add-Content -Path '%LOG_FILE%' -Value $line -Encoding UTF8" not in bat
        assert '"& python @cmd;"' in bat

    def test_farm_loop_cli_default_matches_visible_pfr_budget(self) -> None:
        source = Path("scripts/strategy_lab/farm_loop.py").read_text(encoding="utf-8")

        assert 'STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES", "12"' in source
        assert 'paper_signals_max_pfr_fetches", 12' in source
        assert 'paper_signals_max_pfr_fetches", 8' not in source

    def test_visible_full_cycle_network_cap_covers_paper_signal_lanes(self) -> None:
        bat = Path("bat/strategy_lab_farm_full_cycle_loop.bat").read_text(encoding="utf-8")

        def default_int(name: str) -> int:
            marker = f"set \"{name}="
            line = next(item for item in bat.splitlines() if marker in item)
            return int(line.split(marker, 1)[1].split("\"", 1)[0])

        observe = default_int("STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE")
        live = default_int("STRATEGY_LAB_PAPER_SIGNALS_MAX_LIVE_FETCHES")
        pfr = default_int("STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES")
        network = default_int("STRATEGY_LAB_PAPER_SIGNALS_MAX_NETWORK_FETCHES")

        assert network >= observe + live + pfr

    def test_journal_export_forces_private_fills_off_and_restores_env(self, monkeypatch, tmp_path) -> None:
        import scripts.build_journal as journal

        journal_path = tmp_path / "journal.xlsx"
        seen: dict[str, str | None] = {}

        def fake_build() -> None:
            seen["root"] = farm_loop.os.environ.get("TRADING_BOT_RESEARCH_ROOT")
            seen["private_fills"] = farm_loop.os.environ.get("JOURNAL_ENABLE_PRIVATE_FILLS")
            journal_path.write_bytes(b"xlsx")

        monkeypatch.setenv("TRADING_BOT_RESEARCH_ROOT", "old-root")
        monkeypatch.setenv("JOURNAL_ENABLE_PRIVATE_FILLS", "1")
        monkeypatch.setattr(journal, "build", fake_build)
        monkeypatch.setattr(journal, "JOURNAL_PATH", journal_path)

        out = farm_loop._run_journal_export_stage(tmp_path, apply=True)

        assert seen == {"root": str(tmp_path), "private_fills": "0"}
        assert out["status"] == "rebuilt"
        assert out["private_fills"] is False
        assert out["paper_only"] is True
        assert out["execution_allowed"] is False
        assert farm_loop.os.environ["TRADING_BOT_RESEARCH_ROOT"] == "old-root"
        assert farm_loop.os.environ["JOURNAL_ENABLE_PRIVATE_FILLS"] == "1"


def test_production_memory_refresh_blocks_snapshot_after_owner_lease_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import setup_outcome_memory as memory

    signal = farm_loop._TaskClaimFailureSignal(
        tmp_path,
        threading.Event(),
        interrupt_main=lambda: None,
    )
    args = SimpleNamespace(task_claim_failure_signal=signal)
    milestones: list[str] = []
    snapshot_written = False

    def build(
        _root,
        *,
        progress,
        check_active,
        reject_cache_path,
        build_stats,
    ):
        assert reject_cache_path.name == "setup_outcome_memory_reject_cache.json"
        build_stats["reject_characterization"] = {"recomputed": 10}
        check_active()
        progress("lifecycle_loaded", 10, 10)
        milestones.append("lifecycle_loaded")
        signal.notify(
            RuntimeError("synthetic owner lease failure"),
            {
                "failure_kind": "process_lease",
                "fencing_token": 7,
            },
        )
        progress("records_built", 10, 10)
        return []

    def forbidden_snapshot(*_args, **_kwargs):
        nonlocal snapshot_written
        snapshot_written = True
        pytest.fail("lost process authority must block snapshot publication")

    monkeypatch.setattr(memory, "build_memory_index", build)
    monkeypatch.setattr(memory, "write_memory_snapshot", forbidden_snapshot)
    monkeypatch.setattr(
        farm_loop,
        "_write_loop_status",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(
        RuntimeError,
        match="canonical process ownership heartbeat failed",
    ):
        farm_loop._refresh_setup_outcome_memory(
            args,
            tmp_path,
            apply=True,
            loop=True,
            cycle_started_at=100.0,
        )

    assert milestones == ["lifecycle_loaded"]
    assert snapshot_written is False


def test_production_memory_refresh_cancels_on_stop_intent_before_snapshot(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import setup_outcome_memory as memory

    stop_file = tmp_path / "STOP_FARM.txt"
    stop_file.write_text("stop", encoding="utf-8")
    args = SimpleNamespace(
        task_claim_failure_signal=None,
        stop_file=str(stop_file),
    )
    snapshot_written = False

    def build(
        _root,
        *,
        progress,
        check_active,
        reject_cache_path,
        build_stats,
    ):
        assert reject_cache_path.name == "setup_outcome_memory_reject_cache.json"
        check_active()
        progress("unreachable", 1, 1)
        return []

    def forbidden_snapshot(*_args, **_kwargs):
        nonlocal snapshot_written
        snapshot_written = True
        pytest.fail("stop intent must block snapshot publication")

    monkeypatch.setattr(memory, "build_memory_index", build)
    monkeypatch.setattr(memory, "write_memory_snapshot", forbidden_snapshot)

    with pytest.raises(
        farm_loop.FarmCycleStopRequested,
        match="canonical stop requested",
    ):
        farm_loop._refresh_setup_outcome_memory(
            args,
            tmp_path,
            apply=True,
            loop=True,
            cycle_started_at=100.0,
        )

    assert snapshot_written is False


def test_production_memory_refresh_publishes_incremental_cache_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import setup_outcome_memory as memory

    args = SimpleNamespace(task_claim_failure_signal=None, stop_file="")
    product_evidence = {
        "summary": {
            "rows": 2,
            "terminal_rows": 1,
            "paper_pnl_usdt": 0.5,
        },
        "paper_generation_run_id": "run-v2",
        "generation_status": "complete",
        "current_generation_compatible": True,
    }
    captured: dict[str, object] = {}

    def build(
        _root,
        *,
        progress,
        check_active,
        reject_cache_path,
        build_stats,
    ):
        check_active()
        assert reject_cache_path.name == "setup_outcome_memory_reject_cache.json"
        build_stats["reject_characterization"] = {
            "cache_hits": 49,
            "recomputed": 1,
            "run_artifacts_reread": 1,
        }
        progress("records_built", 50, 50)
        return []

    def write_snapshot(_root, *, records, product_paper_memory):
        captured["records"] = records
        captured["product_paper_memory"] = product_paper_memory
        return tmp_path / "state" / "derived" / "setup_outcome_memory.json"

    monkeypatch.setattr(memory, "build_memory_index", build)
    monkeypatch.setattr(memory, "summarize_memory", lambda _records: {"total": 50})
    monkeypatch.setattr(
        memory,
        "summarize_product_training_memory",
        lambda *_args, **_kwargs: product_evidence,
    )
    monkeypatch.setattr(memory, "write_memory_snapshot", write_snapshot)
    monkeypatch.setattr(farm_loop, "_write_loop_status", lambda *_args, **_kwargs: True)

    out = farm_loop._refresh_setup_outcome_memory(
        args,
        tmp_path,
        apply=True,
        loop=True,
        cycle_started_at=100.0,
    )

    assert out["reject_characterization"] == {
        "cache_hits": 49,
        "recomputed": 1,
        "run_artifacts_reread": 1,
    }
    assert captured["product_paper_memory"] is product_evidence
    assert out["paper_generation_run_id"] == "run-v2"
    assert out["current_generation_compatible"] is True


def test_historical_memory_backfill_rejects_stale_generation_before_snapshot(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import setup_outcome_memory as memory

    args = SimpleNamespace(task_claim_failure_signal=None, stop_file="")
    snapshot_written = False

    monkeypatch.setattr(
        memory,
        "build_memory_index",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(memory, "summarize_memory", lambda _records: {"total": 0})
    monkeypatch.setattr(
        memory,
        "summarize_product_training_memory",
        lambda *_args, **_kwargs: {
            "summary": {"rows": 0, "terminal_rows": 0, "paper_pnl_usdt": 0.0},
            "paper_generation_run_id": "newer-run",
            "generation_status": "complete",
            "current_generation_compatible": True,
        },
    )

    def forbidden_snapshot(*_args, **_kwargs):
        nonlocal snapshot_written
        snapshot_written = True
        pytest.fail("stale-generation backfill must not publish a snapshot")

    monkeypatch.setattr(memory, "write_memory_snapshot", forbidden_snapshot)
    monkeypatch.setattr(farm_loop, "_write_loop_status", lambda *_a, **_k: True)

    with pytest.raises(
        farm_loop.SetupOutcomeMemoryGenerationChanged,
        match="generation changed",
    ):
        farm_loop._refresh_setup_outcome_memory(
            args,
            tmp_path,
            apply=True,
            loop=True,
            cycle_started_at=100.0,
            stage="setup_outcome_memory_backfill",
            expected_generation_run_id="run-current",
        )

    assert snapshot_written is False


def test_historical_memory_backfill_yields_without_snapshot_or_delivery_replay(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.research_lab import setup_outcome_memory as memory

    args = SimpleNamespace(
        task_claim_failure_signal=None,
        stop_file="",
        setup_memory_backfill_max_seconds=5.0,
        setup_memory_backfill_max_recomputed_rows=2,
    )
    def defer(*_args, **_kwargs):
        raise memory.SetupOutcomeMemoryBackfillDeferred(
            {
                "sources": 26_845,
                "cache_hits": 200,
                "recomputed": 2,
                "cache_complete": False,
                "deferred_reason": "slice_budget",
            }
        )

    monkeypatch.setattr(memory, "build_memory_index", defer)
    monkeypatch.setattr(
        memory,
        "write_memory_snapshot",
        lambda *_a, **_k: pytest.fail("deferred backfill must not publish a snapshot"),
    )
    monkeypatch.setattr(farm_loop, "_write_loop_status", lambda *_a, **_k: True)

    out = farm_loop._refresh_setup_outcome_memory(
        args,
        tmp_path,
        apply=True,
        loop=True,
        cycle_started_at=100.0,
        stage="setup_outcome_memory_backfill",
        expected_generation_run_id="run-current",
    )

    assert out["state"] == "deferred"
    assert out["completed"] == 202
    assert out["total"] == 26_845
