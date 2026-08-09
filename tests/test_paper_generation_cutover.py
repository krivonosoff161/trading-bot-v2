import json
import time
from pathlib import Path

import pytest

from scripts.strategy_lab import farm_loop
from src.research_lab.ownership import ProcessIdentity
from src.research_lab.paper_evidence_store import (
    PaperEvidenceConflict,
    PaperEvidenceStore,
)
from src.research_lab.paper_generation_cutover import (
    CanonicalPaperGenerationRuntime,
    activate_cutover,
    compare_shadow_parity,
    current_checkout_revision,
    load_cutover_manifest,
    rollback_cutover,
    run_forward_shadow_replay,
)
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import append_signal
from src.research_lab.paper_telegram_sender import send_paper_telegram_previews
from src.research_lab.farm_tasks_db import FarmTasksDB
from src.research_lab.role_environment_dispatch import dispatch_role_environments
from src.research_lab.system_analyst_cycle import (
    feedback_payloads_from_outcomes,
    feedback_payloads_from_system_results,
)


IDENTITY = ProcessIdentity(4141, 100.0, "python-test.exe", "sha256:test-command")


class _Provider:
    name = "synthetic-cutover-provider"

    def fetch_ohlcv(self, _symbol, _timeframe, start_ts, end_ts):
        rows = [
            {
                "ts": 3_600_001,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "vol": 1,
            },
            {
                "ts": 7_200_001,
                "open": 100.5,
                "high": 111.0,
                "low": 100.0,
                "close": 110.0,
                "vol": 1,
            },
        ]
        return [row for row in rows if start_ts <= row["ts"] <= end_ts]


def _signal() -> PaperActionSignal:
    return PaperActionSignal(
        signal_id="cutover-signal",
        source="pfr_farm",
        symbol="BTC_USDT_SWAP",
        okx_inst_id="BTC-USDT-SWAP",
        timeframe="1h",
        side="long",
        setup_family="early_tp_tactical",
        entry_zone=[100.0, 101.0],
        stop_loss=95.0,
        invalidation_rule="close below 95",
        take_profit_plan=[{"label": "tp1", "price": 110.0, "size_frac": 1.0}],
        max_hold_bars=10,
        max_hold_minutes=600,
        reason_now="synthetic cutover signal",
        status="armed",
        created_at=1_800_000_000.0,
        expires_at=1_800_003_600.0,
        ref_price=100.0,
        risk_pct=5.0,
        boundary_ts=1,
        data_fingerprint="snapshot-cutover",
        dedup_key="BTC|1h|cutover",
        exit_mode="partial_be",
        validation_id="validation-cutover",
        validator_context={
            "ready_strategy_id": "ready-cutover",
            "source_validation_verdict": "PAPER_FORWARD_READY",
            "validation_id": "validation-cutover",
        },
    )


def _activate(tmp_path: Path) -> dict:
    return activate_cutover(
        tmp_path,
        owner_id="cutover-owner",
        identity=IDENTITY,
        code_identity=current_checkout_revision(),
        now=200.0,
    )


def test_runtime_requires_existing_digest_bound_cutover_and_never_auto_activates(
    tmp_path,
):
    database = tmp_path / "state" / "derived" / "paper_evidence.sqlite3"
    with pytest.raises(PaperEvidenceConflict, match="manifest is missing"):
        CanonicalPaperGenerationRuntime.open_required(
            tmp_path,
            owner_id="farm-owner",
            identity=IDENTITY,
        )
    assert not database.exists()

    activated = _activate(tmp_path)
    manifest_path = Path(activated["manifest_path"])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["code_identity"] = "sha256:tampered"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PaperEvidenceConflict, match="digest mismatch"):
        CanonicalPaperGenerationRuntime.open_required(
            tmp_path,
            owner_id="farm-owner",
            identity=IDENTITY,
        )


def test_cutover_and_runtime_refuse_revision_mismatch(tmp_path):
    with pytest.raises(PaperEvidenceConflict, match="does not match"):
        activate_cutover(
            tmp_path,
            owner_id="cutover-owner",
            identity=IDENTITY,
            code_identity="0" * 40,
        )

    _activate(tmp_path)
    with pytest.raises(PaperEvidenceConflict, match="does not match"):
        CanonicalPaperGenerationRuntime.open_required(
            tmp_path,
            owner_id="farm-owner",
            identity=IDENTITY,
            expected_code_identity="0" * 40,
        )


def test_canonical_runtime_binds_same_owner_and_publishes_one_current_generation(
    tmp_path,
):
    append_signal(tmp_path, _signal())
    activated = _activate(tmp_path)
    runtime = CanonicalPaperGenerationRuntime.open_required(
        tmp_path,
        owner_id="canonical-farm-owner",
        identity=IDENTITY,
        heartbeat_interval_seconds=0.02,
        lease_seconds=1.0,
    )
    try:
        row = runtime.store.connection.execute(
            "SELECT owner_id,pid,started_at,executable,command_digest "
            "FROM paper_writer_lease"
        ).fetchone()
        assert dict(row) == {
            "owner_id": "canonical-farm-owner",
            "pid": IDENTITY.pid,
            "started_at": IDENTITY.started_at,
            "executable": IDENTITY.executable,
            "command_digest": IDENTITY.command_digest,
        }
        result = runtime.run(provider=_Provider(), now_ms=8_000_000)
        assert result["projection"]["current"] is True
        assert result["projection"]["paper_generation_run_id"] == result["run_id"]
        assert result["paper_only"] is True
        assert result["execution_allowed"] is False
        assert (
            runtime.store.latest_producer_cursor("canonical-paper-signals")[
                "producer_sequence"
            ]
            == 1
        )
        assert runtime.database_path == Path(activated["database_path"])
    finally:
        runtime.close()
    assert not runtime.heartbeat._thread.is_alive()
    reopened = PaperEvidenceStore.open_existing(activated["database_path"])
    try:
        lease = reopened.connection.execute(
            "SELECT owner_id,lease_expires_at FROM paper_writer_lease"
        ).fetchone()
        assert lease["owner_id"] is None
        assert lease["lease_expires_at"] is None
    finally:
        reopened.close()


def test_writer_fence_advance_blocks_stale_runtime_before_second_materialization(
    tmp_path,
):
    append_signal(tmp_path, _signal())
    _activate(tmp_path)
    runtime = CanonicalPaperGenerationRuntime.open_required(
        tmp_path,
        owner_id="canonical-farm-owner",
        identity=IDENTITY,
        heartbeat_interval_seconds=60.0,
    )
    try:
        first = runtime.run(provider=_Provider(), now_ms=8_000_000)
        runtime.store.connection.execute(
            "UPDATE paper_writer_lease SET next_fence=next_fence+1"
        )
        runtime.store.connection.commit()
        with pytest.raises(PaperEvidenceConflict, match="stale"):
            runtime.run(provider=_Provider(), now_ms=8_100_000)
        assert runtime.store.current_run_id() == first["run_id"]
        assert (
            runtime.store.connection.execute(
                "SELECT COUNT(*) FROM projection_materializations WHERE status='completed'"
            ).fetchone()[0]
            == 1
        )
    finally:
        runtime.heartbeat._stop.set()
        runtime.heartbeat._thread.join(timeout=2)
        runtime.store.close()


def test_heartbeat_failure_is_visible_and_graceful_close_leaves_no_thread(
    tmp_path, monkeypatch
):
    _activate(tmp_path)
    failures = []
    runtime = CanonicalPaperGenerationRuntime.open_required(
        tmp_path,
        owner_id="canonical-farm-owner",
        identity=IDENTITY,
        heartbeat_interval_seconds=0.01,
        lease_seconds=1.0,
        on_failure=lambda exc, details: failures.append((exc, details)),
    )

    def fail_renew(*_args, **_kwargs):
        raise PaperEvidenceConflict("synthetic fence loss")

    monkeypatch.setattr(runtime.store, "renew_writer", fail_renew)
    deadline = time.time() + 1.0
    while runtime.heartbeat.failure is None and time.time() < deadline:
        time.sleep(0.01)
    with pytest.raises(PaperEvidenceConflict, match="heartbeat failed"):
        runtime.raise_if_failed()
    assert failures[0][1]["failure_kind"] == "paper_evidence_writer_lease"
    runtime.close()
    assert not runtime.heartbeat._thread.is_alive()


def test_rollback_is_non_destructive_idempotent_and_blocks_runtime(tmp_path):
    activated = _activate(tmp_path)
    database = Path(activated["database_path"])
    first = rollback_cutover(tmp_path, now=300.0)
    second = rollback_cutover(tmp_path, now=301.0)
    assert first["changed"] == 1
    assert second["changed"] == 0
    assert database.is_file()
    assert (
        load_cutover_manifest(tmp_path, require_active=False)["status"] == "rolled_back"
    )
    with pytest.raises(PaperEvidenceConflict, match="not active"):
        CanonicalPaperGenerationRuntime.open_required(
            tmp_path,
            owner_id="canonical-farm-owner",
            identity=IDENTITY,
        )


def test_shadow_parity_is_bounded_and_excludes_only_v2_identity_fields():
    legacy = [{"source_signal_id": "s1", "net_pct": 1.5, "paper_only": True}]
    v2 = {
        "current": True,
        "items": [
            {
                **legacy[0],
                "paper_generation_run_id": "run-1",
                "paper_subject_generation_id": "subject-1",
                "account_generation_id": "account-1",
                "paper_account_decision": "position_opened",
            }
        ],
    }
    assert compare_shadow_parity(legacy, v2)["parity"] is True
    changed = compare_shadow_parity(
        legacy, {**v2, "items": [{**v2["items"][0], "net_pct": 2.0}]}
    )
    assert changed["parity"] is False


def test_forward_shadow_replay_copies_only_signal_ledger_and_preserves_source(tmp_path):
    source = tmp_path / "source"
    shadow = tmp_path / "shadow"
    append_signal(source, _signal())
    source_ledger = source / "state" / "derived" / "paper_signals.jsonl"
    before = source_ledger.read_bytes()
    (source / "state" / "unrelated-private-surface.bin").write_bytes(b"do-not-copy")

    report = run_forward_shadow_replay(
        source,
        shadow,
        provider=_Provider(),
        owner_id="shadow-owner",
        identity=IDENTITY,
        code_identity=current_checkout_revision(),
        now_ms=8_000_000,
    )

    assert report["parity"]["parity"] is True
    assert source_ledger.read_bytes() == before
    assert (shadow / "state" / "derived" / "paper_signals.jsonl").read_bytes() == before
    assert not (shadow / "state" / "unrelated-private-surface.bin").exists()
    assert Path(report["database_path"]).is_file()


def test_delivery_refuses_stale_generation_without_transport_or_status_digest(tmp_path):
    snapshot = tmp_path / "state" / "derived" / "paper_telegram_preview.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(
        json.dumps(
            {
                "schema": "paper_telegram_preview_summary.v1",
                "paper_generation_run_id": "old-run",
                "current_generation_compatible": True,
                "items": [
                    {
                        "schema": "PaperTelegramPreview.v1",
                        "source_signal_id": "s1",
                        "paper_generation_run_id": "old-run",
                        "paper_only": True,
                        "execution_allowed": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    async def send_text(_recipient, _text):
        calls.append("sent")
        return 1

    summary = send_paper_telegram_previews(
        tmp_path,
        apply=True,
        paper_chat_configured=True,
        paper_chat_ids_count=1,
        recipient_ids=["synthetic-recipient"],
        send_text=send_text,
        status_digest=True,
        expected_generation_run_id="current-run",
    )
    assert summary["generation_block_reason"] == "preview_generation_run_mismatch"
    assert summary["eligible_cards"] == 0
    assert summary["sent_messages"] == 0
    assert calls == []


def test_analyst_task_specs_preserve_generation_and_reject_stale_followup():
    training = {
        "training_row_id": "training-current",
        "candidate_id": "candidate-current",
        "symbol": "BTC_USDT_SWAP",
        "timeframe": "1h",
        "family": "continuation",
        "boundary_ts": 1_800_000_000_000,
        "paper_generation_run_id": "run-current",
        "paper_subject_generation_id": "subject-current",
        "terminal_lifecycle_event_id": "event-current",
        "account_generation_id": "account-current",
    }
    review = {
        "role_id": "outcome_reviewer",
        "review_id": "review-current",
        "source_ref": "training-current",
        "accepted": True,
        "created_at": "2026-08-09T10:00:00+00:00",
        "payload": {"summary": "bounded generation review"},
    }
    payload = feedback_payloads_from_outcomes([training], [review])[0]
    specs = [item["task_spec"] for item in payload["recommendations"]]
    assert {item["paper_generation_run_id"] for item in specs} == {"run-current"}
    assert {item["paper_subject_generation_id"] for item in specs} == {
        "subject-current"
    }
    result = {
        "result_id": "role-result-stale",
        "task_spec": {"generation": 0, "paper_generation_run_id": "run-stale"},
        "result": {"status": "completed"},
    }
    draft = {
        "role_id": "system_analyst",
        "review_id": "review-stale",
        "source_ref": "role-result-stale",
        "accepted": True,
        "created_at": "2026-08-09T10:01:00+00:00",
        "payload": {},
    }
    assert (
        feedback_payloads_from_system_results(
            [result],
            [draft],
            expected_generation_run_id="run-current",
        )
        == []
    )


def test_role_dispatch_skips_accepted_environment_from_stale_generation(tmp_path):
    candidate = tmp_path / "state" / "role_environments" / "farm" / "env_stale.json"
    state = candidate.parent / "_state" / candidate.name
    candidate.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    candidate.write_text(
        json.dumps(
            {
                "schema": "RoleEnvironment.v1",
                "environment_id": "env_stale",
                "task_spec": {
                    "schema": "RoleTaskSpec.v1",
                    "paper_generation_run_id": "run-stale",
                },
            }
        ),
        encoding="utf-8",
    )
    state.write_text(
        json.dumps({"status": "request_accepted"}),
        encoding="utf-8",
    )
    tasks = FarmTasksDB(":memory:")
    try:
        summary = dispatch_role_environments(
            tmp_path,
            tasks,
            apply=True,
            expected_generation_run_id="run-current",
            evidence_database_path=tmp_path / "missing.sqlite3",
        )
    finally:
        tasks.close()
    assert summary["by_role"]["farm"]["seen"] == 0
    assert summary["paper_generation_run_id"] == "run-current"
    assert not (tmp_path / "state" / "role_work_queue").exists()


def test_canonical_farm_launcher_requires_v2_but_noncanonical_default_remains_explicit():
    launcher = Path("bat/strategy_lab_farm_full_cycle_loop.bat").read_text(
        encoding="utf-8"
    )
    assert 'set "STRATEGY_LAB_PAPER_EVIDENCE_V2_REQUIRED=1"' in launcher
    source = Path("scripts/strategy_lab/farm_loop.py").read_text(encoding="utf-8")
    assert 'default=_env_flag("STRATEGY_LAB_PAPER_EVIDENCE_V2_REQUIRED")' in source


class _FakeRuntime:
    database_path = Path("synthetic-paper-evidence.sqlite3")

    def __init__(self):
        self.failures_checked = 0

    def raise_if_failed(self):
        self.failures_checked += 1

    def run(self, *, provider, now_ms):
        assert provider == "bounded-provider"
        assert now_ms > 0
        return {
            "run_id": "run-current",
            "producer_generation_id": "producer-current",
            "account_generation_id": "account-current",
            "bridge": {"stage": "bridge"},
            "consumer": {"stage": "consumer"},
            "queue": {"stage": "queue"},
            "observer": {"stage": "observer"},
            "trades": {"stage": "account"},
        }


def test_farm_v2_chain_routes_every_generation_aware_consumer_to_authority(
    tmp_path, monkeypatch
):
    runtime = _FakeRuntime()
    args = type("Args", (), {"paper_generation_runtime": runtime})()
    calls = []

    def bound(**extra):
        return {
            "paper_generation_run_id": "run-current",
            "current_generation_compatible": True,
            **extra,
        }

    monkeypatch.setattr(farm_loop, "_write_loop_status", lambda *_a, **_k: None)
    monkeypatch.setattr(
        farm_loop,
        "_refresh_setup_outcome_memory",
        lambda *_a, **kwargs: (
            calls.append(("memory", kwargs.get("evidence_database_path"))) or bound()
        ),
    )

    from src.research_lab import (
        outcome_retest_result,
        paper_lineage,
        paper_product_quality_report,
        paper_telegram_preview,
        role_environment_dispatch,
        system_analyst_cycle,
        trading_policy_calibration,
    )
    from src.research_lab.paper_signals import training_export

    def preview(*_args, **kwargs):
        calls.append(("preview", kwargs.get("evidence_database_path")))
        return {
            "current_generation_compatible": True,
            "paper_generation_run_id": "run-current",
        }

    monkeypatch.setattr(paper_telegram_preview, "build_paper_telegram_preview", preview)
    monkeypatch.setattr(
        training_export,
        "export_training_rows",
        lambda *_a, **kwargs: (
            calls.append(("training", kwargs.get("evidence_database_path"))) or bound()
        ),
    )
    monkeypatch.setattr(
        paper_lineage,
        "build_paper_lineage",
        lambda *_a, **kwargs: (
            calls.append(("lineage", kwargs.get("evidence_database_path"))) or bound()
        ),
    )
    monkeypatch.setattr(
        outcome_retest_result,
        "build_outcome_retest_results",
        lambda *_a: {"training_evidence": bound()},
    )
    monkeypatch.setattr(
        system_analyst_cycle, "run_system_analyst_cycle", lambda *_a, **_k: bound()
    )
    monkeypatch.setattr(
        role_environment_dispatch,
        "dispatch_role_environments",
        lambda *_a, **_k: bound(),
    )
    monkeypatch.setattr(
        role_environment_dispatch,
        "reconcile_role_work_results",
        lambda *_a, **_k: bound(),
    )
    monkeypatch.setattr(
        trading_policy_calibration,
        "build_trading_policy_calibration",
        lambda *_a, **kwargs: (
            calls.append(("calibration", kwargs.get("evidence_database_path")))
            or bound()
        ),
    )
    monkeypatch.setattr(
        paper_product_quality_report,
        "build_paper_product_quality_report",
        lambda *_a, **kwargs: (
            calls.append(("quality", kwargs.get("evidence_database_path"))) or bound()
        ),
    )
    out = {}
    farm_loop._run_main_paper_derived_chain(
        args,
        tmp_path,
        tasks=object(),
        apply=True,
        loop=True,
        cycle_started_at=1.0,
        out=out,
        provider="bounded-provider",
    )
    expected = _FakeRuntime.database_path
    assert {name for name, _path in calls} == {
        "preview",
        "training",
        "lineage",
        "calibration",
        "memory",
        "quality",
    }
    assert all(path == expected for _name, path in calls)
    assert out["paper_generation_v2"]["run_id"] == "run-current"
    assert args.paper_generation_run_id == "run-current"
    assert runtime.failures_checked == 2


def test_farm_v2_preview_mismatch_fails_closed_before_training(tmp_path, monkeypatch):
    runtime = _FakeRuntime()
    args = type("Args", (), {"paper_generation_runtime": runtime})()
    training_called = []
    monkeypatch.setattr(farm_loop, "_write_loop_status", lambda *_a, **_k: None)

    from src.research_lab import paper_telegram_preview
    from src.research_lab.paper_signals import training_export

    monkeypatch.setattr(
        paper_telegram_preview,
        "build_paper_telegram_preview",
        lambda *_a, **_k: {
            "current_generation_compatible": True,
            "paper_generation_run_id": "stale-run",
        },
    )
    monkeypatch.setattr(
        training_export,
        "export_training_rows",
        lambda *_a, **_k: training_called.append(True),
    )
    with pytest.raises(RuntimeError, match="not bound"):
        farm_loop._run_main_paper_derived_chain(
            args,
            tmp_path,
            tasks=object(),
            apply=True,
            loop=True,
            cycle_started_at=1.0,
            out={},
            provider="bounded-provider",
        )
    assert training_called == []
