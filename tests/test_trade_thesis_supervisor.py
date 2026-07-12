import ast
import json
from pathlib import Path

from src.research_lab.trade_thesis_supervisor import (
    build_trade_thesis_supervisor,
    replay_symbol_fsm,
    write_trade_thesis_supervisor,
)


def _trade(**overrides):
    row = {
        "schema": "PaperProductTrade.v1",
        "paper_product_trade_id": "ppt_1",
        "source_signal_id": "sig_1",
        "okx_inst_id": "KAITO-USDT-SWAP",
        "timeframe": "4h",
        "side": "short",
        "setup_family": "reversal_fade",
        "status": "opened_paper",
        "source": "farm",
        "live_ready": False,
        "ready_strategy_id": "",
        "created_at": "2026-07-08T12:10:00+00:00",
        "paper_only": True,
        "execution_allowed": False,
    }
    row.update(overrides)
    return row


def _write_ledger(tmp_path, rows):
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "paper_product_trades.json").write_text(
        json.dumps({
            "schema": "paper_product_trade_ledger.v1",
            "trades": len(rows),
            "items": rows,
            "paper_only": True,
            "execution_allowed": False,
        }),
        encoding="utf-8",
    )


def test_trade_thesis_supervisor_keeps_higher_timeframe_primary_thesis(tmp_path):
    _write_ledger(
        tmp_path,
        [
            _trade(source_signal_id="sig_short_4h", paper_product_trade_id="ppt_short_4h"),
            _trade(
                source_signal_id="sig_long_15m",
                paper_product_trade_id="ppt_long_15m",
                timeframe="15m",
                side="long",
                setup_family="continuation",
                created_at="2026-07-08T13:00:00+00:00",
            ),
        ],
    )

    summary = build_trade_thesis_supervisor(tmp_path)

    assert summary["schema"] == "trade_thesis_supervisor.v1"
    assert summary["theses"] == 1
    assert summary["active_trades"] == 2
    assert summary["items"][0]["side"] == "short"
    assert summary["items"][0]["primary_timeframe"] == "4h"
    assert summary["by_event_type"] == {
        "countertrend_bounce": 1,
        "primary_thesis": 1,
        "scenario_opened": 1,
    }
    assert summary["by_action"] == {
        "hold_primary_tighten_watch": 1,
        "start_watch": 1,
        "track_primary": 1,
    }
    assert summary["execution_allowed"] is False


def test_trade_thesis_supervisor_marks_equal_timeframe_opposite_as_invalidation_warning(tmp_path):
    _write_ledger(
        tmp_path,
        [
            _trade(source_signal_id="sig_short_1h", paper_product_trade_id="ppt_short_1h", timeframe="1h"),
            _trade(
                source_signal_id="sig_long_1h",
                paper_product_trade_id="ppt_long_1h",
                timeframe="1h",
                side="long",
                created_at="2026-07-08T13:00:00+00:00",
            ),
        ],
    )

    summary = build_trade_thesis_supervisor(tmp_path)

    assert summary["by_event_type"]["invalidation_warning"] == 1
    assert summary["by_action"]["tighten_or_flip_watch"] == 1


def test_trade_thesis_supervisor_writes_private_artifacts(tmp_path):
    _write_ledger(tmp_path, [_trade()])

    summary = write_trade_thesis_supervisor(tmp_path)

    assert Path(summary["snapshot_path"]).exists()
    assert Path(summary["theses_jsonl_path"]).exists()
    assert Path(summary["events_jsonl_path"]).exists()
    assert summary["paper_only"] is True
    assert summary["execution_allowed"] is False


def test_trade_thesis_id_survives_new_confirmation_and_event_log_is_idempotent(tmp_path):
    _write_ledger(tmp_path, [_trade()])
    first = write_trade_thesis_supervisor(tmp_path)
    first_id = first["items"][0]["thesis_id"]

    _write_ledger(
        tmp_path,
        [
            _trade(),
            _trade(
                source_signal_id="sig_2",
                paper_product_trade_id="ppt_2",
                timeframe="15m",
                created_at="2026-07-08T13:00:00+00:00",
            ),
        ],
    )
    second = write_trade_thesis_supervisor(tmp_path)
    second_id = second["items"][0]["thesis_id"]
    event_log = Path(second["events_jsonl_path"])
    before = event_log.read_bytes()
    third = write_trade_thesis_supervisor(tmp_path)

    assert first_id == second_id
    assert second["events_added"] > 0
    assert third["events_added"] == 0
    assert event_log.read_bytes() == before


def test_trade_thesis_closes_when_last_active_signal_ends(tmp_path):
    _write_ledger(tmp_path, [_trade()])
    opened = write_trade_thesis_supervisor(tmp_path)
    thesis_id = opened["items"][0]["thesis_id"]

    _write_ledger(
        tmp_path,
        [
            _trade(
                status="reviewed",
                outcome={"result": "take", "net_pct": 1.25},
            )
        ],
    )
    closed = write_trade_thesis_supervisor(tmp_path)

    thesis = closed["items"][0]
    closure = [row for row in closed["event_items"] if row["event_type"] == "scenario_closed"][-1]
    assert thesis["thesis_id"] == thesis_id
    assert thesis["status"] == "closed"
    assert thesis["fsm_state"] == "closed"
    assert thesis["active_signals"] == 0
    assert thesis["close_reason"] == "take"
    assert closure["supervisor_action"] == "stop_watch"
    assert closure["terminal_result"] == "take"
    assert closure["terminal_net_pct"] == 1.25


def test_trade_thesis_supervisor_has_no_live_order_imports():
    path = Path("src/research_lab/trade_thesis_supervisor.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "main",
        "src.exchange",
        "src.exchange.okx_client",
        "src.utils.telegram",
        "dotenv",
        "ccxt",
        "hmac",
        "requests",
        "aiohttp",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not (imported & forbidden)


def test_market_context_snapshot_is_versioned_and_references_visual_evidence(tmp_path):
    _write_ledger(tmp_path, [_trade(visual_evidence={
        "reference": "derived/charts/sig_1.png",
        "content_hash": "a" * 64,
        "observed_at": "2026-07-08T12:10:00+00:00",
    })])

    thesis = build_trade_thesis_supervisor(tmp_path)["items"][0]
    snapshot = thesis["market_context_snapshot"]

    assert snapshot["schema"] == "MarketContextSnapshot.v1"
    assert snapshot["version"] == 1
    assert snapshot["paper_only"] is True
    assert thesis["visual_evidence"][0]["schema"] == "VisualEvidence.v1"
    assert thesis["visual_evidence"][0]["reference"] == "derived/charts/sig_1.png"
    assert thesis["visual_evidence"][0]["content_hash"] == "a" * 64
    assert snapshot["visual_evidence_ids"] == [thesis["visual_evidence"][0]["evidence_id"]]


def test_symbol_fsm_replay_handles_duplicate_stale_contradiction_and_reversal():
    rows = [
        _trade(source_signal_id="sig_a", timeframe="1h", side="long", created_at="2026-07-08T12:00:00+00:00"),
        _trade(source_signal_id="sig_a", timeframe="1h", side="long", created_at="2026-07-08T12:01:00+00:00"),
        _trade(source_signal_id="sig_stale", timeframe="15m", side="long", created_at="2026-07-08T11:59:00+00:00"),
        _trade(source_signal_id="sig_conflict", timeframe="15m", side="short", created_at="2026-07-08T12:02:00+00:00"),
        _trade(source_signal_id="sig_reverse", timeframe="4h", side="short", created_at="2026-07-08T12:03:00+00:00"),
    ]

    first = replay_symbol_fsm(rows)
    second = replay_symbol_fsm(rows)

    assert first == second
    assert [item["event_type"] for item in first["transitions"]] == [
        "activated", "confirmation", "duplicate_ignored", "contradiction", "reversal"
    ]
    assert first["state"] == "reversed"
    assert first["primary_side"] == "short"
    assert first["primary_signal_id"] == "sig_reverse"
    assert first["execution_allowed"] is False


def test_llm_advice_cannot_change_deterministic_fsm():
    base = _trade(source_signal_id="sig_a", side="long")
    advised = dict(base, llm_advice={"side": "short", "action": "reverse", "confidence": 1.0})

    assert replay_symbol_fsm([base]) == replay_symbol_fsm([advised])


def test_symbol_fsm_replay_is_independent_of_ledger_order():
    rows = [
        _trade(source_signal_id="sig_a", timeframe="1h", side="long", created_at="2026-07-08T12:00:00+00:00"),
        _trade(source_signal_id="sig_b", timeframe="4h", side="short", created_at="2026-07-08T13:00:00+00:00"),
    ]
    assert replay_symbol_fsm(rows) == replay_symbol_fsm(list(reversed(rows)))


def test_symbol_fsm_records_degraded_data_and_persists_replay_evidence(tmp_path):
    row = _trade(data_quality="degraded")
    _write_ledger(tmp_path, [row])
    thesis = build_trade_thesis_supervisor(tmp_path)["items"][0]
    assert thesis["fsm_state"] == "data_degraded"
    assert thesis["fsm_transitions"][0]["event_type"] == "data_degraded"
    assert thesis["fsm_watermark"]


def test_symbol_fsm_rejects_late_arrival_against_persisted_watermark():
    initial = _trade(
        source_signal_id="sig_new", timeframe="1h", side="long",
        created_at="2026-07-08T12:00:00+00:00",
    )
    first = replay_symbol_fsm([initial])
    previous = {
        "fsm_state": first["state"], "fsm_watermark": first["watermark"],
        "fsm_transitions": first["transitions"], "side": first["primary_side"],
        "primary_timeframe": first["primary_timeframe"],
        "primary_signal_id": first["primary_signal_id"],
    }
    late = _trade(
        source_signal_id="sig_late", timeframe="15m", side="short",
        created_at="2026-07-08T11:00:00+00:00",
    )
    replayed = replay_symbol_fsm([initial, late], previous)
    assert any(
        item["signal_id"] == "sig_late" and item["event_type"] == "stale_ignored"
        for item in replayed["transitions"]
    )
    assert replayed["primary_signal_id"] == "sig_new"


def test_symbol_fsm_reselects_when_persisted_primary_is_no_longer_active():
    long_row = _trade(
        source_signal_id="sig_long", timeframe="1h", side="long",
        created_at="2026-07-08T12:00:00+00:00",
    )
    short_row = _trade(
        source_signal_id="sig_short", timeframe="4h", side="short",
        created_at="2026-07-08T13:00:00+00:00",
    )
    first = replay_symbol_fsm([long_row, short_row])
    previous = {
        "fsm_state": first["state"], "fsm_watermark": first["watermark"],
        "fsm_transitions": first["transitions"], "side": first["primary_side"],
        "primary_timeframe": first["primary_timeframe"],
        "primary_signal_id": first["primary_signal_id"],
    }
    remaining = replay_symbol_fsm([long_row], previous)
    assert remaining["primary_signal_id"] == "sig_long"
    assert remaining["primary_side"] == "long"
    assert remaining["transitions"][-2]["event_type"] == "primary_ended"
    assert remaining["transitions"][-1]["event_type"] == "activated"
    stable = replay_symbol_fsm([long_row], {
        "fsm_state": remaining["state"], "fsm_watermark": remaining["watermark"],
        "fsm_transitions": remaining["transitions"], "side": remaining["primary_side"],
        "primary_timeframe": remaining["primary_timeframe"],
        "primary_signal_id": remaining["primary_signal_id"],
    })
    assert stable["transitions"] == remaining["transitions"]
