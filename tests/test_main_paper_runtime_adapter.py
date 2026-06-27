import ast
import json
from pathlib import Path

import pytest

from src.research_lab.main_paper_bridge import export_main_paper_instructions
from src.research_lab.main_paper_consumer import consume_main_paper_instructions
from src.research_lab.main_paper_runtime_adapter import (
    MainPaperRuntimeQueueItem,
    build_main_paper_runtime_queue,
)
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import append_signal


def _sig(
    signal_id: str,
    *,
    family: str = "early_tp_tactical",
    tf: str = "1h",
    source: str = "farm",
) -> PaperActionSignal:
    return PaperActionSignal(
        signal_id=signal_id,
        source=source,
        symbol="BTC_USDT_SWAP",
        okx_inst_id="BTC-USDT-SWAP",
        timeframe=tf,
        side="long",
        setup_family=family,
        entry_zone=[100.0, 101.0],
        stop_loss=95.0,
        invalidation_rule="close below 95",
        take_profit_plan=[
            {"label": "tp1", "price": 110.0, "size_frac": 0.5},
            {"label": "tp2", "price": 120.0, "size_frac": 0.5},
        ],
        max_hold_bars=10,
        max_hold_minutes=600,
        reason_now="deterministic test signal",
        status="armed",
        created_at=1_800_000_000.0,
        expires_at=1_800_003_600.0,
        ref_price=100.0,
        risk_pct=5.0,
        boundary_ts=1,
        data_fingerprint=f"fp-{signal_id}",
        dedup_key=f"BTC|{tf}|{family}|{signal_id}",
        exit_mode="partial_be",
    )


def test_runtime_queue_builds_from_accepted_consumer_rows(tmp_path):
    append_signal(tmp_path, _sig("fast", family="early_tp_tactical", tf="15m"))
    append_signal(tmp_path, _sig("slow", family="continuation", tf="4h"))
    export_main_paper_instructions(tmp_path)
    consume_main_paper_instructions(tmp_path)

    summary = build_main_paper_runtime_queue(tmp_path)

    assert summary["rows_read"] == 2
    assert summary["queued"] == 2
    assert summary["invalid"] == 0
    assert summary["paper_only"] is True
    assert summary["execution_allowed"] is False
    rows = [
        json.loads(line)
        for line in Path(summary["jsonl_path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["setup_family"] == "early_tp_tactical"
    assert rows[0]["runtime_action"] == "watch_paper"
    assert rows[0]["source"] == "farm"
    assert rows[0]["entry_zone"] == [100.0, 101.0]
    assert rows[0]["boundary_ts"] == 1
    assert rows[0]["created_at"] == 1_800_000_000.0
    assert rows[0]["expires_at"] == 1_800_003_600.0
    assert rows[0]["max_hold_bars"] == 10
    assert rows[0]["risk_pct"] == 5.0
    assert rows[0]["data_fingerprint"] == "fp-fast"
    assert rows[0]["dedup_key"] == "BTC|15m|early_tp_tactical|fast"
    assert rows[0]["source_mode"] == "live"
    assert rows[0]["exit_mode"] == "partial_be"
    assert all(row["paper_only"] is True for row in rows)
    assert all(row["execution_allowed"] is False for row in rows)


def test_runtime_queue_preserves_pfr_source(tmp_path):
    append_signal(tmp_path, _sig("pfr", source="pfr_farm"))
    export_main_paper_instructions(tmp_path)
    consume_main_paper_instructions(tmp_path)

    summary = build_main_paper_runtime_queue(tmp_path)

    rows = [
        json.loads(line)
        for line in Path(summary["jsonl_path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert summary["queued"] == 1
    assert rows[0]["source"] == "pfr_farm"


def test_runtime_queue_skips_rejected_consumer_rows(tmp_path):
    append_signal(tmp_path, _sig("bad"))
    bridge = export_main_paper_instructions(tmp_path)
    snap = Path(bridge["snapshot_path"])
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["items"][0]["execution_allowed"] = True
    snap.write_text(json.dumps(data), encoding="utf-8")
    consume_main_paper_instructions(tmp_path)

    summary = build_main_paper_runtime_queue(tmp_path)

    assert summary["rows_read"] == 1
    assert summary["accepted_rows"] == 0
    assert summary["queued"] == 0
    assert summary["rejected_or_skipped"] == 1


def test_runtime_queue_record_rejects_execution_enabled():
    with pytest.raises(ValueError, match="never allow execution"):
        MainPaperRuntimeQueueItem(
            runtime_id="r",
            consumer_id="c",
            instruction_id="i",
            source_signal_id="s",
            source="farm",
            pair="BTC-USDT-SWAP",
            okx_inst_id="BTC-USDT-SWAP",
            timeframe="1h",
            side="long",
            setup_family="early_tp_tactical",
            entry=100.0,
            entry_zone=[100.0, 101.0],
            stop=95.0,
            take_profit_plan=[{"label": "tp", "price": 110.0}],
            max_hold_min=60,
            max_hold_bars=10,
            boundary_ts=1,
            created_at=1.0,
            expires_at=2.0,
            risk_pct=5.0,
            data_fingerprint="fp",
            dedup_key="BTC|1h|early_tp_tactical",
            source_mode="live",
            exit_mode="partial_be",
            priority=0,
            execution_allowed=True,
        )


def test_runtime_queue_has_no_live_order_imports():
    path = Path("src/research_lab/main_paper_runtime_adapter.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "scripts.auto_execute",
        "scripts.ws.ws_main_screener",
        "src.exchange",
        "src.exchange.okx_client",
        "src.utils.telegram",
        "dotenv",
        "ccxt",
        "hmac",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not (imported & forbidden)
