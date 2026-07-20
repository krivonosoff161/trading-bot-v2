import json
import sqlite3
import threading

import pytest

from src.research_lab.ownership import ProcessIdentity
from src.research_lab.paper_evidence_store import (
    STAGES,
    PaperEvidenceConflict,
    PaperEvidenceStore,
    StalePaperWriter,
    _digest,
)


def _identity(pid: int) -> ProcessIdentity:
    return ProcessIdentity(pid, float(pid), f"python-{pid}.exe", f"sha256:cmd-{pid}")


def _store(tmp_path, clock):
    store = PaperEvidenceStore(tmp_path / "paper-evidence.sqlite3", clock=clock)
    store.activate()
    return store


def _lease(store, owner="owner-a", pid=101, seconds=30.0):
    return store.acquire_writer(owner_id=owner, identity=_identity(pid), lease_seconds=seconds)


def _account(store, lease, *, deposit=70.0, margin=35.0, parent=None):
    return store.create_account_genesis(
        lease,
        {
            "currency": "USDT",
            "deposit": deposit,
            "leverage": 3.0,
            "position_margin": margin,
            "allocation_policy": "one-primary-per-scenario.v1",
            "cost_policy": "net-pct-cost-inclusive.v1",
            "rounding_policy": "integer-microunits-half-even.v1",
            "method": "paper-account.v2",
        },
        parent_generation_id=parent,
    )


def _subject_payload(logical_id, **overrides):
    return {
        "runtime_id": f"runtime-{logical_id}",
        "source_member_payload_digest": _digest({"logical_id": logical_id}),
        "source_validation_generation_id": f"validation-{logical_id}",
        "simulator_manifest_id": "simulator-manifest-a",
        "method_identity": "paper-lifecycle.v2",
        "paper_only": True,
        "execution_allowed": False,
        **overrides,
    }


def _new_run(
    store,
    lease,
    *,
    source,
    subjects,
    producer_status="completed",
    subject_payloads=None,
):
    subject_payloads = subject_payloads or {}
    latest = store.connection.execute(
        "SELECT producer_generation_id,producer_sequence FROM producer_generations "
        "WHERE producer_id='synthetic-paper-producer' ORDER BY producer_sequence DESC LIMIT 1"
    ).fetchone()
    sequence = (int(latest["producer_sequence"]) if latest is not None else 0) + 1
    producer = store.register_producer_generation(
        lease,
        producer_id="synthetic-paper-producer",
        producer_sequence=sequence,
        members=[
            {
                "logical_id": logical_id,
                "payload_digest": subject_payloads.get(
                    logical_id, _subject_payload(logical_id)
                )["source_member_payload_digest"],
                "source_validation_generation_id": subject_payloads.get(
                    logical_id, _subject_payload(logical_id)
                )["source_validation_generation_id"],
                "disposition": "active",
            }
            for logical_id in subjects
        ],
        code_identity="sha256:synthetic-code",
        method_identity="synthetic-paper-producer.v2",
        status=producer_status,
        parent_generation_id=(
            str(latest["producer_generation_id"]) if latest is not None else None
        ),
    )
    run = store.create_run(lease, producer_generation_id=producer)
    return run, producer


def _begin_run(
    store,
    lease,
    *,
    source,
    subjects,
    producer_status="completed",
    subject_payloads=None,
):
    run, previous = _new_run(
        store,
        lease,
        source=source,
        subjects=subjects,
        producer_status=producer_status,
        subject_payloads=subject_payloads,
    )
    for stage in STAGES[:4]:
        output = f"sha256:{run}:{stage}"
        store.complete_stage(lease, run, stage, input_digest=previous, output_digest=output)
        previous = output
    return run, previous


def _finish_run(store, lease, run, previous, *, items=None):
    account_output = f"sha256:{run}:account"
    store.complete_stage(lease, run, "account", input_digest=previous, output_digest=account_output)
    store.prepare_projection(
        lease,
        run_id=run,
        projection_kind="trades",
        items=list(items or []),
        input_projection_digests={"account": account_output},
        target_path=store.path.with_name("main_paper_trades.json"),
    )
    projection_output = f"sha256:{run}:projection"
    store.complete_stage(
        lease,
        run,
        "projection",
        input_digest=account_output,
        output_digest=projection_output,
    )
    return store.finalize_run(lease, run)


def _subject(store, lease, run, logical_id="signal-a", *, payload=None, supersedes=None):
    return store.register_subject(
        lease,
        run_id=run,
        logical_id=logical_id,
        payload=payload or _subject_payload(logical_id),
        supersedes_generation_id=supersedes,
    )


def _observation(store, lease, run, subject, *, close=101.0, available_at=2_000.0):
    rows = [
        {"ts": 1_000, "open": 100.0, "high": 102.0, "low": 99.0, "close": close, "vol": 1.0}
    ]
    request = {"start_ts": 1_000, "end_ts": 1_000, "timeframe": "1h"}
    provider_identity = "synthetic-provider-a"
    acquisition_id = _digest(
        {
            "provider_identity": provider_identity,
            "request": request,
            "rows_digest": _digest(rows),
            "observed_at_ms": 1_500_000,
        }
    )
    return store.record_observation(
        lease,
        run_id=run,
        subject_generation_id=subject,
        rows=rows,
        request=request,
        observed_at=1_500.0,
        available_at=available_at,
        acquisition_id=acquisition_id,
        provider_identity=provider_identity,
        manifest_digest=_digest(
            {
                "schema": "CandleSnapshotManifest.v2",
                "request_digest": _digest(request),
                "rows_digest": _digest(rows),
                "observed_at_ms": 1_500_000,
                "available_at_ms": int(available_at * 1000),
                "provider_identity": provider_identity,
                "acquisition_id": acquisition_id,
            }
        ),
    )


def _plan(store, lease, run, subject, observation, event_type, account, payload, *, supersedes=None):
    return store.plan_lifecycle(
        lease,
        run_id=run,
        subject_generation_id=subject,
        observation_id=observation,
        event_type=event_type,
        payload=payload,
        account_generation_id=account,
        supersedes_event_id=supersedes,
    )


def _empty_current_run(store, lease, source="source-empty"):
    run, previous = _begin_run(store, lease, source=source, subjects=())
    _finish_run(store, lease, run, previous)
    return run


def test_direct_authority_bypasses_are_forbidden(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    with pytest.raises(PaperEvidenceConflict, match="direct lifecycle mutation"):
        store.apply_lifecycle()
    with pytest.raises(PaperEvidenceConflict, match="direct run promotion"):
        store.promote_run()
    with pytest.raises(PaperEvidenceConflict, match="direct projection publication"):
        store.publish_projection()


def test_writer_fence_is_checked_in_final_authority_transaction(tmp_path):
    now = [100.0]
    store = _store(tmp_path, lambda: now[0])
    old = _lease(store, seconds=5.0)
    account = _account(store, old)
    run, previous = _begin_run(store, old, source="source-a", subjects=("signal-a",))
    subject = _subject(store, old, run)
    observation = _observation(store, old, run, subject)
    _plan(store, old, run, subject, observation, "position_opened", account, {"scenario_id": "a"})
    account_output = f"sha256:{run}:account"
    store.complete_stage(old, run, "account", input_digest=previous, output_digest=account_output)
    store.prepare_projection(
        old,
        run_id=run,
        projection_kind="trades",
        items=[],
        input_projection_digests={"account": account_output},
        target_path=tmp_path / "trades.json",
    )
    store.complete_stage(old, run, "projection", input_digest=account_output, output_digest=f"sha256:{run}:projection")

    now[0] = 106.0
    current = _lease(store, owner="owner-b", pid=202)
    with pytest.raises(StalePaperWriter):
        store.finalize_run(old, run)

    result = store.finalize_run(current, run)
    assert result["applied_intents"][0]["account_event_type"] == "position_opened"


def test_two_connections_finalize_last_margin_once(tmp_path):
    now = [100.0]
    first = _store(tmp_path, lambda: now[0])
    lease = _lease(first)
    account = _account(first, lease, deposit=35.0, margin=35.0)
    run, previous = _begin_run(first, lease, source="source-race", subjects=("a", "b"))
    subjects = [_subject(first, lease, run, logical_id=value) for value in ("a", "b")]
    observations = [_observation(first, lease, run, subject) for subject in subjects]
    for subject, observation in zip(subjects, observations, strict=True):
        _plan(first, lease, run, subject, observation, "position_opened", account, {"scenario_id": subject})
    account_output = f"sha256:{run}:account"
    first.complete_stage(lease, run, "account", input_digest=previous, output_digest=account_output)
    first.prepare_projection(
        lease,
        run_id=run,
        projection_kind="trades",
        items=[],
        input_projection_digests={"account": account_output},
        target_path=tmp_path / "trades.json",
    )
    first.complete_stage(lease, run, "projection", input_digest=account_output, output_digest="sha256:projection")
    second = PaperEvidenceStore(first.path, clock=lambda: now[0])
    second.activate()
    barrier = threading.Barrier(2)
    results, errors = [], []

    def finalize(store):
        try:
            barrier.wait()
            results.append(store.finalize_run(lease, run))
        except Exception as exc:  # assertion captures any unexpected concurrency failure
            errors.append(exc)

    threads = [threading.Thread(target=finalize, args=(first,)), threading.Thread(target=finalize, args=(second,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 2
    replay = first.replay_account(account)
    assert replay["reserved_margin"] == 35.0
    assert replay["available_margin"] == 0.0
    types = [row["event_type"] for row in first.connection.execute("SELECT event_type FROM account_events ORDER BY account_seq")]
    assert types == ["position_opened", "allocation_rejected"]


def test_terminal_revision_preserves_close_and_appends_adjustment(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease)
    open_run, previous = _begin_run(store, lease, source="source-open", subjects=("signal-a",))
    subject = _subject(store, lease, open_run)
    first_obs = _observation(store, lease, open_run, subject)
    _plan(store, lease, open_run, subject, first_obs, "position_opened", account, {"scenario_id": "a"})
    _finish_run(store, lease, open_run, previous)

    close_run, previous = _begin_run(store, lease, source="source-close", subjects=("signal-a",))
    close_obs = _observation(store, lease, close_run, subject)
    _plan(store, lease, close_run, subject, close_obs, "position_closed", account, {"net_pct": 2.0})
    close_result = _finish_run(store, lease, close_run, previous)
    close_id = close_result["applied_intents"][0]["lifecycle_event_id"]

    revision_run, previous = _begin_run(store, lease, source="source-revision", subjects=("signal-a",))
    revised_obs = _observation(
        store, lease, revision_run, subject, close=100.5, available_at=3_000.0
    )
    _plan(
        store,
        lease,
        revision_run,
        subject,
        revised_obs,
        "outcome_revised",
        account,
        {"net_pct": 1.0},
        supersedes=close_id,
    )
    revision = _finish_run(store, lease, revision_run, previous)

    assert revision["applied_intents"][0]["account_event_type"] == "pnl_adjustment"
    assert [row["event_type"] for row in store.lifecycle_events(subject)] == [
        "position_opened",
        "position_closed",
        "outcome_revised",
    ]
    assert store.replay_account(account)["balance"] == 71.05


def test_account_model_change_requires_explicit_child_generation(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    original = _account(store, lease, deposit=70.0)
    with pytest.raises(PaperEvidenceConflict):
        _account(store, lease, deposit=100.0)
    child = _account(store, lease, deposit=100.0, parent=original)
    assert child != original
    assert store.replay_account(original)["balance"] == 70.0
    assert store.replay_account(child)["balance"] == 100.0


@pytest.mark.parametrize("producer_status", ["incomplete", "failed"])
def test_noncompleted_producer_generation_cannot_withdraw_subject(tmp_path, producer_status):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    run, previous = _begin_run(store, lease, source="source-active", subjects=("signal-a",))
    subject = _subject(store, lease, run)
    _finish_run(store, lease, run, previous)
    incomplete, _ = _begin_run(
        store,
        lease,
        source="source-incomplete",
        subjects=(),
        producer_status=producer_status,
    )
    with pytest.raises(PaperEvidenceConflict):
        store.finalize_run(lease, incomplete)
    assert store.subject(subject)["state"] == "active"


def test_digest_mismatched_or_stale_producer_generation_cannot_create_run(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    _, producer = _new_run(
        store,
        lease,
        source="source-producer-valid",
        subjects=("signal-a",),
    )
    store.connection.execute("DROP TRIGGER immutable_producer_generations_update")
    store.connection.execute(
        "UPDATE producer_generations SET manifest_json='{}' WHERE producer_generation_id=?",
        (producer,),
    )
    store.connection.commit()
    with pytest.raises(PaperEvidenceConflict, match="manifest/member mismatch"):
        store.create_run(lease, producer_generation_id=producer)

    fresh = _store(tmp_path / "fresh", lambda: 100.0)
    fresh_lease = _lease(fresh)
    first = fresh.register_producer_generation(
        fresh_lease,
        producer_id="producer-lineage",
        producer_sequence=1,
        members=[],
        code_identity="sha256:code",
        method_identity="producer.v2",
    )
    second = fresh.register_producer_generation(
        fresh_lease,
        producer_id="producer-lineage",
        producer_sequence=2,
        parent_generation_id=first,
        members=[],
        code_identity="sha256:code",
        method_identity="producer.v2",
    )
    with pytest.raises(PaperEvidenceConflict, match="immediate authenticated parent"):
        fresh.register_producer_generation(
            fresh_lease,
            producer_id="producer-lineage",
            producer_sequence=3,
            parent_generation_id=first,
            members=[],
            code_identity="sha256:code",
            method_identity="producer.v2",
        )
    assert second


@pytest.mark.parametrize("failed_stage", STAGES)
def test_failed_stage_cannot_apply_plans_or_replace_current_run(tmp_path, failed_stage):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease, deposit=70.0, margin=35.0)
    current, previous = _begin_run(
        store,
        lease,
        source="source-current",
        subjects=("close-subject", "drop-subject"),
    )
    close_subject = _subject(store, lease, current, logical_id="close-subject")
    drop_subject = _subject(store, lease, current, logical_id="drop-subject")
    for subject, scenario in ((close_subject, "close"), (drop_subject, "drop")):
        observation = _observation(store, lease, current, subject)
        _plan(
            store,
            lease,
            current,
            subject,
            observation,
            "position_opened",
            account,
            {"scenario_id": scenario},
        )
    _finish_run(store, lease, current, previous)
    candidate, previous = _new_run(
        store,
        lease,
        source=f"source-{failed_stage}",
        subjects=("close-subject",),
    )
    planned_close = False
    for stage in STAGES:
        if stage == failed_stage:
            store.fail_stage(lease, candidate, stage, reason="synthetic-stage-failure")
            break
        output = f"sha256:{candidate}:{stage}"
        if stage == "projection":
            store.prepare_projection(
                lease,
                run_id=candidate,
                projection_kind="trades",
                items=[],
                input_projection_digests={"account": previous},
                target_path=tmp_path / "candidate.json",
            )
        store.complete_stage(lease, candidate, stage, input_digest=previous, output_digest=output)
        previous = output
        if stage == "observer":
            observation = _observation(store, lease, candidate, close_subject)
            _plan(
                store,
                lease,
                candidate,
                close_subject,
                observation,
                "position_closed",
                account,
                {"net_pct": 2.0},
            )
            planned_close = True
    with pytest.raises(PaperEvidenceConflict):
        store.finalize_run(lease, candidate)
    assert store.current_run_id() == current
    assert store.subject(close_subject)["state"] == "active"
    assert store.subject(drop_subject)["state"] == "active"
    replay = store.replay_account(account)
    assert replay["events"] == 2
    assert replay["reserved_margin"] == 70.0
    assert int(
        store.connection.execute(
            "SELECT COUNT(*) AS n FROM paper_run_mutation_intents WHERE run_id=? AND status='applied'",
            (candidate,),
        ).fetchone()["n"]
    ) == 0
    assert planned_close is (STAGES.index(failed_stage) > STAGES.index("observer"))


def test_changed_active_subject_requires_explicit_supersession(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    first_run, previous = _begin_run(store, lease, source="source-a", subjects=("signal-a",))
    first = _subject(store, lease, first_run)
    _finish_run(store, lease, first_run, previous)
    changed_payload = _subject_payload("signal-a", method_identity="paper-lifecycle.v3")
    second_run, _ = _begin_run(
        store,
        lease,
        source="source-b",
        subjects=("signal-a",),
        subject_payloads={"signal-a": changed_payload},
    )
    with pytest.raises(PaperEvidenceConflict):
        _subject(store, lease, second_run, payload=changed_payload)
    second = _subject(
        store,
        lease,
        second_run,
        payload=changed_payload,
        supersedes=first,
    )
    assert second != first


def test_execution_boundary_is_enforced_by_store(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    run, _ = _begin_run(store, lease, source="source-a", subjects=("signal-a",))
    with pytest.raises(PaperEvidenceConflict):
        _subject(store, lease, run, payload={"paper_only": False, "execution_allowed": True})


@pytest.mark.parametrize(
    "unsafe_override",
    ({"paper_only": False}, {"execution_allowed": True}),
)
def test_account_genesis_rejects_unsafe_execution_flags(tmp_path, unsafe_override):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    config = {
        "currency": "USDT",
        "deposit": 70.0,
        "leverage": 3.0,
        "position_margin": 35.0,
        "allocation_policy": "one-primary-per-scenario.v1",
        "cost_policy": "net-pct-cost-inclusive.v1",
        "rounding_policy": "integer-microunits-half-even.v1",
        "method": "paper-account.v2",
        **unsafe_override,
    }

    with pytest.raises(ValueError, match="paper account genesis"):
        store.create_account_genesis(lease, config)

    assert store.connection.execute("SELECT COUNT(*) FROM account_geneses").fetchone()[0] == 0


def test_account_genesis_persists_canonical_paper_boundary(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    generation_id = _account(store, lease)
    row = store.connection.execute(
        "SELECT config_json,paper_only,execution_allowed FROM account_geneses "
        "WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    config = json.loads(row["config_json"])

    assert config["paper_only"] is True
    assert config["execution_allowed"] is False
    assert row["paper_only"] == 1
    assert row["execution_allowed"] == 0


def test_completed_projection_is_verified_from_read_only_database(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    run, previous = _begin_run(store, lease, source="source-projection", subjects=())
    _finish_run(store, lease, run, previous, items=[])
    loaded = PaperEvidenceStore.read_completed_projection(store.path, "trades", expected_run_id=run)
    assert loaded["current"] is True
    assert loaded["items"] == []


def test_export_crash_does_not_change_database_authority_or_pointer(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    run, previous = _begin_run(store, lease, source="source-export", subjects=())
    _finish_run(store, lease, run, previous)
    target = tmp_path / "trades.json"
    with pytest.raises(PaperEvidenceConflict, match="synthetic projection export crash"):
        store.export_completed_projection("trades", target, fail_after_generation_file=True)
    assert not target.exists()
    assert PaperEvidenceStore.read_completed_projection(store.path, "trades")["current"] is True


def test_tampered_projection_row_fails_closed(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    run, previous = _begin_run(store, lease, source="source-tamper", subjects=())
    _finish_run(store, lease, run, previous)
    store.connection.execute(
        "UPDATE projection_materializations SET envelope_json='{}' WHERE run_id=?", (run,)
    )
    store.connection.commit()
    loaded = PaperEvidenceStore.read_completed_projection(store.path, "trades")
    assert loaded["current"] is False
    assert loaded["generation_status"] == "digest_mismatch"


def test_read_only_projection_lookup_does_not_create_database(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    loaded = PaperEvidenceStore.read_completed_projection(missing, "trades")
    assert loaded["current"] is False
    assert loaded["display_only"] is True
    assert not missing.exists()


def test_round_robin_scheduler_serves_more_than_fifty_subjects(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    logical_ids = tuple(f"signal-{index:03d}" for index in range(75))
    run, previous = _begin_run(store, lease, source="source-many", subjects=logical_ids)
    subjects = [_subject(store, lease, run, logical_id=logical_id) for logical_id in logical_ids]
    _finish_run(store, lease, run, previous)
    first = store.schedule_subjects(lease, limit=50)
    second = store.schedule_subjects(lease, limit=50)
    assert set(first + second) == set(subjects)


def test_lifecycle_replay_rejects_broken_hash_chain(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease)
    run, previous = _begin_run(store, lease, source="source-chain", subjects=("signal-a",))
    subject = _subject(store, lease, run)
    observation = _observation(store, lease, run, subject)
    _plan(store, lease, run, subject, observation, "position_opened", account, {"scenario_id": "a"})
    _finish_run(store, lease, run, previous)
    store.connection.execute("DROP TRIGGER immutable_lifecycle_events_update")
    store.connection.execute(
        "UPDATE lifecycle_events SET prior_event_hash='forged' WHERE subject_generation_id=?",
        (subject,),
    )
    store.connection.commit()
    with pytest.raises(PaperEvidenceConflict, match="chain mismatch"):
        store.replay_lifecycle(subject)


def test_account_replay_rejects_broken_prior_hash(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease)
    run, previous = _begin_run(store, lease, source="source-account-chain", subjects=("signal-a",))
    subject = _subject(store, lease, run)
    observation = _observation(store, lease, run, subject)
    _plan(store, lease, run, subject, observation, "position_opened", account, {"scenario_id": "a"})
    _finish_run(store, lease, run, previous)
    store.connection.execute("DROP TRIGGER immutable_account_events_update")
    store.connection.execute(
        "UPDATE account_events SET prior_event_hash='forged' WHERE account_generation_id=?",
        (account,),
    )
    store.connection.commit()
    with pytest.raises(PaperEvidenceConflict, match="digest mismatch"):
        store.replay_account(account)


def test_account_replay_independently_recomputes_close_arithmetic(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease)
    run, previous = _begin_run(store, lease, source="source-account-math", subjects=("signal-a",))
    subject = _subject(store, lease, run)
    observation = _observation(store, lease, run, subject)
    _plan(store, lease, run, subject, observation, "position_opened", account, {"scenario_id": "a"})
    _plan(store, lease, run, subject, observation, "position_closed", account, {"net_pct": 10.0})
    _finish_run(store, lease, run, previous)

    store.connection.execute("DROP TRIGGER immutable_account_events_update")
    row = store.connection.execute(
        "SELECT * FROM account_events WHERE account_generation_id=? AND event_type='position_closed'",
        (account,),
    ).fetchone()
    payload = json.loads(row["payload_json"])
    payload["pnl_delta_microunits"] += 1
    payload_digest = _digest(payload)
    identity = {
        "account_generation_id": account,
        "account_seq": int(row["account_seq"]),
        "prior_event_hash": str(row["prior_event_hash"]),
        "event_type": str(row["event_type"]),
        "subject_generation_id": str(row["subject_generation_id"]),
        "lifecycle_event_id": str(row["lifecycle_event_id"]),
        "account_model_digest": str(row["account_model_digest"]),
        "payload_digest": payload_digest,
        "supersedes_account_event_id": str(row["supersedes_account_event_id"] or ""),
    }
    store.connection.execute(
        "UPDATE account_events SET payload_json=?,payload_digest=?,event_hash=? "
        "WHERE account_event_id=?",
        (
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            payload_digest,
            _digest(identity),
            row["account_event_id"],
        ),
    )
    store.connection.commit()
    with pytest.raises(PaperEvidenceConflict, match="arithmetic mismatch"):
        store.replay_account(account)


def test_lifecycle_replay_derives_state_and_rejects_cursor_rewrite(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease)
    run, previous = _begin_run(store, lease, source="source-state-replay", subjects=("signal-a",))
    subject = _subject(store, lease, run)
    observation = _observation(store, lease, run, subject)
    _plan(store, lease, run, subject, observation, "position_opened", account, {"scenario_id": "a"})
    _finish_run(store, lease, run, previous)
    store.connection.execute(
        "UPDATE subject_cursors SET state='armed' WHERE subject_generation_id=?",
        (subject,),
    )
    store.connection.commit()
    with pytest.raises(PaperEvidenceConflict, match="cursor does not match"):
        store.replay_lifecycle(subject)


def test_restart_accepts_no_change_observation_without_reopening_subject(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease)
    run1, previous1 = _begin_run(store, lease, source="source-restart-open", subjects=("signal-a",))
    subject = _subject(store, lease, run1)
    observation1 = _observation(store, lease, run1, subject)
    assert _observation(store, lease, run1, subject) == observation1
    _plan(store, lease, run1, subject, observation1, "position_opened", account, {"scenario_id": "a"})
    _finish_run(store, lease, run1, previous1)

    run2, previous2 = _begin_run(store, lease, source="source-restart-no-change", subjects=("signal-a",))
    assert _subject(store, lease, run2) == subject
    observation2 = _observation(store, lease, run2, subject, available_at=2_001.0)
    with pytest.raises(PaperEvidenceConflict, match="not armed"):
        _plan(store, lease, run2, subject, observation2, "position_opened", account, {"scenario_id": "a"})
    _finish_run(store, lease, run2, previous2)

    replay = store.replay_lifecycle(subject)
    assert replay["state"] == "opened"
    assert replay["last_observation_id"] == observation2
    assert store.connection.execute(
        "SELECT COUNT(*) FROM accepted_observations WHERE subject_generation_id=?",
        (subject,),
    ).fetchone()[0] == 2
    assert store.connection.execute(
        "SELECT COUNT(*) FROM observation_batches WHERE subject_generation_id=?",
        (subject,),
    ).fetchone()[0] == 2


def test_producer_sequence_cannot_be_reused_with_changed_members(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    member = {
        "logical_id": "signal-a",
        "payload_digest": _subject_payload("signal-a")["source_member_payload_digest"],
        "source_validation_generation_id": "validation-signal-a",
        "disposition": "active",
    }
    store.register_producer_generation(
        lease,
        producer_id="producer-a",
        producer_sequence=1,
        members=[member],
        code_identity="sha256:code-a",
        method_identity="producer.v2",
    )
    with pytest.raises(PaperEvidenceConflict, match="reused with different content"):
        store.register_producer_generation(
            lease,
            producer_id="producer-a",
            producer_sequence=1,
            members=[member | {"payload_digest": "sha256:changed"}],
            code_identity="sha256:code-a",
            method_identity="producer.v2",
        )


def test_account_child_generation_rejected_while_parent_has_open_position(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease)
    run, previous = _begin_run(store, lease, source="source-open-child", subjects=("signal-a",))
    subject = _subject(store, lease, run)
    observation = _observation(store, lease, run, subject)
    _plan(store, lease, run, subject, observation, "position_opened", account, {"scenario_id": "a"})
    _finish_run(store, lease, run, previous)
    with pytest.raises(PaperEvidenceConflict, match="active positions"):
        _account(store, lease, deposit=100.0, parent=account)


def test_completed_withdrawal_preserves_open_lifecycle_and_reserved_margin(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease)
    run, previous = _begin_run(store, lease, source="source-open-withdraw", subjects=("signal-a",))
    subject = _subject(store, lease, run)
    observation = _observation(store, lease, run, subject)
    _plan(store, lease, run, subject, observation, "position_opened", account, {"scenario_id": "a"})
    _finish_run(store, lease, run, previous)

    _empty_current_run(store, lease, source="source-complete-withdraw")

    assert store.subject(subject)["state"] == "withdrawn"
    assert store.replay_lifecycle(subject)["state"] == "opened"
    assert store.replay_account(account)["reserved_margin"] == 35.0
    assert [row["event_type"] for row in store.lifecycle_events(subject)][-1] == "source_withdrawn"


def test_withdrawn_subject_reintroduction_is_explicit_and_content_bound(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    payload = _subject_payload("signal-a")
    run1, previous1 = _begin_run(
        store,
        lease,
        source="source-a",
        subjects=("signal-a",),
        subject_payloads={"signal-a": payload},
    )
    subject1 = _subject(store, lease, run1, payload=payload)
    _finish_run(store, lease, run1, previous1)
    _empty_current_run(store, lease, source="source-withdraw")
    assert store.subject(subject1)["state"] == "withdrawn"

    run2, previous2 = _begin_run(
        store,
        lease,
        source="source-reintroduce-identical",
        subjects=("signal-a",),
        subject_payloads={"signal-a": payload},
    )
    identical = _subject(store, lease, run2, payload=payload)
    _finish_run(store, lease, run2, previous2)

    assert identical == subject1
    assert store.subject(subject1)["state"] == "active"
    assert [row["event_type"] for row in store.lifecycle_events(subject1)] == [
        "source_withdrawn",
        "source_reintroduced",
    ]

    _empty_current_run(store, lease, source="source-withdraw-again")
    changed_payload = _subject_payload("signal-a", method_identity="paper-lifecycle.v3")
    run3, previous3 = _begin_run(
        store,
        lease,
        source="source-reintroduce-changed",
        subjects=("signal-a",),
        subject_payloads={"signal-a": changed_payload},
    )
    changed = _subject(
        store,
        lease,
        run3,
        payload=changed_payload,
        supersedes=subject1,
    )
    _finish_run(store, lease, run3, previous3)

    assert changed != subject1
    assert store.subject(changed)["supersedes_generation_id"] == subject1
    assert store.lifecycle_events(changed)[0]["event_type"] == "source_reintroduced"


def test_counterfactual_arriving_before_primary_cannot_reserve_capital(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease, deposit=35.0, margin=35.0)
    run, previous = _begin_run(store, lease, source="source-scenarios", subjects=("variant-a", "variant-b"))
    primary = _subject(store, lease, run, logical_id="variant-a")
    counterfactual = _subject(store, lease, run, logical_id="variant-b")
    counterfactual_observation = _observation(store, lease, run, counterfactual)
    primary_observation = _observation(store, lease, run, primary)
    _plan(
        store,
        lease,
        run,
        counterfactual,
        counterfactual_observation,
        "position_opened",
        account,
        {"scenario_id": "b", "scenario_candidates": ["a", "b"]},
    )
    _plan(
        store,
        lease,
        run,
        primary,
        primary_observation,
        "position_opened",
        account,
        {"scenario_id": "a", "scenario_candidates": ["b", "a"]},
    )
    result = _finish_run(store, lease, run, previous)
    assert [item["account_event_type"] for item in result["applied_intents"]] == [
        "counterfactual_excluded",
        "position_opened",
    ]
    replay = store.replay_account(account)
    assert replay["active_subjects"] == [primary]
    assert replay["reserved_margin"] == 35.0


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("account_geneses", "config_json"),
        ("producer_generations", "manifest_json"),
        ("producer_generation_members", "payload_digest"),
        ("observation_batches", "rows_json"),
        ("lifecycle_events", "payload_json"),
        ("account_events", "payload_json"),
    ],
)
def test_authority_rows_are_database_immutable(tmp_path, table, column):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease)
    run, previous = _begin_run(store, lease, source=f"source-immutable-{table}", subjects=("signal-a",))
    subject = _subject(store, lease, run)
    observation = _observation(store, lease, run, subject)
    _plan(store, lease, run, subject, observation, "position_opened", account, {"scenario_id": "a"})
    _finish_run(store, lease, run, previous)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.connection.execute(f"UPDATE {table} SET {column}='forged'")


def test_database_checks_reject_execution_enabled_account_row(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease)
    run, previous = _begin_run(store, lease, source="source-db-check", subjects=("signal-a",))
    subject = _subject(store, lease, run)
    observation = _observation(store, lease, run, subject)
    _plan(store, lease, run, subject, observation, "position_opened", account, {"scenario_id": "a"})
    _finish_run(store, lease, run, previous)
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            """
            INSERT INTO account_events
            SELECT account_event_id || '-unsafe', account_generation_id, account_seq + 100,
                   prior_event_hash, event_hash || '-unsafe', event_type,
                   subject_generation_id, lifecycle_event_id, account_model_digest,
                   payload_digest, payload_json, supersedes_account_event_id,
                   writer_fence, created_at, 0, 1
            FROM account_events LIMIT 1
            """
        )


def test_observation_cannot_be_attached_to_an_unrelated_run(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    first_run, _ = _begin_run(store, lease, source="source-observation-a", subjects=("signal-a",))
    subject = _subject(store, lease, first_run)
    observation = _observation(store, lease, first_run, subject)
    second_run, _ = _begin_run(store, lease, source="source-observation-b", subjects=("signal-a",))
    with pytest.raises(PaperEvidenceConflict, match="generation join mismatch"):
        _plan(
            store,
            lease,
            second_run,
            subject,
            observation,
            "position_opened",
            _account(store, lease),
            {"scenario_id": "a"},
        )


def test_observation_cannot_cross_subjects_that_share_runtime_id(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease)
    payload_a = _subject_payload("signal-a", runtime_id="shared-runtime")
    payload_b = _subject_payload("signal-b", runtime_id="shared-runtime")
    run, _ = _begin_run(
        store,
        lease,
        source="source-shared-runtime",
        subjects=("signal-a", "signal-b"),
        subject_payloads={"signal-a": payload_a, "signal-b": payload_b},
    )
    subject_a = _subject(store, lease, run, logical_id="signal-a", payload=payload_a)
    subject_b = _subject(store, lease, run, logical_id="signal-b", payload=payload_b)
    observation_a = _observation(store, lease, run, subject_a)
    with pytest.raises(PaperEvidenceConflict, match="generation join mismatch"):
        _plan(
            store,
            lease,
            run,
            subject_b,
            observation_a,
            "position_opened",
            account,
            {"scenario_id": "b"},
        )


def test_exact_lifecycle_intent_retry_is_idempotent(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    account = _account(store, lease)
    run, _ = _begin_run(store, lease, source="source-idempotent-intent", subjects=("signal-a",))
    subject = _subject(store, lease, run)
    observation = _observation(store, lease, run, subject)
    first = _plan(
        store, lease, run, subject, observation, "position_opened", account, {"scenario_id": "a"}
    )
    second = _plan(
        store, lease, run, subject, observation, "position_opened", account, {"scenario_id": "a"}
    )
    assert second == first
    assert store.connection.execute(
        "SELECT COUNT(*) FROM paper_run_mutation_intents WHERE run_id=?", (run,)
    ).fetchone()[0] == 1


def test_projection_kind_rejects_changed_content_within_same_run(tmp_path):
    store = _store(tmp_path, lambda: 100.0)
    lease = _lease(store)
    run, previous = _begin_run(store, lease, source="source-projection-conflict", subjects=())
    account_output = f"sha256:{run}:account"
    store.complete_stage(lease, run, "account", input_digest=previous, output_digest=account_output)
    store.prepare_projection(
        lease,
        run_id=run,
        projection_kind="trades",
        items=[],
        input_projection_digests={"account": account_output},
        target_path=tmp_path / "trades.json",
    )
    with pytest.raises(PaperEvidenceConflict, match="different content"):
        store.prepare_projection(
            lease,
            run_id=run,
            projection_kind="trades",
            items=[{"paper_only": True, "execution_allowed": False}],
            input_projection_digests={"account": account_output},
            target_path=tmp_path / "trades.json",
        )


def test_writer_renew_release_preserve_monotonic_fence(tmp_path):
    now = [100.0]
    store = _store(tmp_path, lambda: now[0])
    first = _lease(store, seconds=5.0)
    renewed = store.renew_writer(first, lease_seconds=20.0)
    assert renewed.fence == first.fence
    store.release_writer(renewed)
    second = _lease(store, owner="owner-b", pid=202)
    assert second.fence == first.fence + 1
    with pytest.raises(StalePaperWriter):
        store.release_writer(first)
