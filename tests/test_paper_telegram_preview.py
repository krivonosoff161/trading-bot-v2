import ast
import json
from pathlib import Path

from src.research_lab.paper_telegram_preview import (
    MAX_MESSAGE_CHARS,
    build_paper_telegram_preview,
    render_preview_text,
    validate_preview,
    validation_tier,
)

DOT = "\u00b7"
IDEA = "\u0418\u0434\u0435\u044f:"
ENTRY = "\u0412\u0445\u043e\u0434:"
STOP = "\u0421\u0442\u043e\u043f:"
SOURCE = "\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a:"
HUMAN_DISCLAIMER = "\u042d\u0442\u043e paper-\u043d\u0430\u0431\u043b\u044e\u0434\u0435\u043d\u0438\u0435"
VALIDATION = "\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430:"
VALIDATED_LABEL = "\u043f\u0440\u043e\u0448\u0435\u043b PFR/\u0432\u0430\u043b\u0438\u0434\u0430\u0446\u0438\u044e"
FARM_CALCULATED_LABEL = "\u0440\u0430\u0441\u0447\u0435\u0442\u043d\u044b\u0439 \u0441\u0438\u0433\u043d\u0430\u043b \u0444\u0435\u0440\u043c\u044b"


def _consumer_record(**overrides):
    row = {
        "consumer_id": "consumer_mainpaper_sig",
        "instruction_id": "mainpaper_sig",
        "source_signal_id": "sig",
        "pair": "BTC_USDT_SWAP",
        "okx_inst_id": "BTC-USDT-SWAP",
        "timeframe": "1h",
        "side": "long",
        "setup_family": "early_tp_tactical",
        "source_status": "armed",
        "consumer_status": "accepted_for_paper_watch",
        "problems": [],
        "paper_only": True,
        "execution_allowed": False,
        "signal_contract": {
            "pair": "BTC-USDT-SWAP",
            "side": "long",
            "entry": 100.5,
            "stop": 95.0,
            "max_hold_min": 600,
            "exit_rule": {
                "type": "scaled",
                "params": {
                    "targets": [
                        {"label": "tp1", "price": 110.0, "size_frac": 0.5},
                        {"label": "tp2", "price": 120.0, "size_frac": 0.5},
                    ]
                },
            },
            "follow": {"be_at_R": 1.0, "trail": {}},
            "regime": "paper_watch",
            "analyzer_id": "paper_signals.early_tp_tactical",
            "snapshot_id": "abc123",
            "ts": "2026-06-26T00:00:00+00:00",
            "metadata": {
                "reason_now": "safe <reason> & no order",
                "ready_strategy_id": "ready_abc",
                "source_validation_verdict": "PAPER_FORWARD_READY",
                "execution_allowed": False,
                "paper_only": True,
            },
        },
    }
    row.update(overrides)
    return row


def _trade_record(**overrides):
    row = {
        "schema": "MainPaperTrade.v1",
        "paper_trade_id": "papertrade_1",
        "runtime_id": "runtime_1",
        "instruction_id": "mainpaper_sig",
        "source_signal_id": "sig",
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
        "status": "queued",
        "outcome": {},
        "review": {},
        "paper_only": True,
        "execution_allowed": False,
    }
    row.update(overrides)
    return row


def _write_consumer_snapshot(root: Path, rows: list[dict]) -> None:
    path = root / "state" / "derived" / "main_paper_consumed.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "main_paper_consumer.v1",
                "instructions_read": len(rows),
                "accepted": sum(row.get("consumer_status") == "accepted_for_paper_watch" for row in rows),
                "rejected": sum(row.get("consumer_status") != "accepted_for_paper_watch" for row in rows),
                "items": rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_trade_snapshot(root: Path, rows: list[dict]) -> None:
    path = root / "state" / "derived" / "main_paper_trades.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "main_paper_trade_ledger.v1",
                "trades": len(rows),
                "items": rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_product_trade_snapshot(root: Path, rows: list[dict]) -> None:
    path = root / "state" / "derived" / "paper_product_trades.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "paper_product_trade_ledger.v1",
                "trades": len(rows),
                "items": rows,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_quality_report(root: Path, families: list[dict]) -> None:
    path = root / "state" / "derived" / "paper_product_quality_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "paper_product_quality_report.v1",
                "families": families,
                "paper_only": True,
                "execution_allowed": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_paper_signal_snapshot(root: Path, rows: list[dict]) -> None:
    path = root / "state" / "derived" / "paper_signals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "paper_signals.v1",
                "total": len(rows),
                "active": rows,
                "by_status": {},
                "all_research_only": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_market_data(root: Path, symbol: str = "BTC_USDT_SWAP", timeframe: str = "1h", rows: int = 80) -> Path:
    step = {
        "15m": 15 * 60_000,
        "1h": 60 * 60_000,
        "4h": 4 * 60 * 60_000,
    }.get(timeframe, 60 * 60_000)
    start = 1_780_000_000_000
    candles = []
    for idx in range(rows):
        price = 100.0 + idx * 0.1
        candles.append(
            {
                "ts": start + idx * step,
                "open": price,
                "high": price + 0.4,
                "low": price - 0.3,
                "close": price + 0.15,
                "vol": 1000 + idx,
            }
        )
    path = root / "market_data" / timeframe / f"{symbol}_{start}_{start + (rows - 1) * step}_{timeframe}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(candles, ensure_ascii=False), encoding="utf-8")
    return path


def _paper_signal_row(**overrides):
    row = {
        "signal_id": "BTC_USDT_SWAP_1h_early_tp_tactical_abc",
        "symbol": "BTC_USDT_SWAP",
        "okx_inst_id": "BTC-USDT-SWAP",
        "timeframe": "1h",
        "side": "long",
        "setup_family": "early_tp_tactical",
        "status": "armed",
        "source": "farm",
        "entry_zone": [100.0, 101.0],
        "stop_loss": 98.0,
        "take_profit_plan": [{"label": "tp1", "price": 103.0, "size_frac": 1.0}],
        "max_hold_minutes": 120,
        "reason_now": "tactical early-TP scalp; fast in/out",
        "risk_pct": 1.98,
        "paper_only": True,
        "execution_allowed": False,
        "validator_context": {},
    }
    row.update(overrides)
    return row


def _product_trade_record(**overrides):
    row = {
        "schema": "PaperProductTrade.v1",
        "paper_trade_id": "paperproducttrade_1",
        "paper_product_trade_id": "paperproducttrade_1",
        "source_signal_id": "sig_product",
        "ready_strategy_id": "",
        "source_validation_verdict": "",
        "live_ready": False,
        "live_block_reason": "missing_ready_strategy_id",
        "okx_inst_id": "BTC-USDT-SWAP",
        "timeframe": "1h",
        "side": "long",
        "setup_family": "early_tp_tactical",
        "entry": 100.5,
        "entry_zone": [100.0, 101.0],
        "stop": 98.0,
        "take_profit_plan": [{"label": "tp1", "price": 103.0, "size_frac": 1.0}],
        "max_hold_min": 120,
        "max_hold_bars": 8,
        "status": "armed",
        "signal_status": "armed",
        "source": "farm",
        "reason_now": "tactical early-TP scalp; fast in/out",
        "paper_only": True,
        "execution_allowed": False,
    }
    row.update(overrides)
    return row


def test_validation_tier_classifies_main_pfr_and_farm_rows():
    assert validation_tier(_trade_record()) == "validated_pfr"
    assert validation_tier(_consumer_record()) == "validated_pfr"
    assert validation_tier(_product_trade_record()) == "farm_calculated"
    assert validation_tier(_paper_signal_row()) == "farm_calculated"


def test_validation_tier_classifies_research_retest_rows():
    row = _paper_signal_row(source="research", origin="outcome_retest")
    assert validation_tier(row) == "research_only"


def test_preview_prefers_main_paper_trade_cards(tmp_path):
    _write_consumer_snapshot(tmp_path, [_consumer_record()])
    _write_trade_snapshot(tmp_path, [_trade_record()])

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["source_schema"] == "main_paper_trade_ledger.v1"
    assert summary["records_read"] == 1
    assert summary["rendered"] == 1
    assert summary["invalid"] == 0
    assert summary["sends_network"] is False
    assert summary["card_template_version"] == "paper_telegram_card_v6_validation_tier_ru"
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    item = data["items"][0]
    text = item["text"]
    assert item["validation_tier"] == "validated_pfr"
    assert summary["by_validation_tier"] == {"validated_pfr": 1}
    assert f"Бумажный сигнал: BTC-USDT-SWAP {DOT} 1h {DOT} LONG" in text
    assert VALIDATION in text
    assert VALIDATED_LABEL in text
    assert IDEA in text
    assert ENTRY in text
    assert STOP in text
    assert SOURCE not in text
    assert HUMAN_DISCLAIMER in text
    assert "ready_abc" not in text
    assert "Бумажный режим: это не ордер." in text
    assert "Автоисполнение выключено." in text
    assert "research-only, not an order" not in text
    assert "execution_allowed=false" not in text
    assert not any(marker in text for marker in ("\u0420\u00a0", "\u0420\u040f", "\u0420\u2019", "\u0456\u201a"))


def test_preview_falls_back_to_active_paper_signal_candidates(tmp_path):
    _write_paper_signal_snapshot(
        tmp_path,
        [
            _paper_signal_row(status="reviewed", signal_id="reviewed_skip"),
            _paper_signal_row(status="armed"),
            _paper_signal_row(status="opened_paper", signal_id="opened_first"),
        ],
    )

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["source_schema"] == "paper_signals.v1"
    assert summary["source_exists"] is True
    assert summary["records_read"] == 2
    assert summary["rendered"] == 2
    assert summary["invalid"] == 0
    assert summary["sends_network"] is False
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    first = data["items"][0]
    text = first["text"]
    assert first["source_signal_id"] == "opened_first"
    assert first["validation_tier"] == "farm_calculated"
    assert summary["by_validation_tier"] == {"farm_calculated": 2}
    assert "Кандидат фермы: BTC-USDT-SWAP" in text
    assert VALIDATION in text
    assert FARM_CALCULATED_LABEL in text
    assert "not_hard_validated" not in text
    assert "Бумажный режим: это не ордер." in text
    assert "Автоисполнение выключено." in text
    assert "Риск:" in text
    assert "research-only, not an order" not in text
    assert "execution_allowed=false" not in text


def test_preview_prefers_product_trade_ledger_before_raw_candidates(tmp_path):
    _write_product_trade_snapshot(tmp_path, [_product_trade_record()])
    _write_paper_signal_snapshot(tmp_path, [_paper_signal_row(signal_id="raw_candidate")])
    chart_path = tmp_path / "state" / "derived" / "paper_reviews" / "sig_product.png"
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    chart_path.write_bytes(b"fake-png")

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["source_schema"] == "paper_product_trade_ledger.v1"
    assert summary["records_read"] == 1
    assert summary["rendered"] == 1
    assert summary["charts_available"] == 1
    text = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))["items"][0]["text"]
    item = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))["items"][0]
    assert item["chart_path"] == str(chart_path)
    assert item["validation_tier"] == "farm_calculated"
    assert FARM_CALCULATED_LABEL in text
    assert "Бумажный сигнал: BTC-USDT-SWAP" in text
    assert "К реальной торговле:" not in text
    assert "missing_ready_strategy_id" not in text
    assert "Бумажный режим: это не ордер." in text
    assert "Автоисполнение выключено." in text
    assert "Paper product:" not in text
    assert "Live-ready:" not in text
    assert "ID сигнала" not in text
    assert "research-only, not an order" not in text
    assert "execution_allowed=false" not in text


def test_preview_translates_fade_reason_for_subscribers(tmp_path):
    _write_product_trade_snapshot(
        tmp_path,
        [
            _product_trade_record(
                reason_now="short fade exhaustion (run 3.55 ATR); tactical mean-revert",
                setup_family="reversal_fade",
            )
        ],
    )

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["rendered"] == 1
    text = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))["items"][0]["text"]
    assert "fade exhaustion" not in text
    assert "tactical mean-revert" not in text
    assert "\u0434\u0432\u0438\u0436\u0435\u043d\u0438\u0435 \u0440\u0430\u0441\u0442\u044f\u043d\u0443\u043b\u043e\u0441\u044c" in text


def test_preview_hides_unknown_raw_reason_for_subscribers(tmp_path):
    _write_product_trade_snapshot(
        tmp_path,
        [
            _product_trade_record(
                reason_now="internal_queue_probe:abc_123",
            )
        ],
    )

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["rendered"] == 1
    text = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))["items"][0]["text"]
    assert "internal_queue_probe" not in text
    assert "\u0443\u0441\u043b\u043e\u0432\u0438\u044f \u0441\u0446\u0435\u043d\u0430\u0440\u0438\u044f" in text


def test_preview_builds_legacy_style_chart_card_from_prepared_candles(tmp_path):
    _write_market_data(tmp_path)
    _write_product_trade_snapshot(tmp_path, [_product_trade_record(source_signal_id="sig_product")])

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["charts_available"] == 1
    item = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))["items"][0]
    chart_path = Path(item["chart_path"])
    assert chart_path.parent.name == "paper_telegram_base_charts"
    assert chart_path.exists()
    assert chart_path == tmp_path / "state" / "derived" / "paper_telegram_base_charts" / "sig_product.png"


def test_preview_fetches_public_chart_when_prepared_candles_are_stale(monkeypatch, tmp_path):
    _write_market_data(tmp_path)
    created_at = "2026-07-04T20:37:57+00:00"
    _write_product_trade_snapshot(tmp_path, [_product_trade_record(source_signal_id="sig_product", created_at=created_at)])
    monkeypatch.setenv("STRATEGY_LAB_PAPER_TELEGRAM_FETCH_CHART_CANDLES", "1")
    calls = []

    class FakeProvider:
        def __init__(self, **_kwargs):
            pass

        def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
            calls.append((symbol, timeframe, start_ts, end_ts))
            step = 60 * 60_000
            base = end_ts - 79 * step
            return [
                {
                    "ts": base + idx * step,
                    "open": 100.0 + idx * 0.1,
                    "high": 101.0 + idx * 0.1,
                    "low": 99.5 + idx * 0.1,
                    "close": 100.4 + idx * 0.1,
                    "vol": 1000 + idx,
                }
                for idx in range(80)
            ]

    monkeypatch.setattr("src.research_lab.paper_telegram_preview.OkxPublicMarketDataProvider", FakeProvider)

    summary = build_paper_telegram_preview(tmp_path)

    assert calls
    assert calls[0][0] == "BTC_USDT_SWAP"
    assert calls[0][1] == "1h"
    assert summary["charts_available"] == 1
    item = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))["items"][0]
    assert Path(item["chart_path"]).parent.name == "paper_telegram_base_charts"


def test_preview_explicit_public_chart_fetch_does_not_require_env(monkeypatch, tmp_path):
    _write_market_data(tmp_path)
    created_at = "2026-07-04T20:37:57+00:00"
    _write_product_trade_snapshot(tmp_path, [_product_trade_record(source_signal_id="sig_product", created_at=created_at)])
    monkeypatch.delenv("STRATEGY_LAB_PAPER_TELEGRAM_FETCH_CHART_CANDLES", raising=False)
    calls = []

    class FakeProvider:
        def __init__(self, **_kwargs):
            pass

        def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
            calls.append((symbol, timeframe, start_ts, end_ts))
            step = 60 * 60_000
            base = end_ts - 79 * step
            return [
                {
                    "ts": base + idx * step,
                    "open": 100.0 + idx * 0.1,
                    "high": 101.0 + idx * 0.1,
                    "low": 99.5 + idx * 0.1,
                    "close": 100.4 + idx * 0.1,
                    "vol": 1000 + idx,
                }
                for idx in range(80)
            ]

    monkeypatch.setattr("src.research_lab.paper_telegram_preview.OkxPublicMarketDataProvider", FakeProvider)

    summary = build_paper_telegram_preview(tmp_path, fetch_public_chart_candles=True)

    assert calls
    assert summary["chart_path_types"] == {"paper_telegram_base_charts": 1}
    item = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))["items"][0]
    assert Path(item["chart_path"]).parent.name == "paper_telegram_base_charts"


def test_preview_ranks_product_trades_by_private_quality_report(tmp_path):
    _write_quality_report(
        tmp_path,
        [
            {"family": "continuation", "quality_label": "mixed", "rows": 100},
            {"family": "early_tp_tactical", "quality_label": "candidate_watch", "rows": 80},
        ],
    )
    _write_product_trade_snapshot(
        tmp_path,
        [
            _product_trade_record(
                paper_trade_id="paper_bad",
                source_signal_id="sig_bad",
                setup_family="continuation",
            ),
            _product_trade_record(
                paper_trade_id="paper_better",
                source_signal_id="sig_better",
                setup_family="early_tp_tactical",
            ),
        ],
    )

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["source_schema"] == "paper_product_trade_ledger.v1"
    assert summary["quality_ranked"] is True
    assert summary["rendered"] == 2
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["items"][0]["source_signal_id"] == "sig_better"
    assert data["items"][1]["source_signal_id"] == "sig_bad"


def test_preview_quality_gate_skips_weak_product_rows_for_subscribers(tmp_path):
    _write_quality_report(
        tmp_path,
        [
            {"family": "continuation", "quality_label": "needs_review", "rows": 100},
            {"family": "early_tp_tactical", "quality_label": "mixed", "rows": 80},
        ],
    )
    _write_product_trade_snapshot(
        tmp_path,
        [
            _product_trade_record(
                paper_trade_id="paper_bad",
                source_signal_id="sig_bad",
                setup_family="continuation",
            ),
            _product_trade_record(
                paper_trade_id="paper_ok",
                source_signal_id="sig_ok",
                setup_family="early_tp_tactical",
            ),
        ],
    )

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["records_read"] == 2
    assert summary["rendered"] == 1
    assert summary["skipped_quality_gate"] == 1
    assert summary["quality_gate_reasons"] == {"quality_label:needs_review": 1}
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert [item["source_signal_id"] for item in data["items"]] == ["sig_ok"]


def test_preview_quality_gate_allows_live_ready_product_rows(tmp_path):
    _write_quality_report(
        tmp_path,
        [{"family": "continuation", "quality_label": "needs_review", "rows": 100}],
    )
    _write_product_trade_snapshot(
        tmp_path,
        [
            _product_trade_record(
                source_signal_id="sig_live_ready",
                setup_family="continuation",
                live_ready=True,
                live_block_reason="",
                ready_strategy_id="ready_1",
                source_validation_verdict="PAPER_FORWARD_READY",
            ),
        ],
    )

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["rendered"] == 1
    assert summary["skipped_quality_gate"] == 0
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    assert data["items"][0]["source_signal_id"] == "sig_live_ready"
    assert data["items"][0]["validation_tier"] == "validated_pfr"
    assert VALIDATED_LABEL in data["items"][0]["text"]


def test_preview_skips_non_actionable_product_trades(tmp_path):
    _write_product_trade_snapshot(tmp_path, [_product_trade_record(status="reviewed")])

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["source_schema"] == "paper_product_trade_ledger.v1"
    assert summary["rendered"] == 0
    assert summary["skipped_non_actionable"] == 1


def test_preview_writes_durable_card_ledger(tmp_path):
    _write_paper_signal_snapshot(tmp_path, [_paper_signal_row(signal_id="sig_one")])
    first = build_paper_telegram_preview(tmp_path)
    ledger_path = Path(first["card_ledger_path"])
    first_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

    assert first["card_ledger_cards"] == 1
    assert first_ledger["schema"] == "paper_telegram_card_ledger.v1"
    assert first_ledger["items"][0]["source_signal_id"] == "sig_one"
    assert first_ledger["items"][0]["validation_tier"] == "farm_calculated"
    assert first_ledger["items"][0]["paper_only"] is True
    assert first_ledger["items"][0]["execution_allowed"] is False

    _write_paper_signal_snapshot(tmp_path, [_paper_signal_row(signal_id="sig_two")])
    second = build_paper_telegram_preview(tmp_path)
    second_ledger = json.loads(Path(second["card_ledger_path"]).read_text(encoding="utf-8"))

    assert second["rendered"] == 1
    assert second["card_ledger_cards"] == 2
    assert {item["source_signal_id"] for item in second_ledger["items"]} == {"sig_one", "sig_two"}


def test_preview_prefers_strict_main_paper_over_candidate_fallback(tmp_path):
    _write_trade_snapshot(tmp_path, [_trade_record()])
    _write_paper_signal_snapshot(tmp_path, [_paper_signal_row()])

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["source_schema"] == "main_paper_trade_ledger.v1"
    assert summary["rendered"] == 1


def test_preview_skips_provider_error_trade_cards(tmp_path):
    _write_consumer_snapshot(tmp_path, [_consumer_record()])
    _write_trade_snapshot(tmp_path, [_trade_record(status="provider_error")])

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["source_schema"] == "main_paper_trade_ledger.v1"
    assert summary["records_read"] == 1
    assert summary["rendered"] == 0
    assert summary["skipped_non_actionable"] == 1


def test_preview_falls_back_to_consumer_rows(tmp_path):
    _write_consumer_snapshot(
        tmp_path,
        [
            _consumer_record(consumer_status="rejected_contract", problems=["bad_schema"]),
            _consumer_record(instruction_id="mainpaper_ok"),
        ],
    )

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["source_schema"] == "main_paper_consumer.v1"
    assert summary["records_read"] == 2
    assert summary["rendered"] == 1
    assert summary["skipped_rejected"] == 1


def test_preview_validation_catches_bad_authority_and_length():
    row = _trade_record(execution_allowed=True)
    text = render_preview_text(row) + ("x" * (MAX_MESSAGE_CHARS + 1))

    problems = validate_preview(row, text)

    assert "execution_allowed_not_false" in problems
    assert "telegram_message_too_long" in problems


def test_preview_validation_catches_mojibake_text():
    row = _trade_record()
    problems = validate_preview(row, "Paper-\u0420\u00a0\u0420\u2019\u0456\u201a")

    assert "mojibake_text" in problems


def test_preview_writes_empty_snapshot_when_sources_missing(tmp_path):
    summary = build_paper_telegram_preview(tmp_path)

    assert summary["source_exists"] is False
    assert summary["records_read"] == 0
    assert summary["rendered"] == 0
    assert Path(summary["snapshot_path"]).exists()


def test_paper_telegram_preview_has_no_sender_imports():
    path = Path("src/research_lab/paper_telegram_preview.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "src.utils.telegram",
        "aiohttp",
        "requests",
        "src.exchange",
        "scripts.auto_execute",
        "dotenv",
        "hmac",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not (imported & forbidden)
