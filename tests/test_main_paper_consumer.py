import ast
import json
from pathlib import Path

import pytest

from src.research_lab.main_paper_bridge import export_main_paper_instructions
from src.research_lab.main_paper_consumer import (
    MainPaperConsumerRecord,
    consume_main_paper_instructions,
)
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import append_signal


def _sig(status: str = "armed") -> PaperActionSignal:
    return PaperActionSignal(
        signal_id=f"sig-{status}",
        source="farm",
        symbol="BTC_USDT_SWAP",
        okx_inst_id="BTC-USDT-SWAP",
        timeframe="1h",
        side="long",
        setup_family="early_tp_tactical",
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
        status=status,
        created_at=1_800_000_000.0,
        expires_at=1_800_003_600.0,
        ref_price=100.0,
        risk_pct=5.0,
        boundary_ts=1,
        data_fingerprint="abc123",
        dedup_key="BTC|1h|early",
        exit_mode="partial_be",
    )


def test_consumer_accepts_bridge_output(tmp_path):
    append_signal(tmp_path, _sig("armed"))
    append_signal(tmp_path, _sig("opened_paper"))
    export_main_paper_instructions(tmp_path)

    summary = consume_main_paper_instructions(tmp_path)

    assert summary["instructions_read"] == 2
    assert summary["accepted"] == 2
    assert summary["rejected"] == 0
    assert summary["paper_only"] is True
    assert summary["execution_allowed"] is False
    rows = [
        json.loads(line)
        for line in Path(summary["jsonl_path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert {row["consumer_status"] for row in rows} == {"accepted_for_paper_watch"}
    assert all(row["paper_only"] is True for row in rows)
    assert all(row["execution_allowed"] is False for row in rows)


def test_consumer_rejects_execution_enabled_instruction(tmp_path):
    append_signal(tmp_path, _sig("armed"))
    bridge = export_main_paper_instructions(tmp_path)
    snap = Path(bridge["snapshot_path"])
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["items"][0]["execution_allowed"] = True
    snap.write_text(json.dumps(data), encoding="utf-8")

    summary = consume_main_paper_instructions(tmp_path)

    assert summary["accepted"] == 0
    assert summary["rejected"] == 1
    out = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    item = out["items"][0]
    assert item["consumer_status"] == "rejected_contract"
    assert "execution_allowed_not_false" in item["problems"]


def test_consumer_rejects_contract_mismatch(tmp_path):
    append_signal(tmp_path, _sig("armed"))
    bridge = export_main_paper_instructions(tmp_path)
    snap = Path(bridge["snapshot_path"])
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["items"][0]["signal_contract"]["pair"] = "ETH-USDT-SWAP"
    snap.write_text(json.dumps(data), encoding="utf-8")

    summary = consume_main_paper_instructions(tmp_path)

    assert summary["accepted"] == 0
    assert summary["rejected"] == 1
    out = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert "contract_pair_mismatch" in out["items"][0]["problems"]


def test_consumer_writes_empty_snapshot_when_bridge_missing(tmp_path):
    summary = consume_main_paper_instructions(tmp_path)

    assert summary["source_exists"] is False
    assert summary["instructions_read"] == 0
    assert summary["accepted"] == 0
    assert Path(summary["snapshot_path"]).exists()


def test_consumer_record_rejects_execution_enabled():
    with pytest.raises(ValueError, match="never allow execution"):
        MainPaperConsumerRecord(
            consumer_id="c",
            instruction_id="i",
            source_signal_id="s",
            pair="BTC_USDT_SWAP",
            okx_inst_id="BTC-USDT-SWAP",
            timeframe="1h",
            side="long",
            setup_family="x",
            source_status="armed",
            consumer_status="accepted_for_paper_watch",
            execution_allowed=True,
        )


def test_main_paper_consumer_has_no_live_order_imports():
    path = Path("src/research_lab/main_paper_consumer.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "scripts.auto_execute",
        "scripts.ws.ws_main_screener",
        "src.exchange",
        "src.exchange.okx_client",
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
