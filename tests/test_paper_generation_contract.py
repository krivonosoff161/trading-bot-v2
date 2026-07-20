import json
from pathlib import Path

import pytest

from src.research_lab.main_paper_bridge import export_main_paper_instructions
from src.research_lab.main_paper_consumer import consume_main_paper_instructions
from src.research_lab.main_paper_runtime import observe_main_paper_runtime
from src.research_lab.main_paper_runtime_adapter import build_main_paper_runtime_queue
from src.research_lab.main_paper_trade_ledger import build_main_paper_trade_ledger
from src.research_lab.paper_generation_contract import (
    PaperGenerationContext,
    PaperGenerationMismatch,
)
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import append_signal


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
        reason_now="synthetic v2 generation test",
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
        },
    )


class _Provider:
    name = "synthetic-v2"

    def fetch_ohlcv(self, _symbol, _timeframe, start_ts, end_ts):
        rows = [
            {"ts": 3_600_001, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "vol": 1},
            {"ts": 7_200_001, "open": 100.5, "high": 111.0, "low": 100.0, "close": 110.0, "vol": 1},
        ]
        return [row for row in rows if start_ts <= row["ts"] <= end_ts]


def test_v2_bridge_consumer_queue_chain_recomputes_every_stage(tmp_path):
    append_signal(tmp_path, _signal())
    source_context = PaperGenerationContext(
        run_id="run-v2",
        producer_generation_id="producer-v2",
        input_digest="sha256:producer-v2",
    )

    bridge = export_main_paper_instructions(tmp_path, generation_context=source_context)
    consumer = consume_main_paper_instructions(
        tmp_path,
        expected_run_id="run-v2",
        expected_input_digest="sha256:producer-v2",
    )
    queue = build_main_paper_runtime_queue(
        tmp_path,
        limit=0,
        expected_run_id="run-v2",
        expected_input_digest=bridge["stage_output_digest"],
    )
    observer = observe_main_paper_runtime(
        tmp_path,
        apply=True,
        provider=_Provider(),
        now_ms=8_000_000,
        expected_run_id="run-v2",
        expected_input_digest=consumer["stage_output_digest"],
        persist_observation=lambda _row, manifest: {
            **manifest,
            "observation_id": "synthetic-persisted-observation",
        },
    )
    trades = build_main_paper_trade_ledger(
        tmp_path,
        expected_run_id="run-v2",
        expected_input_digest=observer["stage_output_digest"],
    )

    assert bridge["generation_status"] == "stage_completed"
    assert consumer["stage_input_digest"] == bridge["stage_output_digest"]
    assert queue["stage_input_digest"] == consumer["stage_output_digest"]
    assert queue["queued"] == 1
    assert queue["scheduling_limit_deferred_to_durable_cursor"] is True
    assert observer["stage_input_digest"] == queue["stage_output_digest"]
    assert observer["items"][0]["observation_manifest"]["rows_digest"].startswith("sha256:")
    assert trades["stage_input_digest"] == observer["stage_output_digest"]
    assert trades["paper_account_ledger"]["generation_status"] == (
        "pending_transactional_finalize"
    )
    row = json.loads(Path(queue["snapshot_path"]).read_text(encoding="utf-8"))["items"][0]
    assert row["paper_generation_run_id"] == "run-v2"
    assert row["source_producer_generation_id"] == "producer-v2"
    assert row["source_member_payload_digest"].startswith("sha256:")
    assert row["source_validation_generation_id"] == "validation-generation-v2"


def test_tampered_bridge_stage_fails_closed_before_consumer(tmp_path):
    append_signal(tmp_path, _signal())
    context = PaperGenerationContext("run-v2", "producer-v2", "sha256:producer-v2")
    bridge = export_main_paper_instructions(tmp_path, generation_context=context)
    snapshot = Path(bridge["snapshot_path"])
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["items"][0]["side"] = "short"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PaperGenerationMismatch, match="mismatch"):
        consume_main_paper_instructions(tmp_path, expected_run_id="run-v2")


def test_legacy_stage_is_explicitly_display_only(tmp_path):
    append_signal(tmp_path, _signal())
    summary = export_main_paper_instructions(tmp_path)

    assert summary["generation_status"] == "legacy_unversioned_projection"
    assert summary["current_generation_compatible"] is False
    assert summary["display_only"] is True
    with pytest.raises(PaperGenerationMismatch, match="legacy_unversioned_projection"):
        consume_main_paper_instructions(tmp_path, expected_run_id="run-v2")
