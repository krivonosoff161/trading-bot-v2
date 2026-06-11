from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from src.strategy.setup_confirmation import confirm_setup


def _watch(side: str = "buy", expires_at: str = "2026-06-12T10:00:00Z") -> dict:
    return {
        "watch_id": "watch_abc",
        "expires_at": expires_at,
        "scanner": {"normalized_side": side, "side": side},
        "asset": {"symbol": "ZEC", "okx_inst": "ZEC-USDT-SWAP"},
    }


def _signal(entry_signal: str, side: str | None = "buy") -> SimpleNamespace:
    return SimpleNamespace(
        symbol="ZEC-USDT",
        captured_at="2026-06-11T12:00:00Z",
        entry_signal=entry_signal,
        side=side,
        trade_style="FAST",
        regime="TRENDING",
        entry_price=42.0,
        sl_price=40.0,
        tp1_price=46.0,
        tp2_price=50.0,
        max_hold_min=180,
        drop_reason="",
        engine_summary="synthetic",
    )


def test_confirm_setup_trade_plan_ready_is_paper_only():
    result = confirm_setup(_watch("buy"), _signal("ENTRY", "buy"))

    assert result.status == "TRADE_PLAN_READY"
    assert result.execution_allowed is False
    assert result.trade_plan["paper_only"] is True
    assert result.trade_plan["entry_price"] == 42.0
    assert result.reason == "event_side_confirmed_by_entry_signal"


def test_confirm_setup_invalidates_opposite_entry():
    result = confirm_setup(_watch("buy"), _signal("ENTRY", "sell"))

    assert result.status == "INVALIDATED"
    assert result.reason == "technical_side_opposes_event_side"


def test_confirm_setup_wait_or_neutral_is_setup_forming():
    wait = confirm_setup(_watch("buy"), _signal("WAIT", "buy"))
    neutral = confirm_setup(_watch("none"), _signal("ENTRY", "buy"))

    assert wait.status == "SETUP_FORMING"
    assert neutral.status == "SETUP_FORMING"


def test_confirm_setup_missing_data_and_expired():
    now = dt.datetime(2026, 6, 11, 12, 0, tzinfo=dt.timezone.utc)
    missing = confirm_setup(_watch("buy"), None, now_utc=now)
    expired = confirm_setup(_watch("buy", expires_at="2026-06-10T10:00:00Z"), _signal("ENTRY", "buy"), now_utc=now)

    assert missing.status == "NEEDS_DATA"
    assert expired.status == "EXPIRED"
