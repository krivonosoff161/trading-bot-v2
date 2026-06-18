# -*- coding: utf-8 -*-
"""Tests for the paper-trading contract (farm -> PaperTradePlan -> PaperTradeOutcome).

Paper/research only: these tests assert the contract is order-free, that only a PASS
SetupCard can become a plan, and that raw intake cannot.
"""
from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

from src.research_lab.hard_validation_contract import SetupCard
from src.research_lab.paper_contract import (
    PaperPlanError,
    PaperRuntimeState,
    PaperTradeOutcome,
    PaperTradePlan,
    plan_from_setup_card,
)

_ROOT = Path(__file__).resolve().parents[1]
_MODULE = "src/research_lab/paper_contract.py"


def _pass_card(**overrides) -> SetupCard:
    """A SetupCard that has cleared lite + hard validation (paper-forward-ready)."""
    base = dict(
        setup_id="setup-abc123",
        candidate_id="abc123",
        symbol="BTC-USDT-SWAP",
        timeframe="1h",
        strategy_id="momentum_breakout",
        params={"direction": "long", "stop_pct": 1.5, "take_pct": 3.0, "hold_bars": 5},
        filters={},
        data_window={
            "start_ts": 1_700_000_000_000,
            "end_ts": 1_700_864_000_000,
            "n_bars": 240,
            "timeframe": "1h",
        },
        lite_status="FORWARD_PAPER",
        hard_status="PAPER_FORWARD_READY",
        checks_summary={},
        failed_checks=[],
        risk_flags=[],
        entry_exit_summary="long breakout",
        regime_tags=["trend"],
        paper_forward_ready=True,
    )
    base.update(overrides)
    return SetupCard(**base)


# Happy path.

def test_pass_card_builds_a_plan_with_expected_fields():
    plan = plan_from_setup_card(_pass_card())
    assert isinstance(plan, PaperTradePlan)
    assert plan.setup_id == "setup-abc123"
    assert plan.candidate_id == "abc123"
    assert plan.symbol == "BTC-USDT-SWAP"
    assert plan.timeframe == "1h"
    assert plan.family == "momentum_breakout"
    assert plan.direction == "long"
    assert plan.entry_rule == "next_bar_open"
    assert plan.stop_loss == {"type": "pct", "value": 1.5}
    assert plan.take_profit == [{"type": "pct", "value": 3.0, "size_frac": 1.0}]
    assert plan.max_hold == {"bars": 5}
    assert plan.fees_bps == 7.0 and plan.slippage_bps == 3.0
    assert plan.source_validation_verdict == {
        "lite": "FORWARD_PAPER",
        "hard": "PAPER_FORWARD_READY",
    }
    assert plan.params_hash and plan.data_fingerprint


# data_window completeness (fingerprint provenance).

def test_empty_data_window_rejected():
    with pytest.raises(PaperPlanError):
        plan_from_setup_card(_pass_card(data_window={}))


def test_zero_n_bars_rejected():
    with pytest.raises(PaperPlanError):
        plan_from_setup_card(
            _pass_card(data_window={"n_bars": 0, "start_ts": 1, "end_ts": 2})
        )


@pytest.mark.parametrize(
    "window",
    [
        {"n_bars": 240, "start_ts": 1_700_000_000_000},   # missing end_ts
        {"n_bars": 240, "end_ts": 1_700_864_000_000},     # missing start_ts
        {"n_bars": 240},                                   # missing both
    ],
)
def test_missing_start_or_end_ts_rejected(window):
    with pytest.raises(PaperPlanError):
        plan_from_setup_card(_pass_card(data_window=window))


def test_explicit_data_window_fingerprint_accepted():
    # An explicit, non-empty fingerprint short-circuits reconstruction.
    plan = plan_from_setup_card(_pass_card(data_window={"fingerprint": "abc123fixed"}))
    assert plan.data_fingerprint == "abc123fixed"


# Validated only.

@pytest.mark.parametrize(
    "overrides",
    [
        {"paper_forward_ready": False},
        {"hard_status": "NEEDS_MORE_DATA"},
        {"lite_status": "OBSERVE"},
        {"hard_status": "FAILED_COSTS", "paper_forward_ready": False},
    ],
)
def test_non_validated_card_does_not_build_a_plan(overrides):
    with pytest.raises(PaperPlanError):
        plan_from_setup_card(_pass_card(**overrides))


# Raw intake rejected.

@pytest.mark.parametrize(
    "raw",
    [
        {"symbol": "BTC-USDT-SWAP", "side": "long", "source": "scanner_watch"},
        {"event": "listing", "symbol": "FOO-USDT", "score": 0.9},
        "FORWARD_PAPER",
        None,
        ["BTC-USDT-SWAP", "long"],
    ],
)
def test_raw_intake_cannot_construct_a_plan(raw):
    with pytest.raises(PaperPlanError):
        plan_from_setup_card(raw)  # type: ignore[arg-type]


# Missing required fields fail.

@pytest.mark.parametrize(
    "bad_params",
    [
        {"stop_pct": 1.5, "take_pct": 3.0, "hold_bars": 5},          # no direction
        {"direction": "long", "take_pct": 3.0, "hold_bars": 5},      # no stop_pct
        {"direction": "long", "stop_pct": 1.5, "hold_bars": 5},      # no take_pct
        {"direction": "long", "stop_pct": 1.5, "take_pct": 3.0},     # no hold_bars
        {"direction": "sideways", "stop_pct": 1.5, "take_pct": 3.0, "hold_bars": 5},
        {"direction": "long", "stop_pct": -1.0, "take_pct": 3.0, "hold_bars": 5},
    ],
)
def test_builder_rejects_incomplete_params(bad_params):
    with pytest.raises(PaperPlanError):
        plan_from_setup_card(_pass_card(params=bad_params))


@pytest.mark.parametrize(
    "field_overrides",
    [
        {"setup_id": ""},
        {"candidate_id": ""},
        {"symbol": ""},
        {"params_hash": ""},
        {"data_fingerprint": ""},
        {"direction": "up"},
        {"entry_rule": ""},
        {"stop_loss": {"type": "pct", "value": 0.0}},
        {"take_profit": []},
        {"max_hold": {"bars": 0}},
        {"fees_bps": -1.0},
        {"source_validation_verdict": {"lite": "OBSERVE", "hard": "PAPER_FORWARD_READY"}},
    ],
)
def test_plan_dataclass_rejects_missing_or_invalid_fields(field_overrides):
    good = dict(
        setup_id="setup-x",
        candidate_id="x",
        params_hash="deadbeef",
        data_fingerprint="cafef00d",
        symbol="ETH-USDT-SWAP",
        timeframe="15m",
        family="bb_fade",
        direction="short",
        params={"stop_pct": 1.0},
        entry_rule="next_bar_open",
        stop_loss={"type": "pct", "value": 1.0},
        take_profit=[{"type": "pct", "value": 2.0, "size_frac": 1.0}],
        invalidation={"type": "none"},
        max_hold={"bars": 4},
        source_validation_verdict={"lite": "FORWARD_PAPER", "hard": "PAPER_FORWARD_READY"},
    )
    good.update(field_overrides)
    with pytest.raises(PaperPlanError):
        PaperTradePlan(**good)


# Deterministic serialization.

def test_plan_serialization_is_deterministic_and_roundtrips():
    plan = plan_from_setup_card(_pass_card())
    d1 = plan.to_dict()
    assert PaperTradePlan.from_dict(d1) == plan
    # JSON is stable across repeated dumps and across a round-trip.
    blob = json.dumps(d1, sort_keys=True)
    assert blob == json.dumps(plan.to_dict(), sort_keys=True)
    assert blob == json.dumps(PaperTradePlan.from_dict(d1).to_dict(), sort_keys=True)


def test_outcome_serialization_roundtrips():
    plan = plan_from_setup_card(_pass_card())
    outcome = PaperTradeOutcome.from_plan(
        plan,
        trade_id="trade-1",
        state=PaperRuntimeState.CLOSED_TP,
        outcome="take",
        reason="closed_tp",
        net_pct=2.9,
        r_multiple=1.93,
    )
    d = outcome.to_dict()
    assert d["schema"] == "PaperTradeOutcome.v1"
    assert d["candidate_id"] == plan.candidate_id  # join key carried forward
    assert d["state"] == "closed_tp"
    assert PaperTradeOutcome.from_dict(d) == outcome
    assert json.dumps(d, sort_keys=True) == json.dumps(
        PaperTradeOutcome.from_dict(d).to_dict(), sort_keys=True
    )


def test_outcome_rejects_unknown_state():
    plan = plan_from_setup_card(_pass_card())
    with pytest.raises(PaperPlanError):
        PaperTradeOutcome.from_plan(plan, trade_id="t1", state="moon")


def _good_outcome_kwargs(**overrides) -> dict:
    base = dict(
        trade_id="t1",
        setup_id="setup-x",
        candidate_id="x",
        symbol="BTC-USDT-SWAP",
        timeframe="1h",
        family="bb_fade",
        direction="long",
        data_fingerprint="cafef00d",
        params_hash="deadbeef",
        state="closed_tp",
    )
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "override",
    [
        {"trade_id": ""},
        {"setup_id": ""},
        {"candidate_id": ""},
        {"symbol": ""},
        {"timeframe": ""},
        {"family": ""},
        {"data_fingerprint": ""},
        {"params_hash": ""},
    ],
)
def test_outcome_rejects_missing_required_fields(override):
    with pytest.raises(PaperPlanError):
        PaperTradeOutcome(**_good_outcome_kwargs(**override))


@pytest.mark.parametrize("schema", ["", "PaperTradeOutcome.v2", "other"])
def test_outcome_rejects_wrong_schema(schema):
    with pytest.raises(PaperPlanError):
        PaperTradeOutcome(**_good_outcome_kwargs(schema=schema))


# No order fields in the contract.

def test_plan_has_no_order_or_credential_fields():
    names = {f.name for f in dataclasses.fields(PaperTradePlan)}
    forbidden = {
        "order_id", "client_order_id", "clordid", "ord_type", "ordtype",
        "leverage", "lever", "td_mode", "tdmode", "pos_side", "posside",
        "api_key", "secret_key", "passphrase", "auto_trade", "live",
        "sz", "px", "side_okx",
    }
    assert names.isdisjoint(forbidden), f"order/credential fields leaked: {names & forbidden}"


# No private import / denylist AST scan over the module.

_FORBIDDEN_IMPORTS = (
    "src.exchange", "src.exchange.okx_client", "scripts.auto_execute",
    "src.data.impulse_pump_engine", "src.data.main_impulse_engine",
    "src.utils.telegram", "src.scout.scanner_v0", "src.config", "main",
)
_FORBIDDEN_TOKENS = (
    "place_market_order", "place_order", "execute_signal", "set_leverage",
)


def _imported_modules(tree: ast.AST) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_paper_contract_has_no_live_or_private_coupling():
    text = (_ROOT / _MODULE).read_text(encoding="utf-8")
    tree = ast.parse(text)
    for mod in _imported_modules(tree):
        for bad in _FORBIDDEN_IMPORTS:
            assert not (mod == bad or mod.startswith(bad + ".")), f"forbidden import {mod}"
    # strip the module docstring so safety prose (e.g. 'AUTO_TRADE') is not a false hit
    body = tree.body
    doc = (
        body[0].value.value
        if body and isinstance(body[0], ast.Expr)
        and isinstance(getattr(body[0], "value", None), ast.Constant)
        else ""
    )
    code = text.replace(str(doc), "", 1) if doc else text
    for token in _FORBIDDEN_TOKENS:
        assert token not in code, f"paper_contract must not use {token}"
