import ast
import json
from pathlib import Path

from src.research_lab.product_signal_training import (
    SCHEMA,
    export_product_signal_training,
    product_training_row,
)


def _event() -> dict:
    return {
        "schema": "signal_event.v1",
        "signal_id": "evt_1",
        "created_at": "2026-06-30T00:00:00Z",
        "source": "manual_telegram",
        "mode": "symbol_analysis",
        "decision": "NO_TRADE",
        "status": "completed",
        "symbol": "BTC-USDT",
        "timeframe": "15m",
        "side": "",
        "entry_zone": [],
        "stop_loss": None,
        "take_profit_plan": [],
        "invalidation_rule": "",
        "max_hold_minutes": None,
        "risk_pct": None,
        "reason_codes": ["no_setup"],
        "provider": "alibaba",
        "model": "qwen3",
        "prompt_version": "manual_chart.v2",
        "chat_id": "123456789",
        "message_id": "987",
        "artifacts": {"chart": "logs/users/123/chart.png"},
        "extra": {"category": "majors", "provider_scope": "shared_llm_client_opt_in"},
        "paper_only": True,
        "execution_allowed": False,
    }


def test_product_training_row_hashes_user_ids_and_preserves_trading_facts():
    row = product_training_row(_event())
    rendered = json.dumps(row, ensure_ascii=False, sort_keys=True)

    assert row["schema"] == SCHEMA
    assert row["product_event_id"] == "evt_1"
    assert row["source"] == "manual_telegram"
    assert row["decision"] == "NO_TRADE"
    assert row["symbol"] == "BTC-USDT"
    assert row["provider"] == "alibaba"
    assert row["prompt_version"] == "manual_chart.v2"
    assert row["chat_id_hash"]
    assert row["message_id_hash"]
    assert "123456789" not in rendered
    assert "987" not in rendered
    assert row["artifact_ref_count"] == 1
    assert row["paper_only"] is True
    assert row["execution_allowed"] is False


def test_export_product_signal_training_writes_private_rows_and_lineage(tmp_path):
    source_log = tmp_path / "public_logs" / "signal_events.jsonl"
    source_log.parent.mkdir(parents=True)
    source_log.write_text(json.dumps(_event(), ensure_ascii=False) + "\n", encoding="utf-8")

    summary = export_product_signal_training(tmp_path, source_log=source_log)
    rows_path = Path(summary["jsonl_path"])
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
    links = tmp_path / "state" / "lineage" / "cycle_links.jsonl"

    assert summary["rows"] == 1
    assert summary["source_rows"] == 1
    assert summary["source_invalid_json"] == 0
    assert summary["by_source"] == {"manual_telegram": 1}
    assert summary["by_provider"] == {"alibaba": 1}
    assert summary["paper_only_false"] == 0
    assert summary["execution_allowed_true"] == 0
    assert rows[0]["schema"] == SCHEMA
    assert rows[0]["paper_only"] is True
    assert rows[0]["execution_allowed"] is False
    assert links.exists()
    assert "product_event_id" in links.read_text(encoding="utf-8")


def test_export_product_signal_training_handles_missing_source(tmp_path):
    summary = export_product_signal_training(tmp_path, source_log=tmp_path / "missing.jsonl")

    assert summary["rows"] == 0
    assert summary["source_rows"] == 0
    assert summary["source_exists"] is False
    assert Path(summary["jsonl_path"]).exists()
    assert Path(summary["snapshot_path"]).exists()


def test_product_signal_training_has_no_provider_exchange_or_order_imports():
    path = Path("src/research_lab/product_signal_training.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    forbidden = (
        "okx_client",
        "ccxt",
        "order_exec",
        "live_engine",
        "auto_trade",
        "dotenv",
        "telegram",
        "llm_client",
        "aiohttp",
    )
    assert not [name for name in imports if any(token in name for token in forbidden)]
