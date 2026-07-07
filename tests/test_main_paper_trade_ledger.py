import ast
import json
from pathlib import Path

from src.research_lab.main_paper_trade_ledger import build_main_paper_trade_ledger


def _queue_item(**overrides):
    row = {
        "runtime_id": "runtime_1",
        "instruction_id": "mainpaper_sig",
        "source_signal_id": "sig",
        "validation_tier": "validated_pfr",
        "ready_strategy_id": "ready_abc",
        "source_validation_verdict": "PAPER_FORWARD_READY",
        "okx_inst_id": "BTC-USDT-SWAP",
        "timeframe": "1h",
        "side": "long",
        "setup_family": "early_tp_tactical",
        "entry": 100.5,
        "entry_zone": [100.0, 101.0],
        "stop": 95.0,
        "take_profit_plan": [
            {"label": "tp1", "price": 110.0, "size_frac": 0.5},
            {"label": "tp2", "price": 120.0, "size_frac": 0.5},
        ],
        "max_hold_min": 600,
        "max_hold_bars": 10,
        "adaptive_policy_id": "main_policy_fast",
        "adaptive_execution_profile": "fast_tactical_watch",
        "adaptive_entry_profile": "limit_mid_zone",
        "adaptive_exit_profile": "early_tp_partial_be",
        "adaptive_stop_profile": "structure_stop",
        "adaptive_max_hold_profile": "tf_aware",
        "adaptive_regime_hint": "volatile",
        "adaptive_policy_confidence": 0.75,
        "adaptive_policy_reasons": ["forward_lead:early_tp_tactical"],
    }
    row.update(overrides)
    return row


def _observation(**overrides):
    row = {
        "runtime_id": "runtime_1",
        "status": "observed",
        "signal_status": "opened_paper",
        "outcome": {},
        "review": {},
    }
    row.update(overrides)
    return row


def _write_snapshot(root: Path, name: str, rows: list[dict]) -> None:
    path = root / "state" / "derived" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": rows}, ensure_ascii=False), encoding="utf-8")


def test_trade_ledger_builds_validated_and_calculated_paper_trades(tmp_path):
    _write_snapshot(
        tmp_path,
        "main_paper_runtime_queue.json",
        [
            _queue_item(),
            _queue_item(
                runtime_id="runtime_calc",
                source_signal_id="sig_calc",
                validation_tier="farm_calculated",
                ready_strategy_id="",
                source_validation_verdict="",
            ),
            _queue_item(runtime_id="runtime_bad", ready_strategy_id=""),
            _queue_item(runtime_id="runtime_research", validation_tier="research_only"),
        ],
    )
    _write_snapshot(
        tmp_path,
        "main_paper_runtime_observation.json",
        [
            _observation(),
            _observation(runtime_id="runtime_calc"),
            _observation(runtime_id="runtime_bad"),
            _observation(runtime_id="runtime_research"),
        ],
    )

    summary = build_main_paper_trade_ledger(tmp_path)

    assert summary["schema"] == "main_paper_trade_ledger.v1"
    assert summary["queue_rows"] == 4
    assert summary["trades"] == 2
    assert summary["invalid"] == 2
    assert summary["paper_only"] is True
    assert summary["execution_allowed"] is False
    assert summary["by_status"] == {"opened_paper": 2}
    trade = summary["items"][0]
    assert trade["schema"] == "MainPaperTrade.v1"
    assert trade["validation_tier"] == "validated_pfr"
    assert trade["ready_strategy_id"] == "ready_abc"
    assert trade["source_validation_verdict"] == "PAPER_FORWARD_READY"
    calc = summary["items"][1]
    assert calc["validation_tier"] == "farm_calculated"
    assert calc["ready_strategy_id"] == ""
    assert calc["source_validation_verdict"] == ""
    assert trade["paper_only"] is True
    assert trade["execution_allowed"] is False
    assert Path(summary["snapshot_path"]).exists()
    assert Path(summary["jsonl_path"]).exists()


def test_trade_ledger_records_terminal_outcome(tmp_path):
    _write_snapshot(tmp_path, "main_paper_runtime_queue.json", [_queue_item()])
    _write_snapshot(
        tmp_path,
        "main_paper_runtime_observation.json",
        [
            _observation(
                signal_status="closed_paper",
                outcome={"result": "take", "net_pct": 1.5},
                review={"diagnosis": "good_signal"},
            )
        ],
    )

    summary = build_main_paper_trade_ledger(tmp_path)

    assert summary["trades"] == 1
    assert summary["by_status"] == {"closed_take": 1}
    assert summary["items"][0]["outcome"]["net_pct"] == 1.5
    assert summary["items"][0]["review"]["diagnosis"] == "good_signal"


def test_trade_ledger_has_no_live_or_sender_imports():
    path = Path("src/research_lab/main_paper_trade_ledger.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "src.exchange",
        "src.utils.telegram",
        "scripts.auto_execute",
        "dotenv",
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
