import ast
from pathlib import Path

from src.research_lab.paper_product_trade_ledger import build_paper_product_trade_ledger
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import append_signal


def _signal(**overrides) -> PaperActionSignal:
    row = {
        "signal_id": "sig_product_1",
        "source": "farm",
        "symbol": "BTC_USDT_SWAP",
        "okx_inst_id": "BTC-USDT-SWAP",
        "timeframe": "1h",
        "side": "long",
        "setup_family": "early_tp_tactical",
        "entry_zone": [100.0, 101.0],
        "stop_loss": 98.0,
        "invalidation_rule": "invalid below stop",
        "take_profit_plan": [{"label": "tp1", "price": 104.0, "size_frac": 1.0}],
        "max_hold_bars": 8,
        "max_hold_minutes": 480,
        "reason_now": "tactical early-TP scalp; fast in/out",
        "status": "armed",
        "created_at": 1_000.0,
        "expires_at": 2_000.0,
        "risk_pct": 2.0,
        "boundary_ts": 1_700_000_000_000,
        "data_fingerprint": "fp1",
        "dedup_key": "BTC|1h|early",
        "validator_context": {},
    }
    row.update(overrides)
    return PaperActionSignal(**row)


def test_product_trade_ledger_tracks_broad_paper_candidates_without_live_ready(tmp_path):
    append_signal(tmp_path, _signal())

    summary = build_paper_product_trade_ledger(tmp_path)

    assert summary["schema"] == "paper_product_trade_ledger.v1"
    assert summary["trades"] == 1
    assert summary["live_ready"] == 0
    assert summary["live_blocked"] == 1
    assert summary["active_trades"] == 1
    assert summary["active_live_ready"] == 0
    assert summary["active_live_blocked"] == 1
    assert summary["active_by_source"] == {"farm": 1}
    assert summary["active_by_family"] == {"early_tp_tactical": 1}
    assert summary["by_geometry_profile"] == {"farm_legacy_static": 1}
    assert summary["by_live_block"] == {"missing_ready_strategy_id": 1}
    assert summary["paper_only"] is True
    assert summary["execution_allowed"] is False
    trade = summary["items"][0]
    assert trade["schema"] == "PaperProductTrade.v1"
    assert trade["paper_product_trade_id"] == trade["paper_trade_id"]
    assert trade["source_signal_id"] == "sig_product_1"
    assert trade["live_ready"] is False
    assert trade["live_block_reason"] == "missing_ready_strategy_id"
    assert trade["farm_geometry_profile_id"] == "farm_legacy_static"
    assert trade["paper_account"]["notional_usdt"] == 105.0
    assert Path(summary["snapshot_path"]).exists()
    assert Path(summary["jsonl_path"]).exists()


def test_product_trade_ledger_marks_pfr_rows_as_live_ready_shadow(tmp_path):
    append_signal(
        tmp_path,
        _signal(
            source="pfr_farm",
            validator_context={
                "ready_strategy_id": "ready_1",
                "source_validation_verdict": "PAPER_FORWARD_READY",
            },
        ),
    )

    summary = build_paper_product_trade_ledger(tmp_path)

    assert summary["trades"] == 1
    assert summary["live_ready"] == 1
    assert summary["live_blocked"] == 0
    assert summary["active_live_ready"] == 1
    assert summary["active_live_blocked"] == 0
    assert summary["items"][0]["ready_strategy_id"] == "ready_1"
    assert summary["items"][0]["live_block_reason"] == ""
    assert summary["items"][0]["farm_geometry_profile_id"] == "pfr_validated_static"


def test_product_trade_ledger_preserves_geometry_profile_lineage(tmp_path):
    append_signal(
        tmp_path,
        _signal(
            validator_context={
                "geometry_profile_id": "stop_relief",
                "geometry_profile_reason": "product memory says this cell needs wider invalidation",
                "geometry_entry_scale": 1.0,
                "geometry_stop_scale": 1.2,
                "geometry_tp_scale": 1.0,
                "geometry_hold_scale": 1.0,
            },
        ),
    )

    summary = build_paper_product_trade_ledger(tmp_path)

    assert summary["by_geometry_profile"] == {"stop_relief": 1}
    trade = summary["items"][0]
    assert trade["farm_geometry_profile_id"] == "stop_relief"
    assert trade["farm_geometry_stop_scale"] == 1.2


def test_product_trade_ledger_links_terminal_outcome(tmp_path):
    append_signal(
        tmp_path,
        _signal(
            status="reviewed",
            outcome={"result": "take", "net_pct": 1.2},
            review={"diagnosis": "good_signal"},
        ),
    )

    summary = build_paper_product_trade_ledger(tmp_path)

    assert summary["by_status"] == {"reviewed": 1}
    assert summary["active_trades"] == 0
    assert summary["active_live_ready"] == 0
    assert summary["active_live_blocked"] == 0
    assert summary["items"][0]["outcome"]["result"] == "take"
    assert summary["items"][0]["review"]["diagnosis"] == "good_signal"
    assert summary["items"][0]["paper_account"]["pnl_usdt"] == 1.26
    assert summary["paper_money"]["terminal_trades"] == 1


def test_product_trade_ledger_has_no_provider_exchange_or_order_imports():
    path = Path("src/research_lab/paper_product_trade_ledger.py")
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
