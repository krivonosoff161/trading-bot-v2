import ast
import json
from pathlib import Path

from src.research_lab.trade_thesis_supervisor import (
    build_trade_thesis_supervisor,
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
    derived.mkdir(parents=True)
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
    assert summary["by_event_type"] == {"countertrend_bounce": 1, "primary_thesis": 1}
    assert summary["by_action"] == {"hold_primary_tighten_watch": 1, "track_primary": 1}
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
