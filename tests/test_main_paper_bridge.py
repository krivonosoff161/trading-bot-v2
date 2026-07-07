import ast
import json
from pathlib import Path

import pytest

from src.research_lab.main_paper_bridge import (
    MainPaperInstruction,
    export_main_paper_instructions,
    instruction_from_signal,
)
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import append_signal


def _sig(status: str = "armed", *, validated: bool = True, suffix: str = "") -> PaperActionSignal:
    validator_context = {}
    if validated:
        validator_context = {
            "ready_strategy_id": "ready_abc",
            "source_validation_verdict": "PAPER_FORWARD_READY",
            "setup_id": "setup_abc",
            "candidate_id": "cand_abc",
        }
    return PaperActionSignal(
        signal_id=f"sig-{status}{suffix}",
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
        validator_context=validator_context,
        boundary_ts=1,
        data_fingerprint="abc123",
        dedup_key=f"BTC|1h|early|{status}|{suffix}",
        exit_mode="partial_be",
    )


def test_instruction_from_signal_reuses_signal_contract_shape():
    item = instruction_from_signal(_sig("armed"))

    assert item is not None
    assert item.execution_allowed is False
    assert item.paper_only is True
    assert item.entry == 100.5
    assert item.signal_contract["pair"] == "BTC-USDT-SWAP"
    assert item.signal_contract["side"] == "long"
    assert item.signal_contract["exit_rule"]["type"] == "scaled"
    assert item.signal_contract["metadata"]["execution_allowed"] is False
    assert item.signal_contract["metadata"]["entry_zone"] == [100.0, 101.0]
    assert item.signal_contract["metadata"]["boundary_ts"] == 1
    assert item.signal_contract["metadata"]["created_at"] == 1_800_000_000.0
    assert item.signal_contract["metadata"]["expires_at"] == 1_800_003_600.0
    assert item.signal_contract["metadata"]["max_hold_bars"] == 10
    assert item.signal_contract["metadata"]["data_fingerprint"] == "abc123"
    assert item.signal_contract["metadata"]["dedup_key"] == "BTC|1h|early|armed|"
    assert item.signal_contract["metadata"]["mode"] == "live"
    assert item.signal_contract["metadata"]["exit_mode"] == "partial_be"
    assert item.signal_contract["metadata"]["entry_trigger"] == "limit_pullback"
    assert item.signal_contract["metadata"]["pretrigger"] is False


def test_instruction_from_signal_ignores_terminal_reviews():
    assert instruction_from_signal(_sig("reviewed")) is None


def test_instruction_from_breakout_stop_uses_stop_entry_price():
    sig = _sig("armed")
    sig.entry_zone = [101.0, 101.2]
    sig.validator_context["entry_trigger"] = "breakout_stop"
    sig.validator_context["pretrigger"] = True
    sig.validator_context["trigger_gap_pct"] = 0.25

    item = instruction_from_signal(sig)

    assert item is not None
    assert item.entry == 101.2
    assert item.signal_contract["entry"] == 101.2
    assert item.signal_contract["metadata"]["entry_trigger"] == "breakout_stop"
    assert item.signal_contract["metadata"]["pretrigger"] is True
    assert item.signal_contract["metadata"]["trigger_gap_pct"] == 0.25


def test_export_main_paper_instructions_rebuilds_private_view(tmp_path):
    append_signal(tmp_path, _sig("armed", suffix="1"))
    append_signal(tmp_path, _sig("opened_paper", suffix="2"))
    append_signal(tmp_path, _sig("reviewed", suffix="3"))
    append_signal(tmp_path, _sig("armed", validated=False, suffix="4"))

    summary = export_main_paper_instructions(tmp_path)
    out_jsonl = Path(summary["jsonl_path"])
    out_snapshot = Path(summary["snapshot_path"])

    assert summary["instructions"] == 3
    assert summary["active_source_signals"] == 3
    assert summary["skipped_unvalidated"] == 0
    assert summary["skip_reasons"] == {}
    assert summary["required_validator_verdict"] == "PAPER_FORWARD_READY"
    assert summary["execution_allowed"] is False
    rows = [json.loads(line) for line in out_jsonl.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3
    assert all(row["execution_allowed"] is False for row in rows)
    tiers = {row["source_signal_id"]: row["signal_contract"]["metadata"]["validation_tier"] for row in rows}
    assert tiers["sig-armed1"] == "validated_pfr"
    assert tiers["sig-opened_paper2"] == "validated_pfr"
    assert tiers["sig-armed4"] == "farm_calculated"
    snap = json.loads(out_snapshot.read_text(encoding="utf-8"))
    assert snap["instructions"] == 3
    assert snap["skip_reasons"] == {}


def test_instruction_from_signal_accepts_unvalidated_farm_as_calculated_paper_watch():
    item = instruction_from_signal(_sig("armed", validated=False))

    assert item is not None
    assert item.signal_contract["metadata"]["validation_tier"] == "farm_calculated"


def test_instruction_from_signal_skips_research_only_sources():
    sig = _sig("armed", validated=False)
    sig.source = "scanner"

    assert instruction_from_signal(sig) is None


def test_main_paper_instruction_rejects_execution_enabled():
    with pytest.raises(ValueError, match="never allow execution"):
        MainPaperInstruction(
            instruction_id="i",
            source_signal_id="s",
            pair="BTC_USDT_SWAP",
            okx_inst_id="BTC-USDT-SWAP",
            timeframe="1h",
            side="long",
            entry=100.0,
            stop=95.0,
            take_profit_plan=[{"label": "tp1", "price": 110.0, "size_frac": 1.0}],
            max_hold_min=60,
            setup_family="x",
            source_status="armed",
            signal_contract={},
            execution_allowed=True,
        )


def test_main_paper_bridge_has_no_live_order_imports():
    path = Path("src/research_lab/main_paper_bridge.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "scripts.auto_execute",
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
