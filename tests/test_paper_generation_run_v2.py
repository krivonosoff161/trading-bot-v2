import sqlite3
import json

import pytest

from scripts.strategy_lab import operational_health
from src.research_lab.ownership import ProcessIdentity
from src.research_lab.paper_evidence_store import PaperEvidenceStore
from src.research_lab.paper_generation_run import run_paper_generation_v2
from src.research_lab.paper_generation_contract import (
    PaperGenerationMismatch,
    canonical_digest,
)
from src.research_lab.paper_lineage import build_paper_lineage
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import (
    append_signal,
    load_signals_strict,
    update_signal,
    write_state_snapshot,
)
from src.research_lab.paper_signals.training_export import export_training_rows
from src.research_lab.paper_telegram_preview import build_paper_telegram_preview
from src.research_lab.paper_telegram_sender import send_paper_telegram_previews
from src.research_lab.setup_outcome_memory import summarize_product_training_memory
from src.research_lab.trading_policy_calibration import build_trading_policy_calibration


class _Provider:
    name = "synthetic-generation-provider"

    def fetch_ohlcv(self, _symbol, _timeframe, start_ts, end_ts):
        rows = [
            {"ts": 3_600_001, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "vol": 1},
            {"ts": 7_200_001, "open": 100.5, "high": 111.0, "low": 100.0, "close": 110.0, "vol": 1},
        ]
        return [row for row in rows if start_ts <= row["ts"] <= end_ts]


class _CapacityProvider(_Provider):
    name = "synthetic-capacity-provider"

    def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
        if symbol == "BTC-USDT-SWAP":
            rows = [
                {"ts": 3_600_001, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "vol": 1}
            ]
            return [row for row in rows if start_ts <= row["ts"] <= end_ts]
        return super().fetch_ohlcv(symbol, timeframe, start_ts, end_ts)


def _signal() -> PaperActionSignal:
    return PaperActionSignal(
        signal_id="signal-v2",
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
        reason_now="synthetic end-to-end v2 generation",
        status="armed",
        created_at=1_800_000_000.0,
        expires_at=1_800_003_600.0,
        ref_price=100.0,
        risk_pct=5.0,
        boundary_ts=1,
        data_fingerprint="snapshot-v2",
        dedup_key="BTC|1h|v2",
        exit_mode="partial_be",
        validation_id="validation-generation-v2",
        validator_context={
            "ready_strategy_id": "ready-v2",
            "source_validation_verdict": "PAPER_FORWARD_READY",
            "validation_id": "validation-generation-v2",
            "validation_generation_id": "validation-generation-v2",
        },
    )


def _store_and_account(tmp_path, *, deposit=70.0):
    store = PaperEvidenceStore(tmp_path / "paper-evidence.sqlite3", clock=lambda: 100.0)
    store.activate()
    lease = store.acquire_writer(
        owner_id="test-owner",
        identity=ProcessIdentity(101, 101.0, "python-test.exe", "sha256:test-command"),
        lease_seconds=30.0,
    )
    account = store.create_account_genesis(
        lease,
        {
            "currency": "USDT",
            "deposit": deposit,
            "leverage": 3.0,
            "position_margin": 35.0,
            "allocation_policy": "one-primary-per-scenario.v1",
            "cost_policy": "net-pct-cost-inclusive.v1",
            "rounding_policy": "integer-microunits-half-even.v1",
            "method": "paper-account.v2",
        },
    )
    return store, lease, account


def _run(tmp_path, store, lease, account, *, sequence=1, parent=None, provider=None):
    return run_paper_generation_v2(
        tmp_path,
        store=store,
        lease=lease,
        account_generation_id=account,
        provider=provider or _Provider(),
        producer_id="synthetic-producer",
        producer_sequence=sequence,
        code_identity="sha256:synthetic-code",
        producer_method_identity="synthetic-producer.v2",
        simulator_manifest_id="simulator-manifest-v2",
        lifecycle_method_identity="paper-lifecycle.v2",
        required_validation_generation_id="validation-generation-v2",
        now_ms=8_000_000,
        parent_producer_generation_id=parent,
    )


def test_end_to_end_generation_commits_events_account_and_projection_once(tmp_path):
    append_signal(tmp_path, _signal())
    store, lease, account = _store_and_account(tmp_path)

    result = _run(tmp_path, store, lease, account)

    assert result["finalized"]["current"] is True
    assert store.current_run_id() == result["run_id"]
    subject = store.active_subject("signal-v2")
    assert subject is not None
    assert store.replay_lifecycle(subject["subject_generation_id"])["state"] == "closed"
    account_state = store.replay_account(account)
    assert account_state["events"] == 2
    assert account_state["reserved_margin"] == 0.0
    readonly_account = PaperEvidenceStore.read_account_state(store.path)
    assert readonly_account["valid"] is True
    assert readonly_account["account_generation_id"] == account
    assert readonly_account["balance_microunits"] == account_state["balance_microunits"]
    projection = PaperEvidenceStore.read_completed_projection(store.path, "trades")
    assert projection["current"] is True
    assert projection["paper_generation_run_id"] == result["run_id"]
    assert projection["items"][0]["paper_subject_generation_id"] == (
        subject["subject_generation_id"]
    )
    assert projection["items"][0]["terminal_lifecycle_event_id"]
    assert projection["items"][0]["account_generation_id"] == account
    assert not (tmp_path / "state" / "derived" / "paper_account_events.jsonl").exists()


def _broad_signal(signal_id: str = "broad-research-signal") -> PaperActionSignal:
    signal = _signal()
    signal.signal_id = signal_id
    signal.source = "farm"
    signal.validation_id = ""
    signal.validator_context = {}
    signal.dedup_key = f"broad|{signal_id}"
    return signal


def test_v2_generation_excludes_broad_research_signal_from_authority(tmp_path):
    append_signal(tmp_path, _broad_signal())
    append_signal(tmp_path, _signal())
    store, lease, account = _store_and_account(tmp_path)

    result = _run(tmp_path, store, lease, account)

    assert result["producer_membership"] == {
        "active_executable_signals": 2,
        "validation_bound_members": 1,
        "research_only_excluded": 1,
        "authority_source": "pfr_farm",
    }
    members = store.connection.execute(
        "SELECT logical_id FROM producer_generation_members ORDER BY logical_id"
    ).fetchall()
    assert [row["logical_id"] for row in members] == ["signal-v2"]
    assert result["bridge"]["instructions"] == 1
    assert result["bridge"]["skipped_unvalidated"] == 1
    assert store.active_subject("broad-research-signal") is None


def test_v2_empty_generation_preserves_broad_research_observation(tmp_path):
    broad = _broad_signal()
    append_signal(tmp_path, broad)
    store, lease, account = _store_and_account(tmp_path)

    result = _run(tmp_path, store, lease, account)

    assert result["producer_membership"]["validation_bound_members"] == 0
    assert result["producer_membership"]["research_only_excluded"] == 1
    assert result["bridge"]["instructions"] == 0
    assert result["finalized"]["current"] is True
    producer = store.connection.execute(
        "SELECT expected_member_count FROM producer_generations"
    ).fetchone()
    assert producer["expected_member_count"] == 0
    assert store.active_subject(broad.signal_id) is None


def test_v2_empty_generation_delivers_broad_signal_only_as_research_card(tmp_path):
    broad = _broad_signal()
    append_signal(tmp_path, broad)
    write_state_snapshot(tmp_path)
    store, lease, account = _store_and_account(tmp_path)
    result = _run(tmp_path, store, lease, account)

    preview = build_paper_telegram_preview(
        tmp_path,
        fetch_public_chart_candles=False,
        evidence_database_path=store.path,
    )

    assert preview["paper_generation_run_id"] == result["run_id"]
    assert preview["research_observation_authority"] == "none"
    assert preview["research_observation_items"] == 1
    assert preview["by_validation_tier"] == {"farm_calculated": 1}
    item = preview["items"][0]
    assert item["source_signal_id"] == broad.signal_id
    assert item["paper_generation_run_id"] == ""
    assert item["research_observation_generation_id"].startswith(
        "research_observation_"
    )
    assert store.active_subject(broad.signal_id) is None

    delivery = send_paper_telegram_previews(
        tmp_path,
        expected_generation_run_id=result["run_id"],
    )
    assert delivery["generation_block_reason"] == ""
    assert delivery["eligible_cards"] == 1
    assert delivery["sent_messages"] == 0

    broad.status = "closed_paper"
    broad.outcome = {"result": "take", "net_pct": 1.0}
    update_signal(tmp_path, broad)
    training = export_training_rows(
        tmp_path,
        force=True,
        evidence_database_path=store.path,
    )
    assert training["rows"] == 0
    assert training["source_terminal_rows_unbound"] == 1
    assert training["current_generation_compatible"] is True


def test_v2_research_card_fails_closed_when_signal_snapshot_changes(tmp_path):
    broad = _broad_signal()
    append_signal(tmp_path, broad)
    write_state_snapshot(tmp_path)
    store, lease, account = _store_and_account(tmp_path)
    result = _run(tmp_path, store, lease, account)
    build_paper_telegram_preview(
        tmp_path,
        fetch_public_chart_candles=False,
        evidence_database_path=store.path,
    )

    broad.reason_now = "new completed farm calculation"
    update_signal(tmp_path, broad)
    write_state_snapshot(tmp_path)
    delivery = send_paper_telegram_previews(
        tmp_path,
        expected_generation_run_id=result["run_id"],
    )

    assert delivery["generation_block_reason"] == "research_preview_generation_stale"
    assert delivery["eligible_cards"] == 0


def test_non_pfr_signal_cannot_self_grant_v2_authority(tmp_path):
    broad = _broad_signal()
    broad.validation_id = "forged-validation-id"
    broad.validator_context = {
        "validation_id": "forged-validation-id",
        "validation_generation_id": "forged-validation-generation-id",
        "ready_strategy_id": "forged-ready-id",
        "source_validation_verdict": "PAPER_FORWARD_READY",
    }
    append_signal(tmp_path, broad)
    store, lease, account = _store_and_account(tmp_path)

    result = _run(tmp_path, store, lease, account)

    assert result["producer_membership"]["validation_bound_members"] == 0
    assert result["bridge"]["instructions"] == 0
    assert store.active_subject(broad.signal_id) is None


def test_pfr_signal_without_validation_identity_still_fails_closed(tmp_path):
    malformed = _signal()
    malformed.validation_id = ""
    malformed.validator_context = {
        "ready_strategy_id": "ready-v2",
        "source_validation_verdict": "PAPER_FORWARD_READY",
        "validation_id": "validation-candidate-v2",
    }
    append_signal(tmp_path, malformed)
    store, lease, account = _store_and_account(tmp_path)

    with pytest.raises(
        PaperGenerationMismatch,
        match="PFR bridge source lacks validation generation identity",
    ):
        _run(tmp_path, store, lease, account)

    assert store.connection.execute(
        "SELECT COUNT(*) FROM producer_generations"
    ).fetchone()[0] == 0


def test_pfr_signal_from_superseded_validation_generation_fails_before_write(
    tmp_path,
):
    stale = _signal()
    stale.validator_context = {
        **stale.validator_context,
        "validation_generation_id": "validation-generation-superseded",
    }
    append_signal(tmp_path, stale)
    store, lease, account = _store_and_account(tmp_path)

    with pytest.raises(
        PaperGenerationMismatch,
        match="outside the current validation generation",
    ):
        _run(tmp_path, store, lease, account)

    assert store.connection.execute(
        "SELECT COUNT(*) FROM producer_generations"
    ).fetchone()[0] == 0


def test_stage_exception_marks_run_failed_without_current_publication(tmp_path, monkeypatch):
    append_signal(tmp_path, _signal())
    store, lease, account = _store_and_account(tmp_path)

    from src.research_lab import paper_generation_run

    monkeypatch.setattr(
        paper_generation_run,
        "consume_main_paper_instructions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic consumer failure")),
    )
    with pytest.raises(RuntimeError, match="synthetic consumer failure"):
        _run(tmp_path, store, lease, account)

    run = store.connection.execute("SELECT * FROM paper_runs").fetchone()
    assert run["status"] == "failed"
    assert store.current_run_id() == ""
    assert store.connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()[0] == 0
    assert store.connection.execute("SELECT COUNT(*) FROM account_events").fetchone()[0] == 0


def test_strict_source_loader_reports_truncated_legacy_row(tmp_path):
    append_signal(tmp_path, _signal())
    path = tmp_path / "state" / "derived" / "paper_signals.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"signal_id":"truncated"')

    with pytest.raises(ValueError, match="corrupt paper signal row"):
        load_signals_strict(tmp_path)


def test_database_trigger_blocks_account_event_delete_after_generation(tmp_path):
    append_signal(tmp_path, _signal())
    store, lease, account = _store_and_account(tmp_path)
    _run(tmp_path, store, lease, account)

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.connection.execute("DELETE FROM account_events")


def test_downstream_learning_preview_and_lineage_bind_completed_generation(tmp_path):
    signal = _signal()
    append_signal(tmp_path, signal)
    store, lease, account = _store_and_account(tmp_path)
    result = _run(tmp_path, store, lease, account)
    observed = result["observer"]["items"][0]
    signal.status = str(observed["signal_status"])
    signal.outcome = dict(observed["outcome"])
    signal.review = dict(observed["review"])
    update_signal(tmp_path, signal)

    training = export_training_rows(
        tmp_path,
        force=True,
        evidence_database_path=store.path,
    )
    training_payload = json.loads(
        (tmp_path / "state" / "derived" / "paper_signal_training.json").read_text(
            encoding="utf-8"
        )
    )
    training_row = training_payload["items"][0]
    preview = build_paper_telegram_preview(
        tmp_path,
        fetch_public_chart_candles=False,
        evidence_database_path=store.path,
    )
    lineage = build_paper_lineage(
        tmp_path,
        evidence_database_path=store.path,
    )
    calibration = build_trading_policy_calibration(
        tmp_path,
        evidence_database_path=store.path,
    )
    product_memory = summarize_product_training_memory(
        tmp_path,
        evidence_database_path=store.path,
    )
    health = operational_health.collect(
        private_root=tmp_path,
        pfr_db_path=tmp_path / "missing.sqlite",
        evidence_database_path=store.path,
    )

    assert training["current_generation_compatible"] is True
    assert training_row["immutable_terminal_evidence"] is True
    assert training_row["terminal_lifecycle_event_id"]
    assert training_row["account_generation_id"] == account
    assert preview["source_schema"] == "PaperProjectionEnvelope.v2"
    assert preview["current_generation_compatible"] is True
    assert preview["items"][0]["terminal_lifecycle_event_id"]
    assert lineage["current_generation_compatible"] is True
    assert lineage["items"][0]["terminal_lifecycle_event_id"]
    assert calibration["trusted_terminal_rows"] == 1
    assert calibration["paper_generation_run_id"] == result["run_id"]
    assert product_memory["eligible_rows"] == 1
    assert product_memory["summary"]["terminal_rows"] == 1
    assert product_memory["paper_generation_run_id"] == result["run_id"]
    assert health["paper_generation"]["current"] is True
    assert health["paper_generation"]["stage_chain_compatible"] is True
    assert health["readiness"]["paper_chain_counts"]["status"] == "pass"
    assert health["readiness"]["paper_runtime_observed"]["status"] == "pass"


def test_terminal_counterfactual_is_excluded_without_rolling_back_generation(tmp_path):
    primary = _signal()
    counterfactual = _signal()
    counterfactual.signal_id = "signal-v2-b"
    counterfactual.dedup_key = "BTC|1h|v2-b"
    counterfactual.validation_id = "validation-generation-v2-b"
    counterfactual.validator_context = {
        **counterfactual.validator_context,
        "validation_id": "validation-generation-v2-b",
    }
    append_signal(tmp_path, primary)
    append_signal(tmp_path, counterfactual)
    store, lease, account = _store_and_account(tmp_path)

    result = _run(tmp_path, store, lease, account)

    assert result["finalized"]["current"] is True
    account_types = [
        row["event_type"]
        for row in store.connection.execute(
            "SELECT event_type FROM account_events WHERE account_generation_id=? ORDER BY account_seq",
            (account,),
        ).fetchall()
    ]
    assert account_types.count("position_opened") == 1
    assert account_types.count("position_closed") == 1
    assert account_types.count("counterfactual_excluded") == 1
    counterfactual_subject = store.active_subject("signal-v2-b")
    assert counterfactual_subject is not None
    assert store.replay_lifecycle(counterfactual_subject["subject_generation_id"])["state"] == (
        "counterfactual"
    )


def test_projection_reader_rejects_self_consistent_dangling_event_reference(tmp_path):
    append_signal(tmp_path, _signal())
    store, lease, account = _store_and_account(tmp_path)
    _run(tmp_path, store, lease, account)
    row = store.connection.execute(
        "SELECT * FROM projection_materializations WHERE status='completed'"
    ).fetchone()
    envelope = json.loads(row["envelope_json"])
    envelope["items"][0]["terminal_lifecycle_event_id"] = "paperlifecycle_dangling"
    content_digest = canonical_digest(envelope["items"])
    envelope["content_digest"] = content_digest
    store.connection.execute(
        "UPDATE projection_materializations SET content_digest=?,envelope_digest=?,envelope_json=? "
        "WHERE projection_id=?",
        (
            content_digest,
            canonical_digest(envelope),
            json.dumps(envelope, sort_keys=True, separators=(",", ":")),
            row["projection_id"],
        ),
    )
    store.connection.commit()

    loaded = PaperEvidenceStore.read_completed_projection(store.path, "trades")
    assert loaded["current"] is False
    assert loaded["generation_status"] == "unreadable"


def test_projection_reader_rejects_decision_without_allocation_evidence(tmp_path):
    append_signal(tmp_path, _signal())
    store, lease, account = _store_and_account(tmp_path)
    _run(tmp_path, store, lease, account)
    row = store.connection.execute(
        "SELECT * FROM projection_materializations WHERE status='completed'"
    ).fetchone()
    envelope = json.loads(row["envelope_json"])
    assert envelope["items"][0]["paper_account_decision"] == "position_opened"
    envelope["items"][0]["allocation_lifecycle_event_id"] = ""
    content_digest = canonical_digest(envelope["items"])
    envelope["content_digest"] = content_digest
    store.connection.execute(
        "UPDATE projection_materializations SET content_digest=?,envelope_digest=?,envelope_json=? "
        "WHERE projection_id=?",
        (
            content_digest,
            canonical_digest(envelope),
            json.dumps(envelope, sort_keys=True, separators=(",", ":")),
            row["projection_id"],
        ),
    )
    store.connection.commit()

    loaded = PaperEvidenceStore.read_completed_projection(store.path, "trades")
    assert loaded["current"] is False
    assert loaded["generation_status"] == "unreadable"


def test_terminal_after_capital_exhaustion_records_rejection_without_rollback(tmp_path):
    first = _signal()
    second = _signal()
    second.signal_id = "zz-signal-capacity-b"
    second.symbol = "ETH_USDT_SWAP"
    second.okx_inst_id = "ETH-USDT-SWAP"
    second.dedup_key = "ETH|1h|capacity-b"
    second.validation_id = "validation-capacity-b"
    second.validator_context = {
        **second.validator_context,
        "validation_id": "validation-capacity-b",
    }
    append_signal(tmp_path, first)
    append_signal(tmp_path, second)
    store, lease, account = _store_and_account(tmp_path, deposit=35.0)
    result = _run(tmp_path, store, lease, account, provider=_CapacityProvider())

    assert result["finalized"]["current"] is True
    event_types = [
        row["event_type"]
        for row in store.connection.execute(
            "SELECT event_type FROM account_events WHERE account_generation_id=? ORDER BY account_seq",
            (account,),
        ).fetchall()
    ]
    assert event_types == ["position_opened", "allocation_rejected"]
    rejected = store.active_subject("zz-signal-capacity-b")
    assert rejected is not None
    assert store.replay_lifecycle(rejected["subject_generation_id"])["state"] == (
        "allocation_rejected"
    )


def test_coordinator_calculates_only_after_store_reloads_persisted_rows(tmp_path, monkeypatch):
    append_signal(tmp_path, _signal())
    store, lease, account = _store_and_account(tmp_path)
    from src.research_lab import main_paper_runtime

    original = main_paper_runtime.lane.observe

    def assert_persisted(signal, candles):
        row = store.connection.execute(
            "SELECT observation_id FROM observation_batches ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert store.observation(row["observation_id"])["rows"] == candles
        return original(signal, candles)

    monkeypatch.setattr(main_paper_runtime.lane, "observe", assert_persisted)
    result = _run(tmp_path, store, lease, account)
    assert result["finalized"]["current"] is True


@pytest.mark.parametrize(
    ("stage", "target"),
    [
        ("bridge", "export_main_paper_instructions"),
        ("consumer", "consume_main_paper_instructions"),
        ("queue", "build_main_paper_runtime_queue"),
        ("observer", "observe_main_paper_runtime"),
        ("account", "build_main_paper_trade_ledger"),
        ("projection", "prepare_projection"),
    ],
)
def test_actual_coordinator_failure_after_each_stage_preserves_prior_current(
    tmp_path, monkeypatch, stage, target
):
    append_signal(tmp_path, _signal())
    store, lease, account = _store_and_account(tmp_path)
    first = _run(tmp_path, store, lease, account)
    account_events_before = store.replay_account(account)["events"]

    from src.research_lab import paper_generation_run

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"synthetic {stage} failure")

    if stage == "projection":
        monkeypatch.setattr(store, target, fail)
    else:
        monkeypatch.setattr(paper_generation_run, target, fail)
    with pytest.raises(RuntimeError, match=f"synthetic {stage} failure"):
        _run(
            tmp_path,
            store,
            lease,
            account,
            sequence=2,
            parent=first["producer_generation_id"],
        )

    assert store.current_run_id() == first["run_id"]
    assert store.replay_account(account)["events"] == account_events_before
    failed = store.connection.execute(
        "SELECT * FROM paper_runs WHERE run_id<>? ORDER BY created_at DESC LIMIT 1",
        (first["run_id"],),
    ).fetchone()
    assert failed["status"] == "failed"
