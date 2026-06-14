# -*- coding: utf-8 -*-
"""
test_trigger_policy.py — tests for channel-specific trigger policy.

Validates:
  - Each of three channels resolves to expected policy
  - Unknown channel gets conservative fallback
  - NewListingsFeed unknown ticker can be L2 listing
  - markettwits unknown ticker stays unknown/needs_context
  - Hyperliquid liquidation is flow_trigger, not ordinary GO signal
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.trigger_policy import (  # noqa: E402
    get_policy,
    get_context_profile,
    should_require_context,
)


def test_new_listings_feed_policy():
    """tg_new_listings_feed: listing, signal, unknown→L2, no context required."""
    meta = {"telegram_kind": "listing", "source_class": "telegram_web"}
    p = get_policy("tg_new_listings_feed", meta)
    assert p["channel_kind"] == "listing"
    assert p["default_trigger_role"] == "signal"
    assert p["unknown_ticker_policy"] == "default_to_crypto_alt_l2"
    assert p["requires_context"] is False
    assert p["phase_default"] == "MIXED"  # temporal classifier overrides to LEADING


def test_markettwits_policy():
    """tg_markettwits: news, needs_context, unknown stays unknown."""
    meta = {"telegram_kind": "news", "source_class": "telegram_web"}
    p = get_policy("tg_markettwits", meta)
    assert p["channel_kind"] == "news"
    assert p["default_trigger_role"] == "needs_context"
    assert p["unknown_ticker_policy"] == "needs_context"
    assert p["requires_context"] is True
    assert p["phase_default"] == "AMBIGUOUS"


def test_hyperliquid_liquidations_policy():
    """tg_hyperliquid_liquidations: liquidations, context_trigger, flow_only."""
    meta = {"telegram_kind": "liquidations", "source_class": "telegram_web"}
    p = get_policy("tg_hyperliquid_liquidations", meta)
    assert p["channel_kind"] == "liquidations"
    assert p["default_trigger_role"] == "context_trigger"
    assert p["unknown_ticker_policy"] == "flow_only"
    assert p["requires_context"] is False
    assert p["phase_default"] == "COINCIDENT"
    assert p["flow_fields"] is not None
    assert "direction_hint" in p["flow_fields"]


def test_unknown_channel_gets_default_telegram():
    """Unknown telegram source gets _default_telegram policy."""
    meta = {"telegram_kind": "something_else", "source_class": "telegram_web"}
    p = get_policy("tg_unknown_channel", meta)
    assert p["channel_kind"] == "news"
    assert p["default_trigger_role"] == "needs_context"
    assert p["requires_context"] is True


def test_non_telegram_source_gets_fallback():
    """Non-telegram source (RSS) gets non-telegram fallback."""
    p = get_policy("cointelegraph", {"source_class": "rss"})
    assert p["channel_kind"] == "rss_or_api"
    assert p["default_trigger_role"] == "signal"
    assert p["requires_context"] is False


def test_liquidation_context_profile():
    """Liquidation flow context profile has flow fields, no min_mentions."""
    profile = get_context_profile("liquidation_flow")
    assert profile["min_mentions"] == 0
    assert profile["require_official"] is False
    assert "direction_hint" in profile["flow_context_fields"]


def test_markettwits_context_profile():
    """Markettwits news context profile requires source corroboration."""
    profile = get_context_profile("markettwits_news")
    assert profile["min_mentions"] >= 1
    assert "source_corroboration" in profile["missing_rules"]


def test_should_require_context_tokenized_always():
    """Tokenized equity always needs context regardless of channel."""
    meta = {"telegram_kind": "listing", "source_class": "telegram_web"}
    assert should_require_context("tg_new_listings_feed", meta, "tokenized_equity") is True


def test_should_require_context_liquidation_never():
    """Liquidation flow never needs context (flow IS the context)."""
    meta = {"telegram_kind": "liquidations", "source_class": "telegram_web"}
    assert should_require_context("tg_hyperliquid_liquidations", meta, "liquidation_flow") is False


def test_hyperliquid_in_flow_sources():
    """tg_hyperliquid_liquidations is in flow_sources for FLOW_SIGNAL escalation."""
    from src.scout.agents import orchestrator
    esc = orchestrator._esc()
    assert "tg_hyperliquid_liquidations" in esc.get("flow_sources", [])


def test_listing_phase_default_is_mixed():
    """Listing channel phase_default is MIXED (temporal classifier overrides)."""
    meta = {"telegram_kind": "listing", "source_class": "telegram_web"}
    p = get_policy("tg_new_listings_feed", meta)
    assert p["phase_default"] == "MIXED"


def test_should_require_context_markettwits_unknown():
    """Markettwits with unknown asset needs context."""
    meta = {"telegram_kind": "news", "source_class": "telegram_web"}
    assert should_require_context("tg_markettwits", meta, "unknown") is True


def test_should_require_context_listing_known_crypto():
    """Listing channel with known crypto does NOT need context."""
    meta = {"telegram_kind": "listing", "source_class": "telegram_web"}
    assert should_require_context("tg_new_listings_feed", meta, "crypto_alt") is False


def test_policy_has_all_required_fields():
    """Every policy result has all required fields."""
    required = [
        "channel_kind", "default_trigger_role", "default_event_type",
        "requires_context", "context_profile", "unknown_ticker_policy",
        "max_items_per_pass", "phase_default", "flow_fields",
    ]
    for source_id, meta in [
        ("tg_new_listings_feed", {"telegram_kind": "listing", "source_class": "telegram_web"}),
        ("tg_markettwits", {"telegram_kind": "news", "source_class": "telegram_web"}),
        ("tg_hyperliquid_liquidations", {"telegram_kind": "liquidations", "source_class": "telegram_web"}),
    ]:
        p = get_policy(source_id, meta)
        for field in required:
            assert field in p, f"{source_id} missing field: {field}"


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
