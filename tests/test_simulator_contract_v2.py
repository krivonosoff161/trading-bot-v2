from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from src.research_lab.experiment import compute_metrics, simulate_trades
from src.research_lab.hard_validation_contract import CandidateForValidation
from src.research_lab.candidate_registry import _metrics_summary
from src.research_lab.honest_backtest_bridge import _candidate_contract_errors
from src.research_lab.paper_signals.lane import _pending_outcome
from src.research_lab.paper_runtime import _decide_trade
from src.research_lab.setup_library import build_setup_card
from src.research_lab.true_forward import _pending_forward_signals, _rehydrate_pending_signals
from src.research_lab.simulator_contract import (
    allocate_shared_account,
    build_cost_ledger,
    build_legacy_combined_cost_ledger,
    build_simulator_assumption_manifest,
    build_trade_quantity_ledger,
    chronological_compounded_metrics,
    funding_cashflow,
    incremental_paper_lane_manifest,
    legacy_fixture_manifest,
    maker_fill,
    profit_factor_state,
    recompute_simulator_manifest_id,
    reconcile_partial_fills,
    resolve_ohlc_exit,
    validate_simulator_assumption_manifest,
    validate_trade_contract,
)
from src.research_lab.state_db import _import_farm_results, connect, init_db


def _bar(ts: int, open_: float, high: float, low: float, close: float) -> dict:
    return {"ts": ts, "open": open_, "high": high, "low": low, "close": close}


def test_manifest_identity_is_content_bound_and_tamper_evident() -> None:
    manifest = build_simulator_assumption_manifest()
    assert manifest["schema"] == "SimulatorAssumptionManifest.v2"
    assert manifest["evidence_tier"] == "bar_plausibility_scenario"
    validate_simulator_assumption_manifest(manifest)
    changed = copy.deepcopy(manifest)
    changed["policies"]["gap"] = "exact_trigger_fixture"
    with pytest.raises(ValueError, match="canonical"):
        validate_simulator_assumption_manifest(changed)
    changed["manifest_id"] = recompute_simulator_manifest_id(changed)
    with pytest.raises(ValueError, match="canonical"):
        validate_simulator_assumption_manifest(changed)
    unknown = copy.deepcopy(manifest)
    unknown["simulator_model_id"] = "unknown.future.model"
    unknown.pop("manifest_id")
    with pytest.raises(ValueError, match="unknown"):
        validate_simulator_assumption_manifest(unknown)


def test_incremental_paper_lane_has_a_distinct_same_bar_model() -> None:
    farm = legacy_fixture_manifest()
    lane = incremental_paper_lane_manifest()
    assert farm["manifest_id"] != lane["manifest_id"]
    assert farm["policies"]["same_bar"] == "entry_bar_included"
    assert lane["policies"]["same_bar"] == "entry_bar_exits_deferred"
    pending = _pending_outcome({}, opened=True)
    assert pending["simulator_manifest"]["manifest_id"] == lane["manifest_id"]


@pytest.mark.parametrize(
    ("side", "open_", "stop", "take", "expected"),
    [("long", 90.0, 95.0, 110.0, 90.0), ("short", 110.0, 105.0, 90.0, 110.0)],
)
def test_adverse_gap_stop_never_improves_first_bar_price(
    side: str, open_: float, stop: float, take: float, expected: float
) -> None:
    bar = _bar(2, open_, max(open_, 111.0), min(open_, 89.0), open_)
    result = resolve_ohlc_exit(side, entry_price=100.0, bar=bar, stop_price=stop, take_price=take)
    assert result["status"] == "resolved_adverse_gap"
    assert result["selected"]["price"] == expected
    assert result["selected"]["outcome"] == "stop"


def test_dual_touch_is_an_explicit_scenario_not_observed_order() -> None:
    result = resolve_ohlc_exit(
        "long",
        entry_price=100.0,
        bar=_bar(2, 100.0, 102.0, 98.0, 100.0),
        stop_price=99.0,
        take_price=101.0,
    )
    assert result["status"] == "ambiguous_intrabar_order"
    assert result["selected"] is None
    assert {item["outcome"] for item in result["scenarios"]} == {"stop", "take"}
    assert result["return_bounds_pct"] == [-1.0, 1.0]


@pytest.mark.parametrize(
    ("entry_price", "bar", "message"),
    [
        (0.0, _bar(2, 100.0, 101.0, 99.0, 100.0), "entry price"),
        (100.0, _bar(2, 100.0, float("nan"), 99.0, 100.0), "bar high"),
        (100.0, _bar(2, 100.0, 99.0, 101.0, 100.0), "OHLC bounds"),
    ],
)
def test_ohlc_exit_rejects_invalid_numeric_evidence(
    entry_price: float,
    bar: dict,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_ohlc_exit(
            "long",
            entry_price=entry_price,
            bar=bar,
            stop_price=99.0,
            take_price=101.0,
        )


def test_maker_touch_does_not_invent_order_or_quantity() -> None:
    impossible = maker_fill(requested_quantity=5.0, available_quantity=5.0, touch_order="exit_before_entry")
    assert impossible["status"] == "not_attainable_from_declared_order"
    assert impossible["filled_quantity"] == 0.0
    partial = maker_fill(requested_quantity=5.0, available_quantity=2.0, touch_order="entry_before_exit")
    assert partial["status"] == "partial_fill"
    assert partial["filled_quantity"] == 2.0
    assert maker_fill(5.0, 0.0, "entry_before_exit")["status"] == "no_fill"


def test_costs_and_funding_remain_separately_attributed() -> None:
    ledger = build_cost_ledger(
        fees_bps=7.0, spread_bps=2.0, slippage_bps=3.0, impact_bps=4.0, funding_pct=-0.05
    )
    assert ledger["components_pct"] == {
        "fees": 0.07, "spread": 0.02, "slippage": 0.03, "impact": 0.04, "funding": -0.05
    }
    assert ledger["total_pct"] == 0.11
    events = [{"ts": 100, "rate_pct": -0.01}, {"ts": 200, "rate_pct": -0.02}]
    assert funding_cashflow(90, 150, events)["total_pct"] == -0.01
    assert funding_cashflow(90, 99, events)["total_pct"] == 0.0


def test_partial_fill_ledger_conserves_quantity_and_cost_once() -> None:
    result = reconcile_partial_fills(
        10.0,
        [
            {"quantity": 4.0, "price": 110.0, "cost_pct": 0.01},
            {"quantity": 6.0, "price": 90.0, "cost_pct": 0.02},
        ],
        entry_price=100.0,
    )
    assert result["closed_quantity"] == 10.0
    assert result["remaining_quantity"] == 0.0
    assert result["gross_proceeds"] == 980.0
    assert result["cost_amount"] == 0.16
    assert result["net_pnl"] == -20.16
    with pytest.raises(ValueError, match="exceeds"):
        reconcile_partial_fills(
            10.0, [{"quantity": 11.0, "price": 100.0, "cost_pct": 0.0}],
            entry_price=100.0,
        )


def test_trade_contract_recomputes_partial_fill_reconciliation() -> None:
    manifest = legacy_fixture_manifest()
    reconciliation = reconcile_partial_fills(
        10.0,
        [
            {"quantity": 4.0, "price": 110.0, "cost_pct": 0.01},
            {"quantity": 6.0, "price": 90.0, "cost_pct": 0.02},
        ],
        entry_price=100.0,
    )
    trade = {
        "side": "long",
        "net_pct": reconciliation["net_return_pct"],
        "partial_exit_fraction": 0.4,
        "simulator_manifest": manifest,
        "simulator_model_id": manifest["simulator_model_id"],
        "unsupported_simulator_dimensions": manifest["unsupported_dimensions"],
        "cost_ledger": build_cost_ledger(fees_bps=1.6),
        "quantity_ledger": build_trade_quantity_ledger(
            entry_quantity=10.0, closed_legs=(4.0, 6.0)
        ),
        "fill_reconciliation": reconciliation,
    }
    validate_trade_contract(trade, manifest)
    forged = copy.deepcopy(trade)
    forged["fill_reconciliation"].update({
        "entry_quantity": 999.0,
        "closed_quantity": -1.0,
        "fills": [],
    })
    with pytest.raises(ValueError, match="fill|required|positive"):
        validate_trade_contract(forged, manifest)


def test_trade_contract_binds_cost_ledger_to_net_return() -> None:
    candles = [_bar(1, 100, 100, 100, 100), _bar(2, 100, 100, 100, 100)]
    trade = simulate_trades(
        candles,
        [{"idx": 0, "side": "long", "reason": "fixture"}],
        {"hold_bars": 1, "stop_pct": 10, "take_pct": 10},
        fees_bps=7,
        slippage_bps=3,
    )[0]
    manifest = trade["simulator_manifest"]
    validate_trade_contract(trade, manifest)
    forged = copy.deepcopy(trade)
    forged["cost_ledger"] = build_cost_ledger()
    with pytest.raises(ValueError, match="net return"):
        validate_trade_contract(forged, manifest)


@pytest.mark.parametrize("invalid_net", [None, "not-a-number", float("nan"), True])
def test_trade_contract_rejects_missing_or_non_finite_net_return(invalid_net) -> None:
    candles = [_bar(1, 100, 100, 100, 100), _bar(2, 100, 100, 100, 100)]
    trade = simulate_trades(
        candles,
        [{"idx": 0, "side": "long", "reason": "fixture"}],
        {"hold_bars": 1, "stop_pct": 10, "take_pct": 10},
        fees_bps=0,
        slippage_bps=0,
    )[0]
    manifest = trade["simulator_manifest"]
    trade["net_pct"] = invalid_net

    with pytest.raises(ValueError, match="trade net return"):
        validate_trade_contract(trade, manifest)


def test_trade_contract_rejects_claim_amplified_ledgers() -> None:
    candles = [_bar(1, 100, 100, 100, 100), _bar(2, 100, 100, 100, 100)]
    trade = simulate_trades(
        candles,
        [{"idx": 0, "side": "long", "reason": "fixture"}],
        {"hold_bars": 1, "stop_pct": 10, "take_pct": 10},
        fees_bps=7,
        slippage_bps=3,
    )[0]
    manifest = trade["simulator_manifest"]
    forged_quantity = copy.deepcopy(trade)
    forged_quantity["quantity_ledger"].update({
        "basis": "observed_market_fill_quantity",
        "availability_status": "observed_sufficient_liquidity",
    })
    with pytest.raises(ValueError, match="quantity claims"):
        validate_trade_contract(forged_quantity, manifest)
    forged_cost = copy.deepcopy(trade)
    forged_cost["cost_ledger"] = {
        "schema": "TradeCostLedger.v2",
        "components_pct": {"observed_spread": 0.1},
        "total_pct": 0.1,
        "attribution_status": "observed_execution",
    }
    with pytest.raises(ValueError, match="component names"):
        validate_trade_contract(forged_cost, manifest)


def test_scenario_manifest_rejects_legacy_combined_cost_attribution() -> None:
    manifest = build_simulator_assumption_manifest()
    trade = {
        "side": "long",
        "gross_pct": 1.0,
        "net_pct": 0.9,
        "simulator_manifest": manifest,
        "simulator_model_id": manifest["simulator_model_id"],
        "unsupported_simulator_dimensions": manifest["unsupported_dimensions"],
        "cost_ledger": build_legacy_combined_cost_ledger(0.001),
        "quantity_ledger": build_trade_quantity_ledger(),
    }
    with pytest.raises(ValueError, match="simulator policy"):
        validate_trade_contract(trade, manifest)


def test_portfolio_metrics_compound_chronologically() -> None:
    allocation = allocate_shared_account([
        {"id": "a", "entry_ts": 1, "exit_ts": 2, "requested_capacity": 1.0, "return_pct": 50.0},
        {"id": "b", "entry_ts": 2, "exit_ts": 3, "requested_capacity": 1.0, "return_pct": -25.0},
        {"id": "c", "entry_ts": 3, "exit_ts": 4, "requested_capacity": 1.0, "return_pct": -25.0},
    ], simulator_manifest=legacy_fixture_manifest())
    metrics = chronological_compounded_metrics(allocation)
    assert metrics["ending_equity"] == 0.84375
    assert metrics["total_return_pct"] == -15.625
    assert metrics["max_drawdown_pct"] == 43.75


def test_overlapping_signals_reserve_one_shared_account() -> None:
    allocation = allocate_shared_account([
        {"id": "first", "entry_ts": 1, "exit_ts": 5, "requested_capacity": 1.0, "return_pct": 50.0},
        {"id": "overlap", "entry_ts": 2, "exit_ts": 4, "requested_capacity": 0.5, "return_pct": 100.0},
        {"id": "later", "entry_ts": 5, "exit_ts": 6, "requested_capacity": 1.0, "return_pct": -25.0},
    ], simulator_manifest=legacy_fixture_manifest())
    assert [item["status"] for item in allocation["decisions"]] == [
        "accepted", "rejected_insufficient_capacity", "accepted"
    ]
    assert chronological_compounded_metrics(allocation)["ending_equity"] == 1.125
    tampered = copy.deepcopy(allocation)
    tampered["decisions"][0]["return_pct"] = 999.0
    with pytest.raises(ValueError, match="identity"):
        chronological_compounded_metrics(tampered)


def test_rehashed_overallocation_is_rejected_by_policy_replay() -> None:
    allocation = allocate_shared_account([
        {"id": "a", "entry_ts": 1, "exit_ts": 5,
         "requested_capacity": 1.0, "return_pct": 10.0},
        {"id": "b", "entry_ts": 2, "exit_ts": 4,
         "requested_capacity": 1.0, "return_pct": 10.0},
    ], simulator_manifest=legacy_fixture_manifest())
    forged = copy.deepcopy(allocation)
    forged["decisions"][1]["status"] = "accepted"
    forged["decisions"][1]["allocated_capacity"] = 1.0
    payload = {key: value for key, value in forged.items() if key != "allocation_id"}
    forged["allocation_id"] = "alloc_" + hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    with pytest.raises(ValueError, match="policy"):
        chronological_compounded_metrics(forged)


def test_profit_factor_uses_tagged_json_safe_states() -> None:
    assert profit_factor_state([1.0, 2.0]) == {
        "schema": "ProfitFactorState.v2", "state": "positive_infinity", "value": None
    }
    assert profit_factor_state([])["state"] == "insufficient_data"
    assert profit_factor_state([0.0, 0.0])["state"] == "undefined"
    metrics = compute_metrics(
        [{"net_pct": 1.0, "regime": {}}, {"net_pct": 2.0, "regime": {}}],
        split_ratio=0.5,
        min_trades=1,
    )
    assert metrics["profit_factor"] is None
    assert metrics["profit_factor_state"]["state"] == "positive_infinity"
    assert metrics["aggregate_basis"] == "independent_what_if_additive_v1"
    assert metrics["portfolio_metrics_status"].startswith("unavailable_without")


def test_true_forward_mode_does_not_finalize_an_incomplete_horizon() -> None:
    candles = [
        _bar(1, 100.0, 100.5, 99.5, 100.0),
        _bar(2, 100.0, 101.0, 99.0, 100.0),
        _bar(3, 100.0, 101.0, 99.0, 100.0),
    ]
    signals = [{"idx": 1, "side": "long", "reason": "fixture"}]
    trades = simulate_trades(
        candles,
        signals,
        {"hold_bars": 5, "stop_pct": 10.0, "take_pct": 10.0},
        fees_bps=0.0,
        slippage_bps=0.0,
        require_complete_horizon=True,
    )
    assert trades == []
    pending = _pending_forward_signals(candles, signals, trades, hold_bars=5)
    assert pending == [{
        "entry_ts": 2, "side": "long", "reason": "fixture", "regime": {},
    }]
    assert _rehydrate_pending_signals(candles, pending) == [
        {"idx": 1, "side": "long", "reason": "fixture", "regime": {}}
    ]


def test_paper_runtime_does_not_timeout_before_full_horizon() -> None:
    candles = [
        _bar(1, 100.0, 100.5, 99.5, 100.0),
        _bar(2, 100.0, 101.0, 99.0, 100.0),
        _bar(3, 100.0, 101.0, 99.0, 100.0),
    ]
    plan = SimpleNamespace(
        max_hold={"bars": 5}, stop_loss={"value": 10.0},
        take_profit=[{"value": 10.0}], fees_bps=0.0, slippage_bps=0.0,
    )
    signal = {"idx": 1, "side": "long", "reason": "fixture"}
    assert _decide_trade(candles, plan, signal) is None


def test_validation_and_setup_descendants_carry_the_exact_simulator_identity() -> None:
    manifest = legacy_fixture_manifest()
    candidate = CandidateForValidation(
        candidate_id="c1", source_run_id="r1", symbol="BTC-USDT-SWAP",
        normalized_symbol="BTC_USDT_SWAP", timeframe="1h", strategy_id="trend",
        params={}, filters={}, fees_bps=7.0, slippage_bps=3.0,
        lite_status="FORWARD_PAPER", lite_reasons=[], risk_flags=[], metrics={},
        trades=[], equity_curve=[], data_window={}, created_at="2026-07-18T00:00:00Z",
        simulator_manifest=manifest,
        unsupported_simulator_dimensions=manifest["unsupported_dimensions"],
    )
    restored = CandidateForValidation.from_dict(candidate.to_dict())
    assert restored.simulator_manifest["manifest_id"] == manifest["manifest_id"]
    report = {
        "candidate_id": "c1", "symbol": "BTC-USDT-SWAP", "timeframe": "1h",
        "strategy_id": "trend", "verdict": {"hard_status": "NEEDS_MORE_DATA"},
        "checks_summary": {}, "simulator_manifest": manifest,
        "unsupported_simulator_dimensions": manifest["unsupported_dimensions"],
        "simulator_claim_ceiling": manifest["claim_ceiling"],
    }
    card = build_setup_card(report, {"params": {}})
    assert card.simulator_manifest["manifest_id"] == manifest["manifest_id"]
    assert card.simulator_claim_ceiling == "deterministic_fixture"
    assert card.unsupported_simulator_dimensions == manifest["unsupported_dimensions"]
    tampered_report = copy.deepcopy(report)
    tampered_report["simulator_claim_ceiling"] = "observed_paper"
    with pytest.raises(ValueError, match="provenance"):
        build_setup_card(tampered_report, {"params": {}})
    bad_trade_candidate = CandidateForValidation(
        **{**candidate.__dict__, "trades": [{"net_pct": 1.0}]}
    )
    assert "invalid_simulator_or_trade_manifest" in _candidate_contract_errors(
        bad_trade_candidate
    )


def test_profit_factor_state_survives_summary_and_sqlite_roundtrip(tmp_path) -> None:
    state = {"schema": "ProfitFactorState.v2", "state": "positive_infinity", "value": None}
    metrics = {
        "n_trades": 2, "win_rate": 1.0, "avg_net_pct": 1.0,
        "test_avg_net_pct": 1.0, "profit_factor": None,
        "profit_factor_state": state, "max_drawdown_pct": 0.0,
    }
    assert _metrics_summary(metrics)["profit_factor_state"] == state
    conn = connect(tmp_path / "state.sqlite3")
    init_db(conn)
    conn.execute(
        "INSERT INTO runs(run_id,experiment_id,created_at,artifact_label,imported_at) "
        "VALUES ('r1','e1','now','a1','now')"
    )
    _import_farm_results(
        conn, "r1", {"runtime": {}, "created_at": "now"},
        [{"run_id": "c1", "symbol": "BTC", "family": "trend", "metrics": metrics}],
    )
    row = conn.execute(
        "SELECT profit_factor, profit_factor_state_json FROM farm_results"
    ).fetchone()
    assert row["profit_factor"] == 0.0
    assert json.loads(row["profit_factor_state_json"]) == state
    conn.close()


def test_legacy_fixture_is_never_silently_promoted() -> None:
    legacy = legacy_fixture_manifest()
    assert legacy["simulator_model_id"] == "deterministic_ohlc_fixture.v1"
    assert legacy["evidence_tier"] == "deterministic_fixture"
    assert "intrabar_event_order" in legacy["unsupported_dimensions"]
    assert "liquidity" in legacy["unsupported_dimensions"]
    candles = [_bar(1, 100, 101, 99, 100), _bar(2, 100, 102, 98, 101)]
    trade = simulate_trades(
        candles, [{"idx": 0, "side": "long", "reason": "fixture"}],
        {"hold_bars": 1, "stop_pct": 10, "take_pct": 10},
        fees_bps=7, slippage_bps=3,
    )[0]
    assert trade["simulator_manifest"]["manifest_id"] == legacy["manifest_id"]
    assert trade["cost_ledger"]["components_pct"]["fees"] == 0.07
