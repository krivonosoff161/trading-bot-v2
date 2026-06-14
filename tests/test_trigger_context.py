# -*- coding: utf-8 -*-
"""
test_trigger_context.py — tests for trigger context builder.

Validates:
  - Empty symbol returns empty context
  - Tokenized equity requires exchange confirmation
  - Liquidation flow uses flow confirmation model (not news corroboration)
  - Unknown asset requires asset identification
  - Flow context is self-confirming if parseable
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.trigger_context import build_context, _is_within_hours, _missing_context  # noqa: E402


def test_empty_symbol_returns_empty_context():
    """Empty symbol → context_found=False with symbol_required."""
    ctx = build_context("")
    assert ctx["context_found"] is False
    assert "symbol_required" in ctx["context_missing"]


def test_tokenized_equity_needs_exchange_confirmation():
    """Tokenized equity with no matching sources → missing exchange_official_confirmation."""
    missing = _missing_context("SPCXX", "tokenized_equity", set(), [])
    assert "exchange_official_confirmation" in missing


def test_tokenized_equity_with_official_source_has_fewer_gaps():
    """Tokenized equity with okx_listings source → no exchange_official_confirmation gap."""
    missing = _missing_context("SPCXX", "tokenized_equity", {"okx_listings"}, [])
    assert "exchange_official_confirmation" not in missing


def test_liquidation_flow_uses_flow_model():
    """Liquidation flow with valid flow_context → context_found=True (flow is self-confirming)."""
    flow = {"direction_hint": "short", "notional_usd": 64600, "entry_price": 80.77, "venue": "hyperlens"}
    ctx = build_context("BTC", "#BTC Liquidated Short $64.6K at $80.77",
                        "liquidation_flow", "tg_hyperliquid_liquidations",
                        flow_context=flow)
    assert ctx["context_found"] is True
    assert ctx["context_model"] == "flow_confirmation"
    assert ctx["flow_context"]["direction_hint"] == "short"
    assert ctx["flow_context"]["notional_usd"] == 64600


def test_liquidation_flow_unparseable_still_works():
    """Liquidation flow with no parseable data → context_found=False, missing unparseable."""
    ctx = build_context("BTC", "some weird text",
                        "liquidation_flow", "tg_hyperliquid_liquidations")
    assert ctx["context_found"] is False
    assert "unparseable_flow_data" in ctx["context_missing"]
    assert ctx["context_model"] == "flow_confirmation"


def test_liquidation_flow_direction_only_is_enough():
    """Liquidation flow with only direction parsed → context_found=True."""
    flow = {"direction_hint": "long", "notional_usd": None, "entry_price": None, "venue": None}
    ctx = build_context("TRUMP", "#TRUMP Liquidated Long",
                        "liquidation_flow", "tg_hyperliquid_liquidations",
                        flow_context=flow)
    assert ctx["context_found"] is True


def test_unknown_needs_asset_identification():
    """Unknown asset with < 2 mentions → missing asset_identification."""
    missing = _missing_context("ZZZZ", "unknown", set(), [])
    assert "asset_identification" in missing


def test_is_within_hours_recent():
    """Recent timestamp is within hours."""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    ts = (now - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _is_within_hours(ts, 48) is True


def test_is_within_hours_old():
    """Old timestamp is not within hours."""
    assert _is_within_hours("2020-01-01T00:00:00Z", 48) is False


def test_is_within_hours_none():
    """None timestamp → False."""
    assert _is_within_hours(None, 48) is False


def test_build_context_no_buffer_still_works():
    """build_context works even when news_buffer is not available."""
    ctx = build_context("SPCXX", "test", "tokenized_equity", "test_source")
    assert "context_found" in ctx
    assert "context_missing" in ctx
    assert "matching_headlines" in ctx
    assert "source_ids" in ctx
    assert "official_confirmation" in ctx
    assert "context_summary" in ctx
    assert "context_model" in ctx


def test_context_missing_all_required_fields():
    """Every context result has all required fields."""
    ctx = build_context("BTC", "Bitcoin rally", "crypto_major", "cointelegraph")
    required = [
        "context_found", "context_missing", "matching_headlines",
        "source_ids", "official_confirmation", "context_summary",
        "context_model", "flow_context",
    ]
    for field in required:
        assert field in ctx, f"missing field: {field}"


def test_flow_context_included_in_result():
    """Flow context is passed through to the result."""
    flow = {"direction_hint": "short", "notional_usd": 1200000}
    ctx = build_context("ETH", "#ETH Liquidated Short $1.2M",
                        "liquidation_flow", "tg_hyperliquid_liquidations",
                        flow_context=flow)
    assert ctx["flow_context"] == flow


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except AssertionError as e:
                failed += 1
                print(f"[FAIL] {name}: {e}")
    print(f"\n{'ALL PASSED' if not failed else f'{failed} failed'}")
    sys.exit(1 if failed else 0)
