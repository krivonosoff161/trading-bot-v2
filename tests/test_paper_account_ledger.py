import ast
import json
from pathlib import Path

from src.research_lab.paper_account_ledger import build_paper_account_ledger


def _trade(trade_id: str, **overrides):
    row = {
        "paper_trade_id": trade_id,
        "source_signal_id": f"sig_{trade_id}",
        "validation_tier": "farm_calculated",
        "okx_inst_id": "BTC-USDT-SWAP",
        "timeframe": "1h",
        "side": "long",
        "boundary_ts": 1_000,
        "signal_status": "opened_paper",
        "adaptive_policy_confidence": 0.5,
        "outcome": {"opened_at_bar_ts": 2_000},
    }
    row.update(overrides)
    return row


def test_account_ledger_reserves_and_releases_real_paper_capital(tmp_path):
    summary = build_paper_account_ledger(
        tmp_path,
        [
            _trade(
                "trade_1",
                signal_status="reviewed",
                status="closed_take",
                outcome={
                    "opened_at_bar_ts": 2_000,
                    "last_observed_bar_ts": 3_000,
                    "net_pct": 2.0,
                    "fees_bps_round_trip": 7.0,
                    "slippage_bps_round_trip": 3.0,
                },
            )
        ],
    )

    assert summary["schema"] == "paper_account_ledger.v1"
    assert summary["events"] == 2
    assert summary["terminal_trades"] == 1
    assert summary["balance_usdt"] == 702.1
    assert summary["available_margin_usdt"] == 702.1
    assert summary["reserved_margin_usdt"] == 0.0
    assert summary["total_fees_usdt"] == 0.0735
    assert summary["total_slippage_usdt"] == 0.0315
    assert summary["paper_only"] is True
    assert summary["execution_allowed"] is False


def test_account_ledger_uses_one_primary_variant_per_scenario(tmp_path):
    calculated = _trade("calculated", adaptive_policy_confidence=0.9)
    validated = _trade(
        "validated",
        validation_tier="validated_pfr",
        adaptive_policy_confidence=0.4,
    )

    summary = build_paper_account_ledger(tmp_path, [calculated, validated])
    events = [json.loads(line) for line in Path(summary["event_log_path"]).read_text(encoding="utf-8").splitlines()]

    assert summary["active_positions"] == 1
    assert summary["reserved_margin_usdt"] == 35.0
    assert summary["counterfactual_exclusions"] == 1
    opened = [row for row in events if row["event_type"] == "position_opened"]
    excluded = [row for row in events if row["event_type"] == "counterfactual_excluded"]
    assert opened[0]["paper_trade_id"] == "validated"
    assert excluded[0]["paper_trade_id"] == "calculated"


def test_account_ledger_is_append_only_and_idempotent(tmp_path):
    trade = _trade("trade_1")
    first = build_paper_account_ledger(tmp_path, [trade])
    before = Path(first["event_log_path"]).read_bytes()
    second = build_paper_account_ledger(tmp_path, [trade])

    assert first["events_added"] == 1
    assert second["events_added"] == 0
    assert Path(second["event_log_path"]).read_bytes() == before


def test_account_ledger_rejects_when_all_margin_is_reserved(tmp_path):
    trades = [
        _trade(
            f"trade_{idx}",
            okx_inst_id=f"COIN{idx}-USDT-SWAP",
            boundary_ts=1_000 + idx,
            outcome={"opened_at_bar_ts": 2_000 + idx},
        )
        for idx in range(21)
    ]

    summary = build_paper_account_ledger(tmp_path, trades)

    assert summary["active_positions"] == 20
    assert summary["reserved_margin_usdt"] == 700.0
    assert summary["available_margin_usdt"] == 0.0
    assert summary["allocation_rejections"] == 1


def test_account_ledger_has_no_live_provider_or_sender_imports():
    path = Path("src/research_lab/paper_account_ledger.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden_prefixes = (
        "src.exchange",
        "src.utils.telegram",
        "scripts.auto_execute",
        "dotenv",
        "requests",
        "aiohttp",
    )
    assert not any(name.startswith(forbidden_prefixes) for name in imported)
