import ast
import json
from pathlib import Path

from src.research_lab.paper_lineage import build_paper_lineage


def _write_snapshot(root: Path, name: str, rows: list[dict]) -> None:
    path = root / "state" / "derived" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": rows}), encoding="utf-8")


def test_paper_lineage_joins_existing_ids_without_replacing_them(tmp_path):
    source = "sig_1"
    base = {
        "source_signal_id": source,
        "scanner_event_id": "se_1",
        "data_packet_id": "dp_1",
        "feature_packet_id": "fp_1",
        "setup_candidate_id": "sc_1",
        "sweep_run_id": "sweep_1",
        "validation_id": "val_1",
        "setup_id": "setup_1",
        "candidate_id": "candidate_1",
        "ready_strategy_id": "ready_1",
        "okx_inst_id": "BTC-USDT-SWAP",
        "timeframe": "1h",
        "setup_family": "continuation",
        "source": "pfr_farm",
    }
    _write_snapshot(
        tmp_path,
        "paper_product_trades.json",
        [{**base, "paper_product_trade_id": "product_1", "paper_trade_id": "product_1", "status": "reviewed"}],
    )
    _write_snapshot(
        tmp_path,
        "main_paper_runtime_queue.json",
        [{**base, "instruction_id": "instruction_1", "consumer_id": "consumer_1", "runtime_id": "runtime_1"}],
    )
    _write_snapshot(
        tmp_path,
        "main_paper_trades.json",
        [{**base, "instruction_id": "instruction_1", "runtime_id": "runtime_1", "paper_trade_id": "trade_1"}],
    )
    _write_snapshot(
        tmp_path,
        "paper_telegram_preview.json",
        [{"source_signal_id": source, "telegram_card_id": "card_1"}],
    )
    _write_snapshot(
        tmp_path,
        "paper_signal_training.json",
        [{"signal_id": source, "training_row_id": "training_1", "outcome_id": "outcome_1", "outcome_review_id": "review_1"}],
    )
    _write_snapshot(
        tmp_path,
        "trade_thesis_supervisor.json",
        [{"source_signal_id": source, "thesis_id": "thesis_1"}],
    )
    account = tmp_path / "state" / "derived" / "paper_account_events.jsonl"
    account.write_text(
        json.dumps({"source_signal_id": source, "scenario_id": "account_scenario_1"}) + "\n",
        encoding="utf-8",
    )

    summary = build_paper_lineage(tmp_path)

    assert summary["schema"] == "paper_lineage_index.v1"
    assert summary["envelopes"] == 1
    assert summary["conflicts"] == 0
    assert summary["main_without_trade"] == 0
    assert summary["terminal_without_training"] == 0
    assert summary["valid"] is True
    row = summary["items"][0]
    assert row["source_signal_id"] == source
    assert row["scanner_event_id"] == "se_1"
    assert row["runtime_id"] == "runtime_1"
    assert row["paper_trade_id"] == "trade_1"
    assert row["paper_product_trade_id"] == "product_1"
    assert row["paper_account_scenario_id"] == "account_scenario_1"
    assert row["thesis_id"] == "thesis_1"
    assert row["telegram_card_id"] == "card_1"
    assert row["training_row_id"] == "training_1"
    assert row["paper_only"] is True
    assert row["execution_allowed"] is False


def test_paper_lineage_reports_conflicts_and_missing_downstream_rows(tmp_path):
    _write_snapshot(
        tmp_path,
        "paper_product_trades.json",
        [{"source_signal_id": "sig_1", "status": "reviewed", "validation_id": "val_a"}],
    )
    _write_snapshot(
        tmp_path,
        "main_paper_runtime_queue.json",
        [{"source_signal_id": "sig_1", "runtime_id": "runtime_1", "validation_id": "val_b"}],
    )

    summary = build_paper_lineage(tmp_path)

    assert summary["conflicts"] == 1
    assert summary["main_without_trade"] == 1
    assert summary["terminal_without_training"] == 1
    assert summary["valid"] is False


def test_paper_lineage_has_no_live_order_provider_or_sender_imports():
    tree = ast.parse(Path("src/research_lab/paper_lineage.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("src.exchange", "src.utils.telegram", "dotenv", "requests", "aiohttp")
    assert not any(name.startswith(forbidden) for name in imported)
