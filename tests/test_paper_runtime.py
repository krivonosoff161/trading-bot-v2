# -*- coding: utf-8 -*-
"""Tests for the minimal validated-setup paper runtime."""
from __future__ import annotations

import json
from pathlib import Path

from src.research_lab.hard_validation_contract import SetupCard
from src.research_lab.paper_contract import PaperRuntimeState, plan_from_setup_card
from src.research_lab.paper_journal import read_paper_outcomes
from src.research_lab.paper_runtime import (
    execute_plan_once,
    load_ready_setup_cards,
    run_paper_cycle,
)
from src.research_lab.setup_library import write_setup_library


def _card(**overrides) -> SetupCard:
    base = dict(
        setup_id="setup-c1",
        candidate_id="c1",
        symbol="ABC-USDT-SWAP",
        timeframe="1h",
        strategy_id="momentum_breakout",
        params={"direction": "long", "stop_pct": 2.0, "take_pct": 4.0, "hold_bars": 3},
        filters={},
        data_window={"start_ts": 1, "end_ts": 10, "n_bars": 10},
        lite_status="FORWARD_PAPER",
        hard_status="PAPER_FORWARD_READY",
        checks_summary={},
        failed_checks=[],
        risk_flags=[],
        entry_exit_summary="ready",
        regime_tags=[],
        paper_forward_ready=True,
    )
    base.update(overrides)
    return SetupCard(**base)


def _candles_for_take() -> list[dict]:
    rows = []
    prices = [100.0 + i * 0.01 for i in range(80)]
    prices[1] = 100.0
    prices[2] = 101.0
    prices[3] = 104.0
    prices[4] = 105.0
    for i, price in enumerate(prices):
        rows.append({
            "ts": 1_700_000_000_000 + i * 3_600_000,
            "open": float(price),
            "high": float(price + (5 if i == 3 else 1)),
            "low": float(price - 1),
            "close": float(price),
            "vol": 1000.0,
        })
    return rows


def _write_data(private_root: Path, candles: list[dict]) -> None:
    d = private_root / "market_data" / "1h"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "ABC_USDT_SWAP_test_1h.json"
    path.write_text(json.dumps(candles), encoding="utf-8")


def test_load_ready_setup_cards_filters_ready(tmp_path):
    write_setup_library(tmp_path, [_card(), _card(setup_id="setup-c2", paper_forward_ready=False)], dry_run=False)
    cards = load_ready_setup_cards(tmp_path)
    assert [c.setup_id for c in cards] == ["setup-c1"]


def test_execute_plan_once_closes_take_with_injected_no_lookahead_signal(monkeypatch):
    plan = plan_from_setup_card(_card())

    def fake_generate(visible, family, params):
        return [{"idx": 1, "side": "long", "reason": "unit"}] if len(visible) >= 2 else []

    monkeypatch.setattr("src.research_lab.paper_runtime.generate_signals", fake_generate)
    result = execute_plan_once(plan, _candles_for_take())
    assert result.status == "completed"
    assert result.outcome is not None
    assert result.outcome.state == PaperRuntimeState.CLOSED_TP.value
    assert result.outcome.outcome == "take"
    assert result.outcome.net_pct > 0
    assert result.trade_id


def test_execute_plan_once_uses_stop_before_take_same_bar(monkeypatch):
    plan = plan_from_setup_card(_card())
    candles = _candles_for_take()
    candles[1]["high"] = 110.0
    candles[1]["low"] = 95.0

    def fake_generate(visible, family, params):
        return [{"idx": 1, "side": "long", "reason": "unit"}] if len(visible) >= 2 else []

    monkeypatch.setattr("src.research_lab.paper_runtime.generate_signals", fake_generate)
    result = execute_plan_once(plan, candles)
    assert result.outcome is not None
    assert result.outcome.state == PaperRuntimeState.CLOSED_SL.value
    assert result.outcome.outcome == "stop"


def test_execute_plan_once_does_not_use_future_signal(monkeypatch):
    plan = plan_from_setup_card(_card())

    def fake_generate(visible, family, params):
        # This attempts to leak a future-bar signal. The runtime should ignore it
        # until that index is actually the current visible bar.
        return [{"idx": len(visible) + 2, "side": "long", "reason": "future"}]

    monkeypatch.setattr("src.research_lab.paper_runtime.generate_signals", fake_generate)
    result = execute_plan_once(plan, _candles_for_take())
    assert result.status == "skipped"
    assert result.reason == "no_signal"


def test_run_paper_cycle_writes_once_and_deduplicates(monkeypatch, tmp_path):
    write_setup_library(tmp_path, [_card()], dry_run=False)
    _write_data(tmp_path, _candles_for_take())

    def fake_generate(visible, family, params):
        return [{"idx": 1, "side": "long", "reason": "unit"}] if len(visible) >= 2 else []

    monkeypatch.setattr("src.research_lab.paper_runtime.generate_signals", fake_generate)
    first = run_paper_cycle(tmp_path, apply=True)
    second = run_paper_cycle(tmp_path, apply=True)
    assert first["counters"]["written"] == 1
    assert second["counters"]["already_recorded"] == 1
    rows = read_paper_outcomes(tmp_path)
    assert len(rows) == 1
    assert rows[0]["setup_id"] == "setup-c1"
