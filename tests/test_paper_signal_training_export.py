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
        validator_context={
            "ready_strategy_id": "ready_1",
            "setup_id": "setup_1",
            "candidate_id": "candidate_1",
            "source_validation_verdict": "PAPER_FORWARD_READY",
        },
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

    assert row["schema"] == "TrainingRow.v2"
    assert "PaperSignalTrainingRow.v1" in row["schema_compat"]
    assert row["paper_only"] is True
    assert row["execution_allowed"] is False
    assert row["entry_mid"] == 100.5
    assert row["ready_strategy_id"] == "ready_1"
    assert row["source_validation_verdict"] == "PAPER_FORWARD_READY"
    assert row["tp1"] == 105.0
    assert row["geometry"]["rr_tp1"] > 0
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
    assert summary["row_schema"] == "TrainingRow.v2"
    assert summary["execution_allowed"] is False
    assert rows[0]["status"] == "reviewed"
    assert rows[0]["result"] == "stop"
    assert summary["by_diagnosis"] == {"wrong_direction": 1}


def test_export_training_rows_links_telegram_preview(tmp_path):
    sig = _signal(status="reviewed")
    sig.outcome = {"result": "take", "net_pct": 1.0}
    sig.review = {"diagnosis": "good_signal"}
    append_signal(tmp_path, sig)
    preview = tmp_path / "state" / "derived" / "paper_telegram_preview.json"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "source_signal_id": sig.signal_id,
                        "telegram_card_id": "tgcard_1",
                        "text": "human paper card",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = export_training_rows(tmp_path)
    rows = [json.loads(line) for line in Path(summary["jsonl_path"]).read_text(encoding="utf-8").splitlines()]

    assert rows[0]["telegram_card_id"] == "tgcard_1"
    assert rows[0]["final_card_text"] == "human paper card"
    assert rows[0]["final_card_hash"]


def test_export_training_rows_links_durable_card_ledger(tmp_path):
    sig = _signal(status="reviewed")
    sig.outcome = {"result": "take", "net_pct": 1.0}
    sig.review = {"diagnosis": "good_signal"}
    append_signal(tmp_path, sig)
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "paper_telegram_card_ledger.json").write_text(
        json.dumps(
            {
                "schema": "paper_telegram_card_ledger.v1",
                "items": [
                    {
                        "source_signal_id": sig.signal_id,
                        "telegram_card_id": "tgcard_old",
                        "text": "old sent paper card",
                        "paper_only": True,
                        "execution_allowed": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (derived / "paper_telegram_preview.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "source_signal_id": "other_signal",
                        "telegram_card_id": "tgcard_current",
                        "text": "current card for another signal",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = export_training_rows(tmp_path)
    rows = [json.loads(line) for line in Path(summary["jsonl_path"]).read_text(encoding="utf-8").splitlines()]

    assert rows[0]["telegram_card_id"] == "tgcard_old"
    assert rows[0]["final_card_text"] == "old sent paper card"


def test_export_training_rows_links_main_paper_trade(tmp_path):
    sig = _signal(status="reviewed")
    sig.outcome = {"result": "take", "net_pct": 1.0}
    sig.review = {"diagnosis": "good_signal"}
    append_signal(tmp_path, sig)
    trades = tmp_path / "state" / "derived" / "main_paper_trades.json"
    trades.parent.mkdir(parents=True, exist_ok=True)
    trades.write_text(
        json.dumps(
            {
                "schema": "main_paper_trade_ledger.v1",
                "items": [
                    {
                        "source_signal_id": sig.signal_id,
                        "paper_trade_id": "papertrade_1",
                        "runtime_id": "runtime_1",
                        "status": "closed_take",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = export_training_rows(tmp_path)
    rows = [json.loads(line) for line in Path(summary["jsonl_path"]).read_text(encoding="utf-8").splitlines()]

    assert rows[0]["paper_trade_id"] == "papertrade_1"
    assert rows[0]["main_paper_runtime_id"] == "runtime_1"
    assert rows[0]["main_paper_status"] == "closed_take"


def test_export_training_rows_links_product_trade_when_main_trade_missing(tmp_path):
    sig = _signal(status="reviewed")
    sig.outcome = {"result": "take", "net_pct": 1.0}
    sig.review = {"diagnosis": "good_signal"}
    append_signal(tmp_path, sig)
    trades = tmp_path / "state" / "derived" / "paper_product_trades.json"
    trades.parent.mkdir(parents=True, exist_ok=True)
    trades.write_text(
        json.dumps(
            {
                "schema": "paper_product_trade_ledger.v1",
                "items": [
                    {
                        "source_signal_id": sig.signal_id,
                        "paper_trade_id": "paperproducttrade_1",
                        "paper_product_trade_id": "paperproducttrade_1",
                        "status": "reviewed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = export_training_rows(tmp_path)
    rows = [json.loads(line) for line in Path(summary["jsonl_path"]).read_text(encoding="utf-8").splitlines()]

    assert rows[0]["paper_trade_id"] == "paperproducttrade_1"
    assert rows[0]["paper_product_trade_id"] == "paperproducttrade_1"
    assert rows[0]["main_paper_runtime_id"] == ""
    assert rows[0]["main_paper_status"] == "reviewed"


def test_export_training_rows_links_calculator_advice(tmp_path):
    sig = _signal(status="reviewed")
    sig.feature_packet_id = "fp1"
    sig.outcome = {"result": "take", "net_pct": 1.0}
    sig.review = {"diagnosis": "late_but_worked"}
    append_signal(tmp_path, sig)
    advice = tmp_path / "state" / "llm_advice" / "calculator_advice.jsonl"
    advice.parent.mkdir(parents=True, exist_ok=True)
    advice.write_text(
        json.dumps(
            {
                "calculator_advice_id": "advisor_1",
                "advisor_ref": "advisor_1",
                "feature_packet_id": "fp1",
                "accepted": True,
                "provider": "ollama",
                "model": "calculator",
                "prompt_version": "calculator_advisor_v2_feature_packet_json",
                "prompt_hash": "abc123",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = export_training_rows(tmp_path)
    rows = [json.loads(line) for line in Path(summary["jsonl_path"]).read_text(encoding="utf-8").splitlines()]

    assert rows[0]["calculator_advice_id"] == "advisor_1"
    assert rows[0]["llm_interpretation_ref"] == "advisor_1"
    assert rows[0]["llm_provider"] == "ollama"
    assert rows[0]["llm_model"] == "calculator"
    assert rows[0]["prompt_hash"] == "abc123"


def test_export_training_rows_links_adaptive_policy(tmp_path):
    sig = _signal(status="reviewed")
    sig.outcome = {"result": "take", "net_pct": 1.0}
    sig.review = {"diagnosis": "good_signal"}
    append_signal(tmp_path, sig)
    policy = tmp_path / "state" / "derived" / "main_adaptive_policy.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "policy_id": "main_policy_1",
                        "source_signal_id": sig.signal_id,
                        "execution_profile": "fast_tactical_watch",
                        "entry_profile": "limit_or_pullback",
                        "exit_profile": "early_tp_partial_be",
                        "stop_profile": "tight_atr_cap",
                        "max_hold_profile": "short",
                        "regime_hint": "impulse_exhaustion_scalp",
                        "confidence": 0.72,
                        "reason_codes": ["forward_lead:early_tp_tactical"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = export_training_rows(tmp_path)
    rows = [json.loads(line) for line in Path(summary["jsonl_path"]).read_text(encoding="utf-8").splitlines()]

    assert rows[0]["adaptive_policy_id"] == "main_policy_1"
    assert rows[0]["adaptive_execution_profile"] == "fast_tactical_watch"
    assert rows[0]["adaptive_exit_profile"] == "early_tp_partial_be"
    assert rows[0]["adaptive_policy_reasons"] == ["forward_lead:early_tp_tactical"]


def test_export_training_rows_skips_active_by_default(tmp_path):
    append_signal(tmp_path, _signal(status="armed"))

    summary = export_training_rows(tmp_path)

    assert summary["rows"] == 0


def test_export_training_rows_skips_unchanged_terminal_source(tmp_path, monkeypatch):
    sig = _signal(status="reviewed")
    sig.outcome = {"result": "take", "net_pct": 1.0}
    sig.review = {"diagnosis": "good_signal"}
    append_signal(tmp_path, sig)

    first = export_training_rows(tmp_path)

    def fail_if_rebuilt(*_args, **_kwargs):
        raise AssertionError("unchanged training export should not rewrite lineage")

    monkeypatch.setattr(
        "src.research_lab.paper_signals.training_export.write_cycle_link",
        fail_if_rebuilt,
    )
    second = export_training_rows(tmp_path)

    assert first["source_terminal_hash"] == second["source_terminal_hash"]
    assert second["skipped"] is True
    assert second["skip_reason"] == "source_terminal_unchanged"


def test_export_training_rows_rebuilds_when_linked_card_changes(tmp_path, monkeypatch):
    sig = _signal(status="reviewed")
    sig.outcome = {"result": "take", "net_pct": 1.0}
    sig.review = {"diagnosis": "good_signal"}
    append_signal(tmp_path, sig)

    preview = tmp_path / "state" / "derived" / "paper_telegram_preview.json"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "source_signal_id": sig.signal_id,
                        "telegram_card_id": "tgcard_1",
                        "text": "first card",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    first = export_training_rows(tmp_path)

    preview.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "source_signal_id": sig.signal_id,
                        "telegram_card_id": "tgcard_2",
                        "text": "updated card",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    second = export_training_rows(tmp_path)
    rows = [json.loads(line) for line in Path(second["jsonl_path"]).read_text(encoding="utf-8").splitlines()]

    assert first["source_terminal_hash"] == second["source_terminal_hash"]
    assert first["export_refs_hash"] != second["export_refs_hash"]
    assert second["skipped"] is False
    assert rows[0]["telegram_card_id"] == "tgcard_2"
    assert rows[0]["final_card_text"] == "updated card"


def test_training_export_has_no_live_order_imports():
    path = Path("src/research_lab/paper_signals/training_export.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = ("okx_client", "ccxt", "order_exec", "live_engine", "auto_trade", "dotenv", "telegram")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
            assert not any(token in mod.lower() for token in forbidden), mod
