import ast
from pathlib import Path

from src.research_lab.paper_exit_supervisor import (
    build_exit_supervisor_items,
    validate_exit_advisor_payload,
    write_exit_supervisor,
)
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import append_signal


def _sig(*, status="opened_paper", mfe=2.5, mae=0.2, partial_done=False) -> PaperActionSignal:
    return PaperActionSignal(
        signal_id="sig_exit_1",
        source="farm",
        symbol="BTC_USDT_SWAP",
        okx_inst_id="BTC-USDT-SWAP",
        timeframe="15m",
        side="long",
        setup_family="early_tp_tactical",
        entry_zone=[100.0, 101.0],
        stop_loss=98.0,
        invalidation_rule="close below 98",
        take_profit_plan=[{"label": "tp1", "price": 104.0, "size_frac": 1.0}],
        max_hold_bars=6,
        max_hold_minutes=90,
        reason_now="test",
        status=status,
        created_at=1.0,
        expires_at=999999.0,
        ref_price=100.0,
        risk_pct=2.0,
        boundary_ts=1,
        data_fingerprint="fp",
        dedup_key="BTC|15m|early",
        outcome={
            "result": "pending_open",
            "mfe_pct": mfe,
            "mae_pct": mae,
            "bars_held": 2,
            "partial_done": partial_done,
        },
    )


def test_exit_supervisor_recommends_profit_lock_for_open_paper_signal(tmp_path):
    append_signal(tmp_path, _sig())

    items = build_exit_supervisor_items(tmp_path)

    assert len(items) == 1
    assert items[0].schema == "PaperExitSupervisorItem.v1"
    assert items[0].deterministic_action == "lock_profit_watch"
    assert items[0].urgency == "medium"
    assert items[0].paper_only is True
    assert items[0].execution_allowed is False


def test_exit_supervisor_writes_private_artifact(tmp_path):
    append_signal(tmp_path, _sig(status="armed"))

    summary = write_exit_supervisor(tmp_path)

    assert summary["schema"] == "paper_exit_supervisor.v1"
    assert summary["supervised"] == 1
    assert summary["by_action"] == {"watch_entry": 1}
    assert Path(summary["snapshot_path"]).exists()


def test_exit_advisor_rejects_live_authority_fields():
    ok, problems = validate_exit_advisor_payload(
        {"advisor_action": "close", "stop_loss": 95.0, "execution_allowed": True}
    )

    assert ok is False
    assert "execution_allowed_true" in problems
    assert any(problem.startswith("forbidden_fields:") for problem in problems)


def test_paper_exit_supervisor_has_no_live_order_imports():
    path = Path("src/research_lab/paper_exit_supervisor.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "main",
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
