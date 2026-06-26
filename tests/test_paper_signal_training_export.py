import ast
import json
from pathlib import Path

from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import append_signal, update_signal
from src.research_lab.paper_signals.training_export import export_training_rows, training_row


def _signal(signal_id: str = "s1", status: str = "armed") -> PaperActionSignal:
    return PaperActionSignal(
        signal_id=signal_id,
        source="farm",
        symbol="A_USDT_SWAP",
        okx_inst_id="A-USDT-SWAP",
        timeframe="15m",
        side="long",
        setup_family="early_tp_tactical",
        entry_zone=[100.0, 101.0],
        stop_loss=98.0,
        invalidation_rule="close below local support",
        take_profit_plan=[{"label": "tp1", "price": 105.0, "size_frac": 0.5}],
        max_hold_bars=12,
        max_hold_minutes=180,
        reason_now="fresh pullback",
        status=status,
        created_at=1000.0,
        boundary_ts=900,
        data_fingerprint="fp",
        dedup_key="A|15m|early",
        risk_pct=2.5,
    )


def test_training_row_contains_outcome_and_review_fields():
    sig = _signal(status="reviewed")
    sig.outcome = {"result": "take", "net_pct": 1.2, "mfe_pct": 1.8, "mae_pct": 0.2, "capture": 0.66}
    sig.review = {"diagnosis": "good_signal", "net_r": 0.8}

    row = training_row(sig)

    assert row["schema"] == "PaperSignalTrainingRow.v1"
    assert row["paper_only"] is True
    assert row["entry_mid"] == 100.5
    assert row["tp1"] == 105.0
    assert row["result"] == "take"
    assert row["diagnosis"] == "good_signal"


def test_export_training_rows_uses_latest_terminal_state(tmp_path):
    sig = _signal(status="armed")
    append_signal(tmp_path, sig)
    sig.status = "reviewed"
    sig.outcome = {"result": "stop", "net_pct": -1.0}
    sig.review = {"diagnosis": "wrong_direction", "net_r": -1.0}
    update_signal(tmp_path, sig)

    summary = export_training_rows(tmp_path)
    rows_path = Path(summary["jsonl_path"])
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]

    assert summary["rows"] == 1
    assert rows[0]["status"] == "reviewed"
    assert rows[0]["result"] == "stop"
    assert summary["by_diagnosis"] == {"wrong_direction": 1}


def test_export_training_rows_skips_active_by_default(tmp_path):
    append_signal(tmp_path, _signal(status="armed"))

    summary = export_training_rows(tmp_path)

    assert summary["rows"] == 0


def test_training_export_has_no_live_order_imports():
    path = Path("src/research_lab/paper_signals/training_export.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = ("okx_client", "ccxt", "order_exec", "live_engine", "auto_trade", "dotenv", "telegram")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
            assert not any(token in mod.lower() for token in forbidden), mod
