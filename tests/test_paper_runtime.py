# -*- coding: utf-8 -*-
"""Tests for the minimal validated-setup paper runtime."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.research_lab.hard_validation_contract import SetupCard
from src.research_lab.candle_store import CandleStore
from src.research_lab.paper_contract import PaperRuntimeState, plan_from_setup_card
from src.research_lab.paper_journal import read_paper_outcomes
from src.research_lab.paper_runtime import (
    _PAPER_SIGNAL_SEARCH_BARS,
    _candidate_signals_no_lookahead,
    execute_plan_once,
    load_ready_setup_cards,
    run_paper_cycle,
)
from src.research_lab.paper_readiness import summarize_paper_readiness
from src.research_lab.setup_library import write_setup_library
from src.research_lab.simulator_contract import legacy_fixture_manifest
from src.research_lab.strategy_history_proof import synthetic_candles
from src.research_lab.strategy_registry import REGISTRY

_SIMULATOR_MANIFEST = legacy_fixture_manifest()


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
        simulator_manifest=_SIMULATOR_MANIFEST,
        unsupported_simulator_dimensions=_SIMULATOR_MANIFEST["unsupported_dimensions"],
        simulator_claim_ceiling=_SIMULATOR_MANIFEST["claim_ceiling"],
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


def test_paper_readiness_explains_non_ready_cards(tmp_path):
    write_setup_library(
        tmp_path,
        [
            _card(),
            _card(setup_id="setup-c2", hard_status="NEEDS_MORE_DATA", paper_forward_ready=False),
        ],
        dry_run=False,
    )
    readiness = summarize_paper_readiness(tmp_path)
    assert readiness["checked_cards"] == 2
    assert readiness["paper_forward_ready"] == 1
    assert readiness["by_hard_status"]["NEEDS_MORE_DATA"] == 1
    assert readiness["blocked_reasons"]["hard_status:NEEDS_MORE_DATA"] == 1


def test_execute_plan_once_closes_take_with_injected_no_lookahead_signal(monkeypatch):
    plan = plan_from_setup_card(_card())

    def fake_generate(visible, family, params):
        return [{"idx": 1, "side": "long", "reason": "unit"}] if len(visible) >= 2 else []

    monkeypatch.setattr("src.research_lab.paper_runtime.generate_signals", fake_generate)
    result = execute_plan_once(plan, _candles_for_take())
    assert result.status == "completed"
    assert result.outcome is not None
    assert result.outcome.direction == "long"
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


def test_execute_plan_once_accepts_both_side_plan(monkeypatch):
    plan = plan_from_setup_card(
        _card(params={"stop_pct": 2.0, "take_pct": 4.0, "hold_bars": 3})
    )

    def fake_generate(visible, family, params):
        return [{"idx": 1, "side": "long", "reason": "unit"}] if len(visible) >= 2 else []

    monkeypatch.setattr("src.research_lab.paper_runtime.generate_signals", fake_generate)
    result = execute_plan_once(plan, _candles_for_take())
    assert result.status == "completed"
    assert result.outcome is not None


def test_run_paper_cycle_writes_once_and_deduplicates(monkeypatch, tmp_path):
    write_setup_library(tmp_path, [_card()], dry_run=False)
    _write_data(tmp_path, _candles_for_take())
    CandleStore(tmp_path).upsert_candles(
        "ABC_USDT_SWAP", "1h", _candles_for_take(),
        source="paper-runtime-fixture", available_at_ms=1,
    )

    def fake_generate(visible, family, params):
        return [{"idx": 1, "side": "long", "reason": "unit"}] if len(visible) >= 2 else []

    monkeypatch.setattr("src.research_lab.paper_runtime.generate_signals", fake_generate)
    first = run_paper_cycle(tmp_path, apply=True)
    second = run_paper_cycle(tmp_path, apply=True)
    assert first["counters"]["written"] == 1
    assert first["readiness"]["paper_forward_ready"] == 1
    assert second["counters"]["already_recorded"] == 1
    rows = read_paper_outcomes(tmp_path)
    assert len(rows) == 1
    assert rows[0]["setup_id"] == "setup-c1"

    from src.research_lab.state_db import connect, default_db_path, init_db
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    try:
        db_rows = [dict(r) for r in conn.execute("SELECT * FROM paper_outcomes")]
    finally:
        conn.close()
    assert len(db_rows) == 1
    assert db_rows[0]["candidate_id"] == "c1"
    assert db_rows[0]["state"] == PaperRuntimeState.CLOSED_TP.value


def test_paper_outcome_reader_streams_and_reports_completed_chunks(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "paper" / "paper_trades.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "".join(
            json.dumps({"trade_id": f"t-{index}", "state": "closed"}) + "\n"
            for index in range(2001)
        ),
        encoding="utf-8",
    )
    progress: list[int] = []

    def forbidden_read_text(*_args, **_kwargs):
        raise AssertionError("paper journal must be streamed, not loaded wholesale")

    monkeypatch.setattr(Path, "read_text", forbidden_read_text)

    rows = read_paper_outcomes(tmp_path, progress=progress.append)

    assert len(rows) == 2001
    assert progress == [1000, 2000, 2001]


def test_bounded_no_lookahead_matches_prefix_reference_for_registry_defaults():
    def prefix_reference(candles, plan):
        found = {}
        definition = REGISTRY[plan.family]
        for end in range(1, len(candles) + 1):
            for signal in definition.generate_signals(candles[:end], plan.params):
                idx = int(signal["idx"])
                if idx != end - 1:
                    continue
                if (
                    plan.direction != "both"
                    and str(signal.get("side") or "").lower() != plan.direction
                ):
                    continue
                found[idx] = dict(signal)
        return [found[index] for index in sorted(found)]

    base_plan = plan_from_setup_card(_card())
    for strategy_id, definition in REGISTRY.items():
        params = dict(definition.parameter_defaults)
        params.setdefault("stop_pct", 2.0)
        params.setdefault("take_pct", 4.0)
        params.setdefault("hold_bars", 3)
        plan = replace(
            base_plan,
            family=strategy_id,
            params=params,
        )
        candles = synthetic_candles(
            220,
            include_required_data=definition.required_data,
        )
        assert _candidate_signals_no_lookahead(candles, plan) == prefix_reference(
            candles,
            plan,
        )


def test_no_lookahead_work_is_history_bounded_and_reports_completed_chunks(monkeypatch):
    plan = plan_from_setup_card(_card())
    candles = _candles_for_take() * 30
    visible_sizes: list[int] = []
    progress: list[tuple[str, int, int]] = []

    def fake_generate(visible, _family, _params):
        visible_sizes.append(len(visible))
        return []

    monkeypatch.setattr("src.research_lab.paper_runtime.generate_signals", fake_generate)
    _candidate_signals_no_lookahead(
        candles,
        plan,
        chunk_size=250,
        progress=lambda stage, completed, total: progress.append(
            (stage, completed, total)
        ),
    )

    declared_bound = (
        REGISTRY[plan.family].required_history_bars(plan.params)
        + 2
        + int(plan.max_hold["bars"])
        + _PAPER_SIGNAL_SEARCH_BARS
    )
    assert len(visible_sizes) == min(len(candles), declared_bound)
    assert max(visible_sizes) <= declared_bound
    assert progress[-1] == (
        "signal_history_chunk_completed",
        min(len(candles), declared_bound),
        min(len(candles), declared_bound),
    )


def test_cycle_blocks_write_after_active_check_failure(tmp_path, monkeypatch):
    write_setup_library(tmp_path, [_card()], dry_run=False)
    _write_data(tmp_path, _candles_for_take())
    CandleStore(tmp_path).upsert_candles(
        "ABC_USDT_SWAP",
        "1h",
        _candles_for_take(),
        source="paper-runtime-fixture",
        available_at_ms=1,
    )
    checks = 0

    def check_active():
        nonlocal checks
        checks += 1
        if checks >= 4:
            raise RuntimeError("heartbeat failed")

    appended: list[object] = []
    monkeypatch.setattr(
        "src.research_lab.paper_runtime.append_paper_outcome",
        lambda *_args, **_kwargs: appended.append(object()),
    )

    with pytest.raises(RuntimeError, match="heartbeat failed"):
        run_paper_cycle(tmp_path, apply=True, check_active=check_active)
    assert appended == []
