# -*- coding: utf-8 -*-
"""
test_trigger_package.py — tests for trigger package builder.

Validates:
  - Package for SPCXX/tokenized equity contains L5 + context required
  - Package for liquidation contains flow_context
  - Package for NewListingsFeed crypto listing contains L2 listing
  - Package for markettwits unknown cashtag is needs_context
  - LLM prompt format is concise
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.trigger_package import build_trigger_package, format_for_llm_prompt  # noqa: E402


def test_spcxx_tokenized_equity_package():
    """SPCXX package has L5, tokenized_equity, requires_context."""
    item = {
        "asset": "SPCXX", "asset_class": "tokenized_equity", "layer": 5,
        "baseline": "QQQ-USDT-SWAP", "trigger_role": "needs_context",
        "event_type": "tokenized_equity_listing", "requires_context": True,
        "identity_reason": "known_tokenized_ticker:SPCXX", "identity_confidence": 0.95,
        "channel_kind": "listing", "source": "tg_new_listings_feed",
    }
    pkg = build_trigger_package(item, headline="$SPCXX listed on Bybit", phase="LEADING")
    assert pkg["asset"] == "SPCXX"
    assert pkg["asset_class"] == "tokenized_equity"
    assert pkg["layer"] == 5
    assert pkg["requires_context"] is True
    assert pkg["trigger_role"] == "needs_context"
    assert pkg["phase"] == "LEADING"


def test_liquidation_package_has_flow_context():
    """Liquidation package includes flow_context dict."""
    item = {
        "asset": "BTC", "asset_class": "liquidation_flow", "layer": 1,
        "trigger_role": "context_trigger", "event_type": "liquidation_flow",
        "channel_kind": "liquidations", "source": "tg_hyperliquid_liquidations",
        "flow_context": {"direction_hint": "short", "notional_usd": 64600, "entry_price": 80.77},
    }
    pkg = build_trigger_package(item, headline="#BTC Liquidated Short $64.6K")
    assert pkg["asset"] == "BTC"
    assert pkg["asset_class"] == "liquidation_flow"
    assert pkg["flow_context"]["direction_hint"] == "short"
    assert pkg["flow_context"]["notional_usd"] == 64600


def test_listing_crypto_package_l2():
    """NewListingsFeed crypto listing package has L2, exchange_listing."""
    item = {
        "asset": "ARX", "asset_class": "crypto_alt", "layer": 2,
        "baseline": "BTC-USDT-SWAP", "trigger_role": "signal",
        "event_type": "exchange_listing", "requires_context": False,
        "channel_kind": "listing", "source": "tg_new_listings_feed",
    }
    pkg = build_trigger_package(item, headline="$ARX added to Coinbase roadmap", phase="LEADING")
    assert pkg["asset"] == "ARX"
    assert pkg["asset_class"] == "crypto_alt"
    assert pkg["layer"] == 2
    assert pkg["trigger_role"] == "signal"
    assert pkg["requires_context"] is False


def test_markettwits_unknown_needs_context():
    """Markettwits unknown cashtag package has needs_context, unknown asset_class."""
    item = {
        "asset": "ZZZZ", "asset_class": "unknown", "layer": None,
        "trigger_role": "needs_context", "event_type": "news_trigger",
        "requires_context": True, "channel_kind": "news",
        "source": "tg_markettwits", "identity_reason": "unknown_ticker_no_entity_match",
    }
    pkg = build_trigger_package(item, headline="$ZZZZ surges on new partnership")
    assert pkg["asset"] == "ZZZZ"
    assert pkg["asset_class"] == "unknown"
    assert pkg["trigger_role"] == "needs_context"
    assert pkg["requires_context"] is True
    assert pkg["layer"] is None


def test_context_found_in_package():
    """Context found status is included in package."""
    item = {"asset": "SPCXX", "asset_class": "tokenized_equity", "layer": 5}
    ctx = {"context_found": True, "context_summary": "2 mentions from 1 source",
           "context_missing": [], "context_model": "news_corroboration"}
    pkg = build_trigger_package(item, trigger_context_pkg=ctx)
    assert pkg["context_found"] is True
    assert pkg["context_summary"] == "2 mentions from 1 source"
    assert pkg["context_model"] == "news_corroboration"


def test_context_not_found_in_package():
    """Context not found is included in package."""
    item = {"asset": "ZZZZ", "asset_class": "unknown"}
    ctx = {"context_found": False, "context_summary": "no mentions",
           "context_missing": ["asset_identification"]}
    pkg = build_trigger_package(item, trigger_context_pkg=ctx)
    assert pkg["context_found"] is False
    assert "asset_identification" in pkg["context_missing"]


def test_context_status_no_buffer():
    """No-buffer status is included in package."""
    item = {"asset": "SPCXX", "asset_class": "tokenized_equity"}
    pkg = build_trigger_package(item, context_status="not_available_no_buffer")
    assert pkg["context_status"] == "not_available_no_buffer"


def test_format_for_llm_prompt():
    """LLM prompt format is concise and contains key fields."""
    pkg = {
        "asset": "BTC", "asset_class": "liquidation_flow", "trigger_role": "context_trigger",
        "channel_kind": "liquidations", "phase": "COINCIDENT",
        "flow_context": {"direction_hint": "short", "notional_usd": 64600, "entry_price": 80.77},
    }
    text = format_for_llm_prompt(pkg)
    assert "BTC" in text
    assert "liquidation_flow" in text
    assert "short" in text
    assert "64600" in text
    assert len(text) < 300


def test_format_for_llm_prompt_minimal():
    """Minimal package still produces readable prompt."""
    pkg = {"asset": "ARX", "asset_class": "crypto_alt"}
    text = format_for_llm_prompt(pkg)
    assert "ARX" in text
    assert "crypto_alt" in text


def test_package_has_all_required_fields():
    """Every package has all required fields."""
    required = [
        "asset", "asset_class", "layer", "baseline", "okx_inst",
        "source_id", "channel_kind", "trigger_role", "event_type",
        "phase", "temporal_reason", "identity_reason", "identity_confidence",
        "requires_context", "context_status", "context_found", "context_missing",
        "context_summary", "context_model", "flow_context",
        "headline", "text_excerpt", "source_ts", "price_at_decision", "url",
    ]
    pkg = build_trigger_package({"asset": "BTC", "asset_class": "crypto_major"})
    for field in required:
        assert field in pkg, f"missing field: {field}"


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
