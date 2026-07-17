# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.research_lab.hard_validation_export import _rebuild_trades_from_result
from src.research_lab.candle_library import load_canonical_candles
from src.research_lab.candle_migration import migrate_json_candles
from src.research_lab.candle_store import CandleStore, CandleStoreError
from src.research_lab.data_fingerprint import fingerprint_for_file
from src.research_lab.hard_validation_contract import SetupCard
from src.research_lab.market_data_packet import build_market_data_packet
from src.research_lab.outcome_learning import _market_context
from src.research_lab.paper_readiness import summarize_paper_readiness
from src.research_lab.setup_library import write_setup_library
from src.research_lab.candle_snapshot import build_snapshot_manifest
from src.research_lab.experiment import (
    ExperimentSpec,
    _load_experiment_candles,
    evaluate_spec,
)

MINUTE = 60_000
HOUR = 60 * MINUTE


def _row(ts: int, close: float = 100.0, **extra) -> dict:
    return {
        "ts": ts,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "vol": 10.0,
        **extra,
    }


def _rows(count: int, *, step: int = HOUR, close_offset: float = 0.0) -> list[dict]:
    return [_row(i * step, 100.0 + i + close_offset) for i in range(count)]


def _write_json(root, rows: list[dict], *, symbol: str = "BTC_USDT_SWAP", timeframe: str = "1h"):
    folder = root / "market_data" / timeframe
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{symbol}_history_{timeframe}.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_revision_history_supports_as_of_read(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "BTC_USDT_SWAP", "1m", [_row(0, 100.0)], source="provider-A",
        observed_at_ms=90, available_at_ms=100, acquired_at_ms=110,
    )
    store.upsert_candles(
        "BTC_USDT_SWAP", "1m", [_row(0, 101.0)], source="provider-B",
        observed_at_ms=190, available_at_ms=200, acquired_at_ms=210,
    )

    before = store.read_snapshot(
        "BTC_USDT_SWAP", "1m", 0, 0, as_of_ms=150,
        purpose="experiment", coverage_policy="available",
    )
    after = store.read_snapshot(
        "BTC_USDT_SWAP", "1m", 0, 0, as_of_ms=250,
        purpose="experiment", coverage_policy="available",
    )

    assert before.rows[0]["close"] == 100.0
    assert before.rows[0]["_source"] == "provider-A"
    assert before.rows[0]["_available_at_ms"] == 100
    assert after.rows[0]["close"] == 101.0
    assert after.rows[0]["_source"] == "provider-B"
    assert before.manifest.snapshot_id != after.manifest.snapshot_id


def test_late_enrichment_is_invisible_before_enrichment_available_at(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "FLOW_USDT_SWAP", "1h", [_row(0, 100.0)], source="prices",
        available_at_ms=100,
    )
    store.upsert_candles(
        "FLOW_USDT_SWAP", "1h", [_row(0, 100.0, funding=0.001, oi=1200)],
        source="late-enrichment", available_at_ms=200,
    )

    early = store.read_snapshot(
        "FLOW_USDT_SWAP", "1h", 0, 0, as_of_ms=150,
        purpose="feature", coverage_policy="available",
    )
    late = store.read_snapshot(
        "FLOW_USDT_SWAP", "1h", 0, 0, as_of_ms=250,
        purpose="feature", coverage_policy="available",
    )

    assert "funding" not in early.rows[0]
    assert "oi" not in early.rows[0]
    assert late.rows[0]["funding"] == 0.001
    assert late.rows[0]["oi"] == 1200


def test_price_revision_cannot_silently_inherit_unbound_enrichment(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "FLOW_USDT_SWAP", "1h", [_row(0, 100.0, funding=0.001, oi=1200)],
        source="combined-A", available_at_ms=100,
    )
    store.upsert_candles(
        "FLOW_USDT_SWAP", "1h", [_row(0, 101.0)],
        source="prices-B", available_at_ms=200,
    )

    row = store.read_snapshot(
        "FLOW_USDT_SWAP", "1h", 0, 0, as_of_ms=250,
        purpose="feature", coverage_policy="available",
    ).rows[0]

    assert row["close"] == 101.0
    assert "funding" not in row
    assert "oi" not in row


def test_json_inner_revision_changes_backend_independent_snapshot_id(tmp_path):
    rows = _rows(3)
    path = _write_json(tmp_path, rows)
    before = fingerprint_for_file(path)
    changed = [dict(row) for row in rows]
    changed[1]["close"] += 0.25
    path.write_text(json.dumps(changed), encoding="utf-8")

    assert fingerprint_for_file(path) != before


def test_canonical_json_manifest_binds_arbitrary_inner_fields(tmp_path):
    rows = _rows(3)
    rows[1]["provider_seq"] = {"partition": 2, "offset": 10}
    path = _write_json(tmp_path, rows)
    before = load_canonical_candles(
        tmp_path, "BTC_USDT_SWAP", "1h", purpose="full-content",
        coverage_policy="gap_free",
    )
    changed = [dict(row) for row in rows]
    changed[1]["provider_seq"] = {"partition": 2, "offset": 11}
    path.write_text(json.dumps(changed), encoding="utf-8")
    after = load_canonical_candles(
        tmp_path, "BTC_USDT_SWAP", "1h", purpose="full-content",
        coverage_policy="gap_free",
    )

    assert before.rows[1]["provider_seq"]["offset"] == 10
    assert after.rows[1]["provider_seq"]["offset"] == 11
    assert before.manifest.snapshot_id != after.manifest.snapshot_id


def test_identical_legacy_json_relocation_preserves_manifest_identity(tmp_path):
    rows = _rows(3)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _write_json(first_root, rows).rename(
        first_root / "market_data" / "1h" / "BTC_USDT_SWAP_a_1h.json"
    )
    _write_json(second_root, rows).rename(
        second_root / "market_data" / "1h" / "BTC_USDT_SWAP_b_1h.json"
    )

    first = load_canonical_candles(
        first_root, "BTC_USDT_SWAP", "1h", purpose="relocation",
        coverage_policy="gap_free",
    )
    second = load_canonical_candles(
        second_root, "BTC_USDT_SWAP", "1h", purpose="relocation",
        coverage_policy="gap_free",
    )

    assert first.manifest.snapshot_id == second.manifest.snapshot_id
    assert first.manifest.evidence_hash == second.manifest.evidence_hash


def test_legacy_json_gateway_rejects_unconfirmed_rows_and_identity_changes(tmp_path):
    rows = _rows(3)
    path = _write_json(tmp_path, rows)
    confirmed = load_canonical_candles(
        tmp_path, "BTC_USDT_SWAP", "1h", purpose="confirm-gate",
        coverage_policy="available",
    )
    changed = [dict(row) for row in rows]
    changed[1]["confirm"] = "0"
    path.write_text(json.dumps(changed), encoding="utf-8")
    filtered = load_canonical_candles(
        tmp_path, "BTC_USDT_SWAP", "1h", purpose="confirm-gate",
        coverage_policy="available",
    )

    assert len(confirmed.rows) == 3
    assert [row["ts"] for row in filtered.rows] == [0, 2 * HOUR]
    assert confirmed.manifest.snapshot_id != filtered.manifest.snapshot_id


def test_partial_sqlite_and_complete_json_select_one_manifest_by_policy(tmp_path):
    complete = _rows(30)
    _write_json(tmp_path, complete)
    CandleStore(tmp_path).upsert_candles(
        "BTC_USDT_SWAP", "1h", complete[:10], source="partial-sqlite",
        available_at_ms=100,
    )

    selected = load_canonical_candles(
        tmp_path, "BTC_USDT_SWAP", "1h", start_ts=0, end_ts=29 * HOUR,
        purpose="experiment", coverage_policy="complete_range",
    )

    assert selected.source == "json"
    assert len(selected.rows) == 30
    assert selected.manifest.coverage_status == "complete"
    assert selected.manifest.snapshot_id


def test_same_purpose_and_as_of_select_same_manifest_for_all_consumers(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "BTC_USDT_SWAP", "1h", _rows(30), source="fixture", available_at_ms=100,
    )

    first = load_canonical_candles(
        tmp_path, "BTC_USDT_SWAP", "1h", start_ts=0, end_ts=29 * HOUR,
        as_of_ms=150, purpose="experiment", coverage_policy="complete_range",
    )
    second = load_canonical_candles(
        tmp_path, "BTC_USDT_SWAP", "1h", start_ts=0, end_ts=29 * HOUR,
        as_of_ms=150, purpose="experiment", coverage_policy="complete_range",
    )

    assert first.rows == second.rows
    assert first.manifest.snapshot_id == second.manifest.snapshot_id
    assert first.manifest.to_dict() == second.manifest.to_dict()


def test_market_data_packet_rebuild_preserves_acquisition_identity(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "BTC_USDT_SWAP", "15m", _rows(220, step=15 * MINUTE),
        source="fixture", observed_at_ms=90, available_at_ms=100,
        acquired_at_ms=110,
    )
    selected = load_canonical_candles(
        tmp_path, "BTC_USDT_SWAP", "15m", start_ts=0, end_ts=219 * 15 * MINUTE,
        as_of_ms=150, purpose="decision", coverage_policy="complete_range",
    )

    first = build_market_data_packet(
        scanner_event_id="se1", symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP", timeframe="15m", mode="live",
        candles=selected.rows, snapshot_manifest=selected.manifest.to_dict(),
    )
    second = build_market_data_packet(
        scanner_event_id="se1", symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP", timeframe="15m", mode="live",
        candles=selected.rows, snapshot_manifest=selected.manifest.to_dict(),
    )

    assert first.data_packet_id == second.data_packet_id
    assert first.available_at == second.available_at
    assert first.snapshot_manifest["row_count"] == len(first.ohlcv_window)
    assert first.snapshot_manifest_id != selected.manifest.snapshot_id


def test_market_data_packet_binds_separate_decision_and_future_evidence():
    candles = _rows(320, step=15 * MINUTE)
    changed = [dict(row) for row in candles]
    changed[-1]["close"] += 0.25
    changed[-1]["high"] += 0.25

    first = build_market_data_packet(
        scanner_event_id="se1", symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP", timeframe="15m", mode="validation",
        candles=candles,
    )
    second = build_market_data_packet(
        scanner_event_id="se1", symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP", timeframe="15m", mode="validation",
        candles=changed,
    )

    assert first.data_packet_id == second.data_packet_id
    assert first.content_hash == second.content_hash
    assert first.future_evidence_id != second.future_evidence_id
    assert first.future_content_hash != second.future_content_hash


def test_v1_to_v2_synthetic_dry_run_reports_enrichment_and_provenance_non_parity(tmp_path):
    _write_json(tmp_path, [_row(0, funding=0.001, oi=1200)])

    report = migrate_json_candles(tmp_path, target_schema="v2")

    assert report["schema"] == "strategy_lab_candle_migration.v2"
    assert report["mode"] == "dry_report"
    assert report["rows_accepted"] == 0
    assert report["provenance_non_parity_files"] == 1
    assert report["rollback_reader"] == "v1"
    assert not CandleStore(tmp_path).exists


def test_v1_to_v2_apply_is_idempotent_and_preserves_full_content(tmp_path):
    _write_json(tmp_path, [_row(0, funding=0.001, provider_seq={"offset": 7})])

    first = migrate_json_candles(
        tmp_path, apply=True, target_schema="v2", migration_available_at_ms=500,
    )
    second = migrate_json_candles(
        tmp_path, apply=True, target_schema="v2", migration_available_at_ms=900,
    )
    store = CandleStore(tmp_path)
    with sqlite3.connect(store.path) as conn:
        revision_count = conn.execute("SELECT COUNT(*) FROM candle_revisions").fetchone()[0]
    row = store.read_snapshot(
        "BTC_USDT_SWAP", "1h", 0, 0, as_of_ms=600,
        purpose="migration-parity", coverage_policy="available",
    ).rows[0]

    assert first["parity_failures"] == 0
    assert second["parity_failures"] == 0
    assert second["rows_accepted"] == 0
    assert revision_count == 1
    assert row["provider_seq"] == {"offset": 7}


def test_v1_to_v2_failed_file_rolls_back_and_can_resume(tmp_path, monkeypatch):
    path = _write_json(tmp_path, [_row(0, provider_seq={"offset": 7})])
    original = CandleStore.upsert_candles
    calls = {"count": 0}

    def fail_once(self, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise CandleStoreError("synthetic migration failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CandleStore, "upsert_candles", fail_once)
    failed = migrate_json_candles(
        tmp_path, apply=True, target_schema="v2", migration_available_at_ms=500,
    )
    resumed = migrate_json_candles(
        tmp_path, apply=True, target_schema="v2", migration_available_at_ms=500,
    )

    assert failed["failed_files"] == 1
    assert resumed["failed_files"] == 0
    assert resumed["rows_accepted"] == 1
    assert path.exists()
    with sqlite3.connect(CandleStore(tmp_path).path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM candle_revisions").fetchone()[0] == 1


def test_v1_to_v2_malformed_source_reports_failure_and_resumes(tmp_path):
    path = _write_json(tmp_path, [_row(0)])
    path.write_text("[{broken", encoding="utf-8")

    failed = migrate_json_candles(
        tmp_path, apply=True, target_schema="v2", migration_available_at_ms=500,
    )
    path.write_text(json.dumps([_row(0)]), encoding="utf-8")
    resumed = migrate_json_candles(
        tmp_path, apply=True, target_schema="v2", migration_available_at_ms=500,
    )

    assert failed["failed_files"] == 1
    assert resumed["failed_files"] == 0
    assert resumed["rows_accepted"] == 1


def test_v2_reader_rollback_selects_v1_without_deleting_v2_revisions(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "BTC_USDT_SWAP", "1m", [_row(0, 100.0)], source="A", available_at_ms=100,
    )
    store.upsert_candles(
        "BTC_USDT_SWAP", "1m", [_row(0, 101.0)], source="B", available_at_ms=200,
    )

    historical = store.read_snapshot(
        "BTC_USDT_SWAP", "1m", 0, 0, as_of_ms=150,
        purpose="rollback-test", coverage_policy="available",
    )
    legacy_latest = store.read(
        "BTC_USDT_SWAP", "1m", 0, 0, reader_version="v1",
    )
    with sqlite3.connect(store.path) as conn:
        revisions = conn.execute("SELECT COUNT(*) FROM candle_revisions").fetchone()[0]

    assert historical.rows[0]["close"] == 100.0
    assert legacy_latest[0]["close"] == 101.0
    assert revisions == 2


def test_hard_validation_rebuild_requires_exact_snapshot_binding(tmp_path, monkeypatch):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "BTC_USDT_SWAP", "1h", _rows(30), source="fixture", available_at_ms=100,
    )
    selected = load_canonical_candles(
        tmp_path, "BTC_USDT_SWAP", "1h", purpose="experiment",
        coverage_policy="gap_free",
    )
    monkeypatch.setattr(
        "src.research_lab.hard_validation_export.generate_signals",
        lambda *_args, **_kwargs: [{"idx": 1, "side": "long"}],
    )
    monkeypatch.setattr(
        "src.research_lab.hard_validation_export.annotate_signals_with_regime",
        lambda _candles, signals, _context: signals,
    )
    monkeypatch.setattr(
        "src.research_lab.hard_validation_export.filter_signals",
        lambda signals, _filters: signals,
    )
    monkeypatch.setattr(
        "src.research_lab.hard_validation_export.simulate_trades",
        lambda *_args, **_kwargs: [{"trade": "bound"}],
    )
    row = {"symbol": "BTC_USDT_SWAP", "family": "momentum_breakout", "params": {}}
    context = {"_timeframe": "1h", "_filters": {}}
    metrics = {"data_file_timeframe": "1h", "data_file_label": "sqlite:BTC_USDT_SWAP:1h"}

    assert _rebuild_trades_from_result(tmp_path, row, metrics, context) == []
    metrics["data_snapshot_id"] = selected.manifest.snapshot_id
    assert _rebuild_trades_from_result(tmp_path, row, metrics, context) == [{"trade": "bound"}]


def test_outcome_market_context_binds_snapshot_manifest(tmp_path):
    CandleStore(tmp_path).upsert_candles(
        "BTC_USDT_SWAP", "1h", _rows(30), source="fixture", available_at_ms=100,
    )
    context = _market_context(
        {
            "symbol": "BTC_USDT_SWAP", "timeframe": "1h",
            "boundary_ts": 10 * HOUR, "max_hold_bars": 3,
            "observed_entry": 110.0, "side": "long",
        },
        tmp_path,
    )

    assert context["schema"] == "OutcomeMarketContext.v2"
    assert context["data_snapshot_id"].startswith("csm_")
    assert context["data_evidence_hash"]
    assert context["data_provenance_status"] == "complete"


def test_paper_readiness_fails_closed_for_legacy_unknown_candles(tmp_path):
    card = SetupCard(
        setup_id="setup-c1", candidate_id="c1", symbol="BTC_USDT_SWAP",
        timeframe="1h", strategy_id="momentum_breakout",
        params={"direction": "long", "stop_pct": 2.0, "take_pct": 4.0, "hold_bars": 3},
        filters={}, data_window={"start_ts": 0, "end_ts": 29 * HOUR, "n_bars": 30},
        lite_status="FORWARD_PAPER", hard_status="PAPER_FORWARD_READY",
        checks_summary={}, failed_checks=[], risk_flags=[], entry_exit_summary="ready",
        regime_tags=[], paper_forward_ready=True,
    )
    write_setup_library(tmp_path, [card], dry_run=False)
    _write_json(tmp_path, _rows(30))

    readiness = summarize_paper_readiness(tmp_path)

    assert readiness["local_data_ready"] == 0
    assert readiness["local_data_unproven"] == 1
    assert readiness["blocked_reasons"]["local_candles_provenance_unknown"] == 1
    assert readiness["data_manifests"]["setup-c1"]["snapshot_id"].startswith("csm_")
    assert readiness["data_manifests"]["setup-c1"]["provenance_status"] == "legacy_unknown"


def test_replaying_identical_old_revision_does_not_roll_back_projection(tmp_path):
    store = CandleStore(tmp_path)
    old = _row(0, 100.0)
    new = _row(0, 101.0)
    store.upsert_candles(
        "BTC_USDT_SWAP", "1h", [old], source="A",
        observed_at_ms=90, available_at_ms=100, acquired_at_ms=110,
    )
    store.upsert_candles(
        "BTC_USDT_SWAP", "1h", [new], source="B",
        observed_at_ms=190, available_at_ms=200, acquired_at_ms=210,
    )
    store.upsert_candles(
        "BTC_USDT_SWAP", "1h", [old], source="A",
        observed_at_ms=90, available_at_ms=100, acquired_at_ms=310,
    )

    assert store.read("BTC_USDT_SWAP", "1h", 0, 0)[0]["close"] == 101.0
    assert store.read("BTC_USDT_SWAP", "1h", 0, 0, reader_version="v1")[0]["close"] == 101.0
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM candle_revisions").fetchone()[0] == 2


def test_reingesting_snapshot_row_does_not_embed_internal_lineage_as_content(tmp_path):
    first = CandleStore(tmp_path / "first")
    first.upsert_candles(
        "BTC_USDT_SWAP", "1h", [_row(0)], source="fixture", available_at_ms=100,
    )
    selected = first.read_snapshot(
        "BTC_USDT_SWAP", "1h", 0, 0, as_of_ms=150,
        purpose="copy", coverage_policy="available",
    )
    second = CandleStore(tmp_path / "second")
    second.upsert_candles(
        "BTC_USDT_SWAP", "1h", selected.rows, source="fixture", available_at_ms=100,
    )
    copied = second.read_snapshot(
        "BTC_USDT_SWAP", "1h", 0, 0, as_of_ms=150,
        purpose="copy", coverage_policy="available",
    )

    assert copied.rows[0]["_content_hash"] == selected.rows[0]["_content_hash"]
    assert not any(
        key.startswith("_") for key in copied.rows[0]["_field_provenance"]
    )


def test_supplied_full_manifest_cannot_bind_future_into_decision_identity():
    candles = _rows(320, step=15 * MINUTE)
    changed = [dict(row) for row in candles]
    changed[-1]["close"] += 0.25
    changed[-1]["high"] += 0.25
    first_manifest = build_snapshot_manifest(
        symbol="BTC_USDT_SWAP", timeframe="15m", rows=candles,
        start_ts=0, end_ts=319 * 15 * MINUTE, as_of_ms=None,
        purpose="validation_input", coverage_policy="complete_range",
        source_backend="fixture",
    )
    second_manifest = build_snapshot_manifest(
        symbol="BTC_USDT_SWAP", timeframe="15m", rows=changed,
        start_ts=0, end_ts=319 * 15 * MINUTE, as_of_ms=None,
        purpose="validation_input", coverage_policy="complete_range",
        source_backend="fixture",
    )

    first = build_market_data_packet(
        scanner_event_id="se1", symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP", timeframe="15m", mode="validation",
        candles=candles, snapshot_manifest=first_manifest.to_dict(),
    )
    second = build_market_data_packet(
        scanner_event_id="se1", symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP", timeframe="15m", mode="validation",
        candles=changed, snapshot_manifest=second_manifest.to_dict(),
    )

    assert first.data_packet_id == second.data_packet_id
    assert first.snapshot_manifest["row_count"] == len(first.ohlcv_window)
    assert first.future_evidence_id != second.future_evidence_id
    assert first.future_evidence_hash != second.future_evidence_hash


def test_forged_supplied_manifest_is_recomputed_from_decision_rows():
    candles = _rows(220, step=15 * MINUTE)
    forged = {
        "snapshot_id": "csm_forged", "row_count": 192,
        "first_ts": 28 * 15 * MINUTE, "last_ts": 219 * 15 * MINUTE,
        "content_hash": "forged", "evidence_hash": "forged",
        "provenance_status": "complete", "source_backend": "sqlite",
    }

    packet = build_market_data_packet(
        scanner_event_id="se1", symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP", timeframe="15m", mode="live",
        candles=candles, snapshot_manifest=forged,
    )

    assert packet.snapshot_manifest_id != "csm_forged"
    assert packet.snapshot_manifest["provenance_status"] == "legacy_unknown"
    assert "availability_provenance_unknown" in packet.data_quality_flags


def test_packet_manifest_marks_rows_after_forged_as_of_invalid(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "BTC_USDT_SWAP", "15m", _rows(220, step=15 * MINUTE),
        source="fixture", available_at_ms=200,
    )
    selected = load_canonical_candles(
        tmp_path, "BTC_USDT_SWAP", "15m", purpose="decision",
        coverage_policy="gap_free",
    )
    forged = selected.manifest.to_dict()
    forged["as_of_ms"] = 100

    packet = build_market_data_packet(
        scanner_event_id="se1", symbol="BTC_USDT_SWAP",
        instrument="BTC-USDT-SWAP", timeframe="15m", mode="live",
        candles=selected.rows, snapshot_manifest=forged,
    )

    assert packet.snapshot_manifest["provenance_status"] == "invalid"
    assert "availability_provenance_unknown" in packet.data_quality_flags


def test_snapshot_manifest_detects_mutated_content_under_stale_revision_metadata(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "BTC_USDT_SWAP", "1h", [_row(0)], source="fixture", available_at_ms=100,
    )
    selected = store.read_snapshot(
        "BTC_USDT_SWAP", "1h", 0, 0, as_of_ms=150,
        purpose="integrity", coverage_policy="available",
    )
    mutated = [dict(selected.rows[0])]
    mutated[0]["close"] += 0.5
    mutated[0]["high"] += 0.5

    manifest = build_snapshot_manifest(
        symbol="BTC_USDT_SWAP", timeframe="1h", rows=mutated,
        start_ts=0, end_ts=0, as_of_ms=150, purpose="integrity",
        coverage_policy="available", source_backend="caller",
    )

    assert manifest.provenance_status == "invalid"


def test_snapshot_manifest_rejects_impossible_or_forged_revision_lineage(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "BTC_USDT_SWAP", "1h", [_row(0)], source="fixture",
        observed_at_ms=90, available_at_ms=100, acquired_at_ms=110,
    )
    selected = store.read_snapshot(
        "BTC_USDT_SWAP", "1h", 0, 0, as_of_ms=150,
        purpose="integrity", coverage_policy="available",
    )
    impossible = [dict(selected.rows[0])]
    impossible[0]["_observed_at_ms"] = 300
    forged = [dict(selected.rows[0])]
    forged[0]["_revision_id"] = "cr_forged"
    forged[0]["_field_provenance"] = {
        field: {**ref, "revision_id": "cr_forged"}
        for field, ref in forged[0]["_field_provenance"].items()
    }

    def manifest(rows):
        return build_snapshot_manifest(
            symbol="BTC_USDT_SWAP", timeframe="1h", rows=rows,
            start_ts=0, end_ts=0, as_of_ms=150, purpose="integrity",
            coverage_policy="available", source_backend="caller",
        )

    assert manifest(impossible).provenance_status == "invalid"
    assert manifest(forged).provenance_status == "invalid"


def test_direct_experiment_json_fallback_binds_snapshot_id(tmp_path):
    path = _write_json(tmp_path, _rows(80))

    loaded = _load_experiment_candles(
        str(path), "BTC_USDT_SWAP", timeframe="1h", candle_store=None,
    )

    assert loaded is not None
    assert loaded[2]
    assert loaded[3].startswith("csm_")


def test_queued_experiment_fails_closed_when_snapshot_drifts(tmp_path):
    store = CandleStore(tmp_path)
    store.upsert_candles(
        "BTC_USDT_SWAP", "1h", _rows(80), source="fixture", available_at_ms=100,
    )
    queued = load_canonical_candles(
        tmp_path, "BTC_USDT_SWAP", "1h", purpose="experiment",
        coverage_policy="gap_free",
    )
    store.upsert_candles(
        "BTC_USDT_SWAP", "1h", [_row(10 * HOUR, 999.0)],
        source="correction", available_at_ms=200,
    )
    spec = ExperimentSpec(
        experiment_id="queued-drift", data_glob=str(Path(tmp_path) / "missing-{symbol}.json"),
        symbols=["BTC_USDT_SWAP"], families=["momentum_breakout"],
        parameter_grid={"momentum_breakout": [{}]}, timeframe="1h",
        data_snapshot_id=queued.manifest.snapshot_id,
        data_evidence_hash=queued.manifest.evidence_hash,
    )

    with pytest.raises(RuntimeError, match="queued candle snapshot drift"):
        evaluate_spec(spec, candle_store=store)
