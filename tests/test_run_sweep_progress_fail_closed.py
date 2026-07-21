from __future__ import annotations

import threading
import time
from contextlib import AbstractContextManager
from dataclasses import asdict

import pytest

from src.research_lab import farm_coordinator
from src.research_lab.candle_store import CandleStore
from src.research_lab.farm_coordinator import run_coordinator_cycle
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
from src.research_lab.ownership import OwnershipStore, ProcessIdentity
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.state_db import connect, default_db_path, init_db
from src.research_lab.sweep_spec import SweepSpec
from src.research_lab.task_claim_heartbeat import TaskClaimHeartbeat
from src.research_lab.timeframes import load_timeframe_profiles


class LogicalClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _seed_candles(root, symbol="CL_USDT_SWAP", timeframe="1h") -> None:
    CandleStore(root).upsert_candles(
        symbol,
        timeframe,
        [
            {
                "ts": index * 3_600_000,
                "open": 100.0 + index,
                "high": 101.0 + index,
                "low": 99.0 + index,
                "close": 100.5 + index,
                "vol": 10.0,
            }
            for index in range(200)
        ],
        source="fixture",
        available_at_ms=100,
    )


def _identity() -> ProcessIdentity:
    return ProcessIdentity(
        pid=4242,
        started_at=10.0,
        executable="C:/Python/python.exe",
        command_digest="sha256:canonical-farm",
    )


def _claimed_run_sweep(
    root, *, clock=time.time, lease_seconds=900.0, payload=None,
):
    tasks = FarmTasksDB(
        tasks_db_path(root),
        owner_id="canonical-owner",
        lease_seconds=lease_seconds,
        clock=clock,
    )
    task_id, _ = tasks.enqueue_task(
        task_type="run_sweep",
        task_key="run_sweep::CL_USDT_SWAP::1h::bb_volume_fade::fixture",
        symbol="CL_USDT_SWAP",
        timeframe="1h",
        family="bb_volume_fade",
        priority=10,
        data_fingerprint="fixture",
        payload=payload,
        now=clock(),
    )
    return tasks, task_id


def _owner(root, clock=time.time, lease_seconds=10_000.0):
    identity = _identity()

    def probe(pid):
        return identity if pid == identity.pid else None

    store = OwnershipStore(root / "ownership.sqlite", clock=clock, identity_probe=probe)
    lease = store.acquire(
        resource_id="canonical_farm",
        role_id="farm",
        owner_id="canonical-owner",
        identity=identity,
        lease_seconds=lease_seconds,
    )
    return store, lease, probe


class _LogicalHeartbeatContext(AbstractContextManager):
    """Drive the real fenced renewal deterministically from production milestones."""

    def __init__(self, heartbeat, owner_store, renewal_db, clock, stages) -> None:
        self.heartbeat = heartbeat
        self.owner_store = owner_store
        self.renewal_db = renewal_db
        self.clock = clock
        self.stages = stages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.renewal_db.close()

    def progress(self, stage: str) -> None:
        self.clock.value += 2.0
        self.heartbeat.progress(stage)
        self.heartbeat._renew_if_progressed(self.owner_store, self.renewal_db)
        self.stages.append(stage)

    def assert_active(self) -> None:
        self.heartbeat.assert_active()


def test_production_run_sweep_keeps_claim_beyond_900_logical_seconds(tmp_path) -> None:
    clock = LogicalClock()
    _seed_candles(tmp_path)
    tasks, task_id = _claimed_run_sweep(tmp_path, clock=clock)
    owner_store, process_lease, probe = _owner(tmp_path, clock=clock)
    stages = []

    def guard_factory(task_db, task):
        heartbeat = TaskClaimHeartbeat(
            task_db,
            task,
            ownership_path=tmp_path / "ownership.sqlite",
            process_lease=process_lease,
            lease_seconds=900.0,
            renew_interval_seconds=30.0,
            max_no_progress_seconds=300.0,
            clock=clock,
            monotonic=clock,
            identity_probe=probe,
        )
        renewal_db = FarmTasksDB(
            task_db.path,
            owner_id=process_lease.owner_id,
            lease_seconds=900.0,
            clock=clock,
        )
        return _LogicalHeartbeatContext(
            heartbeat, owner_store, renewal_db, clock, stages,
        )

    out = run_coordinator_cycle(
        tasks,
        private_root=tmp_path,
        profiles=load_timeframe_profiles(),
        policy=load_resource_policy(),
        intake_events=[],
        apply=True,
        now=100.0,
        run_worker=False,
        run_validation=False,
        run_followups=False,
        max_prepares=0,
        max_enrich=0,
        max_sweeps=1,
        task_claim_guard_factory=guard_factory,
    )

    assert clock.value > 1_000.0
    assert out["counters"]["sweeps_materialized"] == 1
    assert any(stage.startswith("grid_validation:") for stage in stages)
    assert any(stage.startswith("grid_ledger:") for stage in stages)
    task = tasks.get_task(task_id)
    assert task["state"] == "running"
    assert task["claim_expires_at"] > clock.value
    assert tasks.raw_connection.execute(
        "SELECT COUNT(*) FROM materialization_outbox"
    ).fetchone()[0] == 1
    compute = connect(default_db_path(tmp_path))
    try:
        assert compute.execute("SELECT COUNT(*) FROM queue_materializations").fetchone()[0] == 1
        assert compute.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 1
    finally:
        compute.close()
        owner_store.release(process_lease)
        owner_store.close()
        tasks.close()


def test_claimed_run_sweep_uses_no_public_network_after_data_is_canonical(
    monkeypatch, tmp_path,
) -> None:
    import socket
    import urllib.request

    import requests

    _seed_candles(tmp_path)
    sweep = SweepSpec(
        sweep_id="network-isolation",
        anchor_symbol="CL_USDT_SWAP",
        related_symbols=(),
        timeframe="1h",
        setup_family="bb_volume_fade",
        setup_grid={"bb_period": [20], "bb_std": [2.0]},
        exit_grid={"hold_bars": [4], "stop_pct": [6.0], "take_pct": [12.0]},
        max_variants=1,
        backend="cpu",
        resource_class="light",
    )
    tasks, _task_id = _claimed_run_sweep(
        tmp_path, payload={"sweep_spec": asdict(sweep)},
    )

    def forbidden_network(*args, **kwargs):
        raise AssertionError("run_sweep must not make a public or private network call")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_network)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden_network)

    out = run_coordinator_cycle(
        tasks,
        private_root=tmp_path,
        profiles=load_timeframe_profiles(),
        policy=load_resource_policy(),
        intake_events=[],
        apply=True,
        now=time.time(),
        run_worker=False,
        run_validation=False,
        run_followups=False,
        max_prepares=0,
        max_enrich=0,
        max_sweeps=1,
    )

    assert out["counters"]["sweeps_materialized"] == 1
    tasks.close()


@pytest.mark.parametrize("blocked_stage", ["sqlite", "compile"])
def test_production_blocking_stage_fails_visible_before_expiry_without_side_effects(
    monkeypatch, tmp_path, blocked_stage,
) -> None:
    _seed_candles(tmp_path)
    tasks, task_id = _claimed_run_sweep(tmp_path, lease_seconds=0.25)
    owner_store, process_lease, probe = _owner(tmp_path, lease_seconds=10.0)
    failure_seen = threading.Event()
    failures = []

    def on_failure(failure, snapshot):
        failures.append((failure, snapshot, time.time()))
        failure_seen.set()

    def guard_factory(task_db, task):
        return TaskClaimHeartbeat(
            task_db,
            task,
            ownership_path=tmp_path / "ownership.sqlite",
            process_lease=process_lease,
            lease_seconds=0.25,
            renew_interval_seconds=0.02,
            max_no_progress_seconds=0.06,
            identity_probe=probe,
            on_failure=on_failure,
        )

    if blocked_stage == "sqlite":
        from src.research_lab import candle_library

        real_load = candle_library.load_canonical_candles

        def blocked_load(*args, **kwargs):
            assert failure_seen.wait(1.0)
            return real_load(*args, **kwargs)

        monkeypatch.setattr(candle_library, "load_canonical_candles", blocked_load)
    else:
        real_queue = farm_coordinator.queue_sweep

        def blocked_queue(*args, **kwargs):
            assert failure_seen.wait(1.0)
            return real_queue(*args, **kwargs)

        monkeypatch.setattr(farm_coordinator, "queue_sweep", blocked_queue)

    started = time.time()
    with pytest.raises(Exception):
        run_coordinator_cycle(
            tasks,
            private_root=tmp_path,
            profiles=load_timeframe_profiles(),
            policy=load_resource_policy(),
            intake_events=[],
            apply=True,
            now=started,
            run_worker=False,
            run_validation=False,
            run_followups=False,
            max_prepares=0,
            max_enrich=0,
            max_sweeps=1,
            task_claim_guard_factory=guard_factory,
        )

    task = tasks.get_task(task_id)
    assert failures
    # The claim may renew while earlier production milestones make real
    # progress.  Compare the visible failure with the current fenced expiry,
    # not the original claim deadline measured before those milestones.
    assert failures[0][2] < float(task["claim_expires_at"])
    assert "owner_id" not in failures[0][1]
    assert task["state"] == "running"
    assert tasks.raw_connection.execute(
        "SELECT COUNT(*) FROM materialization_outbox"
    ).fetchone()[0] == 0
    compute = connect(default_db_path(tmp_path))
    try:
        init_db(compute)
        assert compute.execute("SELECT COUNT(*) FROM queue_materializations").fetchone()[0] == 0
        assert compute.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 0
    finally:
        compute.close()
        owner_store.release(process_lease)
        owner_store.close()
        tasks.close()
