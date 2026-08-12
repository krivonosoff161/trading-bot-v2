from __future__ import annotations

import json
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.research_lab import ownership as ownership_module

from scripts import research_control_center as control_center
from scripts.strategy_lab.release_materialization_payloads import (
    run as run_payload_release,
)
from src.research_lab.farm_coordinator import _replay_materialization_outbox
from src.research_lab.farm_tasks_db import (
    FarmFencingMigrationRequired,
    FarmTasksDB,
    StaleTaskClaimError,
    activate_farm_fencing_v2,
)
from src.research_lab.ownership import (
    OwnershipConflictError,
    OwnershipStore,
    ProcessIdentity,
    StaleProcessLeaseError,
)
from src.research_lab.state_db import (
    FencingMigrationRequired,
    StaleJobClaimError,
    activate_fencing_v2,
    claim_next_job,
    complete_job,
    connect,
    enqueue_experiment,
    ensure_experiment_queued,
    fail_job,
    init_db,
    mark_job_executing,
    mark_publication_indexes_published,
    publish_completed_job,
    reap_stale_jobs,
    recover_pending_publications,
    renew_job_lease,
)


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _identity(pid: int = 101) -> ProcessIdentity:
    return ProcessIdentity(
        pid=pid,
        started_at=10.0,
        executable="C:/Python/python.exe",
        command_digest="sha256:farm-command",
    )


def _ownership_store(tmp_path, clock: Clock, live: dict[int, ProcessIdentity | None]):
    return OwnershipStore(
        tmp_path / "ownership.sqlite",
        clock=clock,
        identity_probe=lambda pid: live.get(pid),
    )


def test_process_lease_has_one_owner_and_monotonic_fence(tmp_path) -> None:
    clock = Clock()
    live = {101: _identity()}
    first = _ownership_store(tmp_path, clock, live)
    second = _ownership_store(tmp_path, clock, live)
    lease = first.acquire(
        resource_id="canonical_farm",
        role_id="farm",
        owner_id="owner-a",
        identity=_identity(),
        lease_seconds=10,
    )
    with pytest.raises(OwnershipConflictError):
        second.acquire(
            resource_id="canonical_farm",
            role_id="farm",
            owner_id="owner-b",
            identity=_identity(202),
            lease_seconds=10,
        )
    first.release(lease)
    live[101] = None
    next_lease = second.acquire(
        resource_id="canonical_farm",
        role_id="farm",
        owner_id="owner-b",
        identity=_identity(202),
        lease_seconds=10,
    )
    assert next_lease.fencing_token == lease.fencing_token + 1
    live[202] = _identity(202)
    second.close()
    reopened = _ownership_store(tmp_path, clock, live)
    reopened.release(next_lease)
    third = reopened.acquire(
        resource_id="canonical_farm",
        role_id="farm",
        owner_id="owner-c",
        identity=_identity(303),
        lease_seconds=10,
    )
    assert third.fencing_token == next_lease.fencing_token + 1


def test_expired_but_still_alive_owner_cannot_be_stolen_or_mutate(tmp_path) -> None:
    clock = Clock()
    live = {101: _identity()}
    owner = _ownership_store(tmp_path, clock, live)
    lease = owner.acquire(
        resource_id="canonical_farm", role_id="farm", owner_id="a",
        identity=_identity(), lease_seconds=5,
    )
    clock.value = 106.0
    with pytest.raises(StaleProcessLeaseError):
        owner.renew(lease)
    with pytest.raises(StaleProcessLeaseError):
        owner.release(lease)
    contender = _ownership_store(tmp_path, clock, live)
    with pytest.raises(OwnershipConflictError, match="expired_alive_conflict"):
        contender.acquire(
            resource_id="canonical_farm", role_id="farm", owner_id="b",
            identity=_identity(202), lease_seconds=5,
        )


def test_non_owner_cannot_acknowledge_stop_intent_or_release(tmp_path) -> None:
    clock = Clock()
    live = {101: _identity()}
    store = _ownership_store(tmp_path, clock, live)
    lease = store.acquire(
        resource_id="canonical_farm", role_id="farm", owner_id="a",
        identity=_identity(), lease_seconds=10,
    )
    stop = tmp_path / "STOP_FARM_FULL_CYCLE.txt"
    stop.write_text("stop", encoding="utf-8")
    forged = lease.replace(owner_id="b")
    with pytest.raises(StaleProcessLeaseError):
        store.acknowledge_stop_intent(forged, stop)
    assert stop.exists()
    with pytest.raises(StaleProcessLeaseError):
        store.release(forged)
    assert store.is_authoritative(lease)


def test_corrupt_lease_state_fails_closed_without_resetting_fence(tmp_path) -> None:
    clock = Clock()
    live = {101: _identity()}
    store = _ownership_store(tmp_path, clock, live)
    lease = store.acquire(
        resource_id="canonical_farm", role_id="farm", owner_id="a",
        identity=_identity(), lease_seconds=10,
    )
    store.raw_connection.execute(
        "UPDATE ownership_resources SET executable='' WHERE resource_id='canonical_farm'"
    )
    store.raw_connection.commit()
    with pytest.raises(OwnershipConflictError, match="corrupt"):
        store.acquire(
            resource_id="canonical_farm", role_id="farm", owner_id="b",
            identity=_identity(202), lease_seconds=10,
        )
    row = store.raw_connection.execute(
        "SELECT next_fence FROM ownership_resources WHERE resource_id='canonical_farm'"
    ).fetchone()
    assert int(row[0]) == lease.fencing_token


def _claim_brain_task(path, owner: str, barrier=None):
    db = FarmTasksDB(path, owner_id=owner, lease_seconds=10, clock=lambda: 100.0)
    if barrier is not None:
        barrier()
    try:
        return db.claim_next_task(now=100.0)
    finally:
        db.close()


def test_two_brain_connections_claim_one_task_once(tmp_path) -> None:
    path = tmp_path / "farm.sqlite"
    seed = FarmTasksDB(path, owner_id="seed", clock=lambda: 100.0)
    seed.enqueue_task(task_type="run_sweep", task_key="one", now=100.0)
    seed.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda owner: _claim_brain_task(path, owner), ("a", "b")))
    claimed = [row for row in rows if row is not None]
    assert len(claimed) == 1
    assert claimed[0]["claim_owner"] in {"a", "b"}
    assert claimed[0]["fencing_token"] == 1


def test_expired_brain_claim_rejects_late_transition_before_and_after_takeover(tmp_path) -> None:
    path = tmp_path / "farm.sqlite"
    clock = Clock()
    old = FarmTasksDB(path, owner_id="old", lease_seconds=5, clock=clock)
    task_id, _ = old.enqueue_task(task_type="run_sweep", task_key="one", now=clock())
    claim = old.claim_next_task(now=clock())
    assert claim and claim["task_id"] == task_id
    clock.value = 106.0
    with pytest.raises(StaleTaskClaimError):
        old.complete_task(task_id, now=100.0)  # stale cached cycle timestamp
    with pytest.raises(StaleTaskClaimError):
        old.fail_task(task_id, "late", now=clock())
    with pytest.raises(StaleTaskClaimError):
        old.materialize_task(task_id, 7, now=clock())
    new = FarmTasksDB(path, owner_id="new", lease_seconds=5, clock=clock)
    assert new.reconcile_orphan_running(now=clock()) == 1
    takeover = new.claim_next_task(now=clock())
    assert takeover and takeover["fencing_token"] == claim["fencing_token"] + 1
    with pytest.raises(StaleTaskClaimError):
        old.complete_task(task_id, now=clock())


def test_brain_transition_and_audit_commit_together(tmp_path) -> None:
    db = FarmTasksDB(tmp_path / "farm.sqlite", owner_id="owner", clock=lambda: 100.0)
    task_id, _ = db.enqueue_task(task_type="run_sweep", task_key="one", now=100.0)
    db.claim_next_task(now=100.0)
    db.complete_task(task_id, reason="done", now=101.0)
    transition = db.raw_connection.execute(
        "SELECT to_state, owner_id, fencing_token FROM task_transitions "
        "WHERE task_id=? ORDER BY transition_id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    assert tuple(transition) == ("completed", "owner", 1)


def test_v2_task_triggers_reject_legacy_update(tmp_path) -> None:
    db = FarmTasksDB(tmp_path / "farm.sqlite", owner_id="owner", clock=lambda: 100.0)
    task_id, _ = db.enqueue_task(task_type="run_sweep", task_key="one", now=100.0)
    with pytest.raises(sqlite3.IntegrityError, match="fenced v2 writer"):
        db.raw_connection.execute(
            "UPDATE tasks SET state='running', attempts=attempts+1, updated_at=? "
            "WHERE task_id=?",
            (100.0, task_id),
        )


def test_public_brain_api_cannot_materialize_without_a_claim(tmp_path) -> None:
    db = FarmTasksDB(tmp_path / "farm.sqlite", owner_id="owner", clock=lambda: 100.0)
    task_id, _ = db.enqueue_task(
        task_type="run_sweep", task_key="one", now=100.0
    )
    with pytest.raises(StaleTaskClaimError, match="requires a fenced claim"):
        db.materialize_task(task_id, 7, now=100.0)


def _compute_db(tmp_path, *, clock=lambda: 100.0):
    conn = connect(tmp_path / "strategy.sqlite", clock=clock)
    init_db(conn)
    return conn


def test_compute_claim_attempt_survives_ambiguous_reap_and_rejects_late_completion(tmp_path) -> None:
    conn = _compute_db(tmp_path)
    job_id = enqueue_experiment(conn, tmp_path / "spec.json")
    first = claim_next_job(conn, owner_id="worker-a", lease_seconds=5, now=100.0)
    assert first and first["job_id"] == job_id
    mark_job_executing(
        conn, job_id, owner_id="worker-a",
        fencing_token=first["fencing_token"], now=101.0,
    )
    assert reap_stale_jobs(conn, now=106.0) == 1
    second = claim_next_job(conn, owner_id="worker-b", lease_seconds=5, now=106.0)
    assert second and second["fencing_token"] == first["fencing_token"] + 1
    attempts = conn.execute(
        "SELECT owner_id, fencing_token, state FROM job_attempts ORDER BY fencing_token"
    ).fetchall()
    assert [tuple(row) for row in attempts] == [
        ("worker-a", first["fencing_token"], "ambiguous"),
        ("worker-b", second["fencing_token"], "claimed"),
    ]
    with pytest.raises(StaleJobClaimError):
        complete_job(
            conn, job_id, "late-run", owner_id="worker-a",
            fencing_token=first["fencing_token"], now=106.0,
        )


def test_expired_compute_claim_cannot_renew_complete_or_fail_before_takeover(tmp_path) -> None:
    clock = Clock()
    conn = _compute_db(tmp_path, clock=clock)
    job_id = enqueue_experiment(conn, tmp_path / "spec.json")
    claim = claim_next_job(conn, owner_id="worker-a", lease_seconds=5, now=100.0)
    assert claim
    kwargs = {
        "owner_id": "worker-a",
        "fencing_token": claim["fencing_token"],
        "now": 100.0,
    }
    clock.value = 106.0
    with pytest.raises(StaleJobClaimError):
        renew_job_lease(conn, job_id, lease_seconds=5, **kwargs)
    with pytest.raises(StaleJobClaimError):
        complete_job(conn, job_id, "late", **kwargs)
    with pytest.raises(StaleJobClaimError):
        fail_job(conn, job_id, "late", **kwargs)


def test_v2_compute_triggers_reject_legacy_update(tmp_path) -> None:
    conn = _compute_db(tmp_path)
    job_id = enqueue_experiment(conn, tmp_path / "spec.json")
    with pytest.raises(sqlite3.IntegrityError, match="fenced v2 writer"):
        conn.execute(
            "UPDATE queue SET status='running', started_at=?, attempts=attempts+1 "
            "WHERE job_id=? AND status='queued'",
            ("2026-07-18T00:00:00+00:00", job_id),
        )


def test_recovered_heartbeat_and_port_processes_are_display_only(tmp_path, monkeypatch) -> None:
    heartbeat = tmp_path / "heartbeat.json"
    heartbeat.write_text(json.dumps({
        "contours": {"farm": {"pid": 101, "started_at": 10.0}},
    }), encoding="utf-8")
    monkeypatch.setattr(control_center, "_process_started_at", lambda pid: 10.0)
    rows = control_center._load_external_contours(heartbeat)
    assert rows["farm"]["stoppable"] is False
    assert rows["farm"]["authority"] == "display_only"
    port_row = control_center._external_process_descriptor(
        key="ollama", pid=202, started_at=20.0,
        executable="C:/Program Files/Ollama/ollama.exe",
        executable_matches=True, owned_child=False,
    )
    assert port_row["stoppable"] is False
    assert port_row["authority"] == "display_only"


def test_owner_group_preflight_rejects_entire_conflicting_request() -> None:
    specs = control_center.contour_specs()
    with pytest.raises(ValueError, match="owner group"):
        control_center.validate_owner_group_start(specs, ("farm", "paper_cards"))


def test_process_identity_mismatch_after_expiry_fails_closed(tmp_path) -> None:
    clock = Clock()
    original = _identity()
    live = {101: original}
    store = _ownership_store(tmp_path, clock, live)
    store.acquire(
        resource_id="canonical_farm", role_id="farm", owner_id="a",
        identity=original, lease_seconds=5,
    )
    clock.value = 106.0
    live[101] = ProcessIdentity(
        pid=101, started_at=11.0, executable=original.executable,
        command_digest=original.command_digest,
    )
    with pytest.raises(OwnershipConflictError, match="identity_mismatch"):
        store.acquire(
            resource_id="canonical_farm", role_id="farm", owner_id="b",
            identity=_identity(202), lease_seconds=5,
        )
    assert store.status("canonical_farm")["state"] == "identity_mismatch"


def _seed_persisted_owner(
    store: OwnershipStore,
    identity: ProcessIdentity,
    *,
    expires_at: float,
    fence: int = 7,
) -> None:
    store.raw_connection.execute(
        """INSERT INTO ownership_resources(
               resource_id,role_id,owner_id,pid,started_at,executable,
               command_digest,lease_expires_at,next_fence,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            "canonical_farm", "farm", "old", identity.pid,
            identity.started_at, identity.executable, identity.command_digest,
            expires_at, fence, 100.0,
        ),
    )
    store.raw_connection.commit()


def test_expired_owner_before_current_boot_reclaims_when_pid_probe_is_denied(
    tmp_path, monkeypatch
) -> None:
    clock = Clock(1_000.0)
    original = _identity()
    calls = 0

    def denied(_pid: int):
        nonlocal calls
        calls += 1
        raise OwnershipConflictError("process identity probe failed")

    monkeypatch.setattr(ownership_module, "_system_boot_time", lambda: 500.0)
    store = OwnershipStore(
        tmp_path / "ownership.sqlite", clock=clock, identity_probe=denied
    )
    _seed_persisted_owner(store, original, expires_at=100.0)

    lease = store.acquire(
        resource_id="canonical_farm", role_id="farm", owner_id="new",
        identity=_identity(202), lease_seconds=90.0,
    )

    assert calls == 1
    assert lease.fencing_token == 8
    assert lease.owner_id == "new"
    store.close()


def test_same_boot_probe_failure_remains_fail_closed(tmp_path, monkeypatch) -> None:
    clock = Clock(1_000.0)
    original = ProcessIdentity(
        pid=101,
        started_at=600.0,
        executable="C:/Python/python.exe",
        command_digest="sha256:farm-command",
    )
    monkeypatch.setattr(ownership_module, "_system_boot_time", lambda: 500.0)

    def denied(_pid: int):
        raise OwnershipConflictError("process identity probe failed")

    store = OwnershipStore(
        tmp_path / "ownership.sqlite", clock=clock, identity_probe=denied
    )
    _seed_persisted_owner(store, original, expires_at=100.0)

    with pytest.raises(OwnershipConflictError, match="process identity probe failed"):
        store.acquire(
            resource_id="canonical_farm", role_id="farm", owner_id="new",
            identity=_identity(202), lease_seconds=90.0,
        )
    row = store.raw_connection.execute(
        "SELECT owner_id,next_fence FROM ownership_resources WHERE resource_id=?",
        ("canonical_farm",),
    ).fetchone()
    assert tuple(row) == ("old", 7)
    store.close()


def test_unexpired_preboot_probe_failure_remains_fail_closed(
    tmp_path, monkeypatch
) -> None:
    clock = Clock(1_000.0)
    original = _identity()
    monkeypatch.setattr(ownership_module, "_system_boot_time", lambda: 500.0)

    def denied(_pid: int):
        raise OwnershipConflictError("process identity probe failed")

    store = OwnershipStore(
        tmp_path / "ownership.sqlite", clock=clock, identity_probe=denied
    )
    _seed_persisted_owner(store, original, expires_at=1_100.0)

    with pytest.raises(OwnershipConflictError, match="process identity probe failed"):
        store.acquire(
            resource_id="canonical_farm", role_id="farm", owner_id="new",
            identity=_identity(202), lease_seconds=90.0,
        )
    store.close()


def test_compute_lease_renewal_prevents_age_only_reap(tmp_path) -> None:
    conn = _compute_db(tmp_path)
    job_id = enqueue_experiment(conn, tmp_path / "spec.json")
    claim = claim_next_job(
        conn, owner_id="worker-a", lease_seconds=10, now=100.0
    )
    assert claim
    conn.execute(
        "UPDATE queue SET started_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",
        (job_id,),
    )
    conn.commit()
    assert reap_stale_jobs(conn, max_age_seconds=1, now=105.0) == 0
    renew_job_lease(
        conn, job_id, owner_id="worker-a",
        fencing_token=claim["fencing_token"], lease_seconds=10, now=105.0,
    )
    assert reap_stale_jobs(conn, max_age_seconds=1, now=111.0) == 0


def test_compute_reaper_never_uses_started_at_when_lease_is_live(tmp_path) -> None:
    conn = _compute_db(tmp_path)
    job_id = enqueue_experiment(conn, tmp_path / "spec.json")
    claim = claim_next_job(
        conn, owner_id="worker-a", lease_seconds=3600, now=1_000_000_000.0
    )
    assert claim
    conn.execute(
        "UPDATE queue SET started_at='2000-01-01T00:00:00+00:00' WHERE job_id=?",
        (job_id,),
    )
    conn.commit()
    assert reap_stale_jobs(conn, max_age_seconds=1, now=1_000_000_001.0) == 0
    assert conn.execute(
        "SELECT status FROM queue WHERE job_id=?", (job_id,)
    ).fetchone()[0] == "running"


def test_compute_terminal_mutation_requires_exact_active_claim(tmp_path) -> None:
    conn = _compute_db(tmp_path)
    job_id = enqueue_experiment(conn, tmp_path / "spec.json")
    with pytest.raises(TypeError):
        complete_job(conn, job_id, "run")
    with pytest.raises(StaleJobClaimError):
        fail_job(
            conn, job_id, "forged", owner_id="worker-a", fencing_token=1,
            now=100.0,
        )
    assert conn.execute(
        "SELECT status FROM queue WHERE job_id=?", (job_id,)
    ).fetchone()[0] == "queued"


def test_content_bound_materialization_replays_same_job_and_acknowledges(tmp_path) -> None:
    brain = FarmTasksDB(tmp_path / "farm.sqlite", owner_id="farm", clock=lambda: 100.0)
    task_id, _ = brain.enqueue_task(
        task_type="run_sweep", task_key="one", now=100.0
    )
    task = brain.claim_next_task(now=100.0)
    assert task
    payload = json.dumps({"experiment_id": "e"}, sort_keys=True) + "\n"
    digest = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    spec = tmp_path / "spec.json"
    materialization_id = f"task:{task_id}:fence:{task['fencing_token']}"
    brain.prepare_materialization(
        task_id,
        materialization_id=materialization_id,
        spec_path=str(spec),
        spec_digest=digest,
        spec_json=payload,
        priority=10,
        now=100.0,
    )
    assert brain.pending_materializations()[0]["state"] == "pending"
    spec.write_text(payload, encoding="utf-8")
    compute = _compute_db(tmp_path)
    first, created = ensure_experiment_queued(
        compute, spec, priority=10,
        materialization_id=materialization_id,
        materialization_digest=digest,
    )
    second, created_again = ensure_experiment_queued(
        compute, spec, priority=10,
        materialization_id=materialization_id,
        materialization_digest=digest,
    )
    assert (first, created, second, created_again) == (first, True, first, False)
    shared, shared_created = ensure_experiment_queued(
        compute, spec, priority=10,
        materialization_id="second-task-fence",
        materialization_digest=digest,
    )
    assert (shared, shared_created) == (first, False)
    assert compute.execute(
        "SELECT COUNT(*) FROM queue_materializations WHERE job_id=?", (first,)
    ).fetchone()[0] == 2
    with pytest.raises(ValueError, match="different spec content"):
        ensure_experiment_queued(
            compute, tmp_path / "other.json", priority=10,
            materialization_id=materialization_id,
            materialization_digest=digest,
        )
    brain.mark_materialization_dispatched(materialization_id, first, now=100.0)
    brain.commit_materialization(
        task_id,
        materialization_id=materialization_id,
        queue_job_id=first,
        now=100.0,
    )
    outbox = brain.raw_connection.execute(
        "SELECT state, queue_job_id, spec_json FROM materialization_outbox"
    ).fetchone()
    assert tuple(outbox) == ("acknowledged", first, "")


def test_acknowledged_payload_release_is_verified_bounded_and_idempotent(
    tmp_path,
) -> None:
    private_root = tmp_path / "private"
    state = private_root / "state"
    state.mkdir(parents=True)
    spec = private_root / "plans" / "event_specs" / "spec.json"
    spec.parent.mkdir(parents=True)
    payload = json.dumps({"experiment_id": "e"}, sort_keys=True) + "\n"
    digest = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    spec.write_text(payload, encoding="utf-8")
    brain = FarmTasksDB(
        state / "farm_tasks.sqlite", owner_id="farm", clock=lambda: 100.0
    )
    task_id, _ = brain.enqueue_task(
        task_type="run_sweep", task_key="release", now=100.0
    )
    task = brain.claim_next_task(now=100.0)
    assert task
    materialization_id = f"task:{task_id}:fence:{task['fencing_token']}"
    brain.prepare_materialization(
        task_id,
        materialization_id=materialization_id,
        spec_path=str(spec.resolve()),
        spec_digest=digest,
        spec_json=payload,
        priority=10,
        now=100.0,
    )
    compute = _compute_db(private_root)
    job_id, _ = ensure_experiment_queued(
        compute,
        spec,
        materialization_id=materialization_id,
        materialization_digest=digest,
    )
    brain.mark_materialization_dispatched(materialization_id, job_id, now=100.0)
    brain.commit_materialization(
        task_id,
        materialization_id=materialization_id,
        queue_job_id=job_id,
        now=100.0,
    )
    # Recreate one legacy acknowledged row as the operational migration input.
    brain.raw_connection.execute(
        "UPDATE materialization_outbox SET spec_json=? WHERE materialization_id=?",
        (payload, materialization_id),
    )
    brain.raw_connection.commit()

    dry = brain.release_acknowledged_materialization_payloads()
    spec.write_text(payload + "tamper", encoding="utf-8")
    with pytest.raises(
        ValueError, match="spec artifact digest mismatch"
    ):
        brain.release_acknowledged_materialization_payloads(
            apply=True,
            expected_plan_digest=dry["plan_digest"],
        )
    assert brain.raw_connection.execute(
        "SELECT LENGTH(spec_json) FROM materialization_outbox WHERE materialization_id=?",
        (materialization_id,),
    ).fetchone()[0] == len(payload)
    spec.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match="plan changed"):
        brain.release_acknowledged_materialization_payloads(
            apply=True,
            expected_plan_digest="sha256:" + ("0" * 64),
        )
    assert brain.raw_connection.execute(
        "SELECT LENGTH(spec_json) FROM materialization_outbox WHERE materialization_id=?",
        (materialization_id,),
    ).fetchone()[0] == len(payload)
    applied = brain.release_acknowledged_materialization_payloads(
        apply=True,
        expected_plan_digest=dry["plan_digest"],
    )
    repeat_dry = brain.release_acknowledged_materialization_payloads()
    compacted = brain.release_acknowledged_materialization_payloads(
        apply=True,
        expected_plan_digest=repeat_dry["plan_digest"],
        compact=True,
    )
    repeated = brain.release_acknowledged_materialization_payloads()

    assert dry == {
        "schema": "MaterializationPayloadRelease.v1",
        "mode": "dry_run",
        "eligible_rows": 1,
        "released_payload_bytes": len(payload.encode()),
        "plan_digest": dry["plan_digest"],
        "storage_compacted": False,
    }
    assert applied == {**dry, "mode": "apply"}
    assert compacted == {
        **repeat_dry,
        "mode": "apply",
        "storage_compacted": True,
    }
    assert repeated["eligible_rows"] == 0
    assert repeated["released_payload_bytes"] == 0
    assert brain.raw_connection.execute(
        "SELECT spec_json FROM materialization_outbox WHERE materialization_id=?",
        (materialization_id,),
    ).fetchone()[0] == ""
    brain.close()
    assert run_payload_release(
        private_root=private_root,
        apply=False,
    ) == repeated


def test_same_spec_path_never_reuses_a_different_content_generation(tmp_path) -> None:
    compute = _compute_db(tmp_path)
    spec = tmp_path / "same-path.json"
    payload_a = json.dumps({"experiment_id": "a"}, sort_keys=True) + "\n"
    digest_a = "sha256:" + hashlib.sha256(payload_a.encode()).hexdigest()
    spec.write_text(payload_a, encoding="utf-8")
    job_a, created_a = ensure_experiment_queued(
        compute, spec, materialization_id="fence-a",
        materialization_digest=digest_a,
    )
    assert created_a is True
    claim = claim_next_job(
        compute, owner_id="worker-a", lease_seconds=10, now=100.0
    )
    assert claim and claim["job_id"] == job_a
    complete_job(
        compute, job_a, "experiments/completed/run-a", owner_id="worker-a",
        fencing_token=claim["fencing_token"], now=101.0,
    )

    payload_b = json.dumps({"experiment_id": "b"}, sort_keys=True) + "\n"
    digest_b = "sha256:" + hashlib.sha256(payload_b.encode()).hexdigest()
    spec.write_text(payload_b, encoding="utf-8")
    job_b, created_b = ensure_experiment_queued(
        compute, spec, materialization_id="fence-b",
        materialization_digest=digest_b,
    )
    assert created_b is True
    assert job_b != job_a
    bindings = compute.execute(
        "SELECT materialization_id, job_id, spec_digest FROM queue_materializations "
        "ORDER BY materialization_id"
    ).fetchall()
    assert [tuple(row) for row in bindings] == [
        ("fence-a", job_a, digest_a),
        ("fence-b", job_b, digest_b),
    ]

    payload_c = json.dumps({"experiment_id": "c"}, sort_keys=True) + "\n"
    digest_c = "sha256:" + hashlib.sha256(payload_c.encode()).hexdigest()
    spec.write_text(payload_c, encoding="utf-8")
    with pytest.raises(ValueError, match="active spec path"):
        ensure_experiment_queued(
            compute, spec, materialization_id="fence-c",
            materialization_digest=digest_c,
        )


def test_expired_outbox_intent_has_no_filesystem_or_compute_side_effect(tmp_path) -> None:
    clock = Clock()
    brain = FarmTasksDB(
        tmp_path / "farm.sqlite", owner_id="farm", lease_seconds=5, clock=clock
    )
    task_id, _ = brain.enqueue_task(
        task_type="run_sweep", task_key="one", now=100.0
    )
    claim = brain.claim_next_task(now=100.0)
    assert claim
    payload = json.dumps({"experiment_id": "e"}, sort_keys=True) + "\n"
    digest = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    spec = tmp_path / "specs" / "expired.json"
    brain.prepare_materialization(
        task_id,
        materialization_id="expired-intent",
        spec_path=str(spec),
        spec_digest=digest,
        spec_json=payload,
        priority=10,
        now=100.0,
    )
    compute = _compute_db(tmp_path)
    clock.value = 106.0
    assert _replay_materialization_outbox(brain, compute, now=100.0) == 0
    assert not spec.exists()
    assert compute.execute("SELECT COUNT(*) FROM queue").fetchone()[0] == 0


def _provisional_run(tmp_path):
    path = tmp_path / "experiments" / "provisional" / "run-one"
    path.mkdir(parents=True)
    (path / "metrics.json").write_text(
        json.dumps({"experiment_id": "e", "created_at": "", "results": []}),
        encoding="utf-8",
    )
    (path / "publication_generation.json").write_text(
        json.dumps({
            "schema": "strategy_lab_publication_generation.v1",
            "job_id": 1,
            "owner_id": "worker-a",
            "fencing_token": 1,
        }),
        encoding="utf-8",
    )
    return path


def test_stale_worker_cannot_publish_provisional_run(tmp_path) -> None:
    conn = _compute_db(tmp_path)
    job_id = enqueue_experiment(conn, tmp_path / "spec.json")
    claim = claim_next_job(
        conn, owner_id="worker-a", lease_seconds=5, now=100.0
    )
    assert claim
    provisional = _provisional_run(tmp_path)
    with pytest.raises(StaleJobClaimError):
        publish_completed_job(
            conn, tmp_path, provisional, job_id=job_id,
            owner_id="worker-a", fencing_token=claim["fencing_token"],
            now=106.0,
        )
    assert provisional.exists()
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM queue").fetchone()[0] == "running"


def test_provisional_generation_must_match_owner_and_fence(tmp_path) -> None:
    conn = _compute_db(tmp_path)
    job_id = enqueue_experiment(conn, tmp_path / "spec.json")
    claim = claim_next_job(
        conn, owner_id="worker-a", lease_seconds=10, now=100.0
    )
    assert claim
    provisional = _provisional_run(tmp_path)
    marker = provisional / "publication_generation.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["owner_id"] = "forged-worker"
    marker.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(StaleJobClaimError, match="does not match"):
        publish_completed_job(
            conn, tmp_path, provisional, job_id=job_id,
            owner_id="worker-a", fencing_token=claim["fencing_token"],
            now=101.0,
        )
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_fenced_publication_imports_and_completes_in_one_generation(tmp_path) -> None:
    conn = _compute_db(tmp_path)
    job_id = enqueue_experiment(conn, tmp_path / "spec.json")
    claim = claim_next_job(
        conn, owner_id="worker-a", lease_seconds=10, now=100.0
    )
    assert claim
    provisional = _provisional_run(tmp_path)
    final_dir, imported = publish_completed_job(
        conn, tmp_path, provisional, job_id=job_id,
        owner_id="worker-a", fencing_token=claim["fencing_token"],
        now=101.0,
    )
    assert imported == 0
    assert final_dir.exists() and not provisional.exists()
    assert conn.execute("SELECT status FROM queue").fetchone()[0] == "completed"
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert conn.execute("SELECT state FROM artifact_publications").fetchone()[0] == "directory_published"
    mark_publication_indexes_published(
        conn, job_id, claim["fencing_token"], now=101.0
    )
    assert conn.execute("SELECT state FROM artifact_publications").fetchone()[0] == "published"


def test_pending_rename_is_recovered_without_reclaim_or_reimport(tmp_path, monkeypatch) -> None:
    conn = _compute_db(tmp_path)
    job_id = enqueue_experiment(conn, tmp_path / "spec.json")
    claim = claim_next_job(
        conn, owner_id="worker-a", lease_seconds=10, now=100.0
    )
    assert claim
    provisional = _provisional_run(tmp_path)
    original_replace = Path.replace

    def _rename_fails_once(self, target):
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(Path, "replace", _rename_fails_once)
    with pytest.raises(OSError, match="synthetic rename failure"):
        publish_completed_job(
            conn, tmp_path, provisional, job_id=job_id,
            owner_id="worker-a", fencing_token=claim["fencing_token"],
            now=101.0,
        )
    assert conn.execute(
        "SELECT status FROM queue WHERE job_id=?", (job_id,)
    ).fetchone()[0] == "completed"
    assert conn.execute(
        "SELECT state FROM artifact_publications"
    ).fetchone()[0] == "pending_rename"
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1

    monkeypatch.setattr(Path, "replace", original_replace)
    assert recover_pending_publications(conn, tmp_path, now=102.0) == 1
    final_dir = tmp_path / "experiments" / "completed" / provisional.name
    assert final_dir.exists() and not provisional.exists()
    assert conn.execute(
        "SELECT state FROM artifact_publications"
    ).fetchone()[0] == "directory_published"
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_v2_insert_triggers_reject_legacy_task_and_queue_writers(tmp_path) -> None:
    brain = FarmTasksDB(tmp_path / "farm.sqlite", owner_id="farm")
    with pytest.raises(sqlite3.IntegrityError, match="fenced v2 writer"):
        brain.raw_connection.execute(
            """INSERT INTO tasks(
                   task_key, task_type, state, created_at, updated_at)
               VALUES('legacy','run_sweep','queued',1,1)"""
        )
    compute = _compute_db(tmp_path)
    with pytest.raises(sqlite3.IntegrityError, match="fenced v2 writer"):
        compute.execute(
            """INSERT INTO queue(spec_path, status, created_at)
               VALUES('legacy.json','queued','2026-01-01')"""
        )


def test_runtime_preflight_does_not_change_legacy_journal_mode(tmp_path) -> None:
    compute_path = tmp_path / "legacy-compute.sqlite"
    raw = sqlite3.connect(str(compute_path))
    raw.execute(
        "CREATE TABLE queue(job_id INTEGER PRIMARY KEY, status TEXT)"
    )
    raw.commit()
    assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    raw.close()
    with pytest.raises(FencingMigrationRequired):
        connect(compute_path)
    verify = sqlite3.connect(str(compute_path))
    assert verify.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    verify.close()

    brain_path = tmp_path / "legacy-brain.sqlite"
    raw = sqlite3.connect(str(brain_path))
    raw.execute(
        "CREATE TABLE tasks(task_id INTEGER PRIMARY KEY, state TEXT)"
    )
    raw.commit()
    assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    raw.close()
    with pytest.raises(FarmFencingMigrationRequired):
        FarmTasksDB(brain_path)
    verify = sqlite3.connect(str(brain_path))
    assert verify.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    verify.close()


def test_legacy_running_is_quarantined_while_queued_and_terminal_are_preserved(tmp_path) -> None:
    brain_path = tmp_path / "farm.sqlite"
    brain = FarmTasksDB(brain_path, owner_id="seed")
    running, _ = brain.enqueue_task(
        task_type="run_sweep", task_key="running", state="running", now=1.0
    )
    queued, _ = brain.enqueue_task(
        task_type="run_sweep", task_key="queued", state="queued", now=1.0
    )
    terminal, _ = brain.enqueue_task(
        task_type="run_sweep", task_key="done", state="completed", now=1.0
    )
    brain.raw_connection.execute("DROP TRIGGER tasks_fenced_v2_guard")
    brain.raw_connection.execute("DROP TRIGGER tasks_fenced_v2_insert_guard")
    brain.raw_connection.execute("UPDATE tasks SET mutation_protocol='legacy.v1'")
    brain.raw_connection.execute(
        "DELETE FROM farm_meta WHERE key='fencing_protocol'"
    )
    brain.raw_connection.commit()
    brain.close()
    reader = FarmTasksDB(brain_path, read_only=True)
    assert reader.get_task(running)["state"] == "running"
    reader.close()
    with pytest.raises(FarmFencingMigrationRequired):
        FarmTasksDB(brain_path, owner_id="runtime")
    verify = sqlite3.connect(str(brain_path))
    assert verify.execute(
        "SELECT state FROM tasks WHERE task_id=?", (running,)
    ).fetchone()[0] == "running"
    verify.close()
    activate_farm_fencing_v2(brain_path, clock=lambda: 100.0)
    activated_brain = sqlite3.connect(str(brain_path))
    with pytest.raises(sqlite3.IntegrityError, match="fenced v2 writer"):
        activated_brain.execute(
            "UPDATE tasks SET state='completed' WHERE task_id=?", (queued,)
        )
    activated_brain.close()
    migrated = FarmTasksDB(brain_path, owner_id="new", clock=lambda: 100.0)
    assert migrated.get_task(running)["machine_reason"] == "legacy_running_unfenced"
    assert migrated.get_task(running)["state"] == "blocked"
    assert migrated.get_task(terminal)["state"] == "completed"
    claim = migrated.claim_next_task(now=100.0)
    assert claim and claim["task_id"] == queued and claim["fencing_token"] == 1

    compute_path = tmp_path / "strategy.sqlite"
    conn = connect(compute_path)
    init_db(conn)
    running_job = enqueue_experiment(conn, tmp_path / "running.json", status="running")
    queued_job = enqueue_experiment(conn, tmp_path / "queued.json", status="queued")
    terminal_job = enqueue_experiment(conn, tmp_path / "done.json", status="completed")
    conn.execute("DROP TRIGGER queue_fenced_v2_guard")
    conn.execute("DROP TRIGGER queue_fenced_v2_insert_guard")
    conn.execute("UPDATE queue SET mutation_protocol='legacy.v1'")
    conn.execute("DELETE FROM meta WHERE key='fencing_protocol'")
    conn.commit()
    conn.close()
    with pytest.raises(FencingMigrationRequired):
        connect(compute_path)
    legacy_compute = sqlite3.connect(str(compute_path))
    legacy_compute.row_factory = sqlite3.Row
    assert legacy_compute.execute(
        "SELECT status FROM queue WHERE job_id=?", (running_job,)
    ).fetchone()[0] == "running"
    activate_fencing_v2(legacy_compute)
    with pytest.raises(sqlite3.IntegrityError, match="fenced v2 writer"):
        legacy_compute.execute(
            "UPDATE queue SET status='completed' WHERE job_id=?", (queued_job,)
        )
    legacy_compute.close()
    migrated_compute = connect(compute_path, clock=lambda: 100.0)
    init_db(migrated_compute)
    states = {
        int(row["job_id"]): row["status"]
        for row in migrated_compute.execute("SELECT job_id, status FROM queue")
    }
    assert states[running_job] == "legacy_running_unfenced"
    assert states[terminal_job] == "completed"
    compute_claim = claim_next_job(
        migrated_compute, owner_id="worker", lease_seconds=10, now=100.0
    )
    assert compute_claim and compute_claim["job_id"] == queued_job
    assert compute_claim["fencing_token"] == 1
