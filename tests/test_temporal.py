# -*- coding: utf-8 -*-
"""
test_temporal.py — tests for deterministic temporal classifier.

Validates:
  - Liquidation flow is always COINCIDENT
  - Listings with future markers get FUTURE phase
  - Official listing source preserves LEADING semantics
  - News posts with no markers are AMBIGUOUS
  - Stale items are detected
  - Realized markers override to REALIZED
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.temporal import classify_temporal  # noqa: E402


def test_liquidation_is_coincident():
    """Liquidation flow → COINCIDENT (happening now)."""
    r = classify_temporal("#BTC Liquidated Short $64.6K", source_kind="liquidations")
    assert r["phase"] == "COINCIDENT"
    assert r["is_stale"] is False


def test_liquidation_event_type_also_coincident():
    """Liquidation by event_type → COINCIDENT."""
    r = classify_temporal("some text", event_type="liquidation_flow")
    assert r["phase"] == "COINCIDENT"


def test_listing_official_is_leading():
    """Listing from realized source → LEADING."""
    r = classify_temporal("$ARX listed on Coinbase", source_kind="listing", phase_prior="realized")
    assert r["phase"] == "LEADING"
    assert r["is_stale"] is False


def test_listing_with_future_markers():
    """Listing with 'upcoming' → FUTURE."""
    r = classify_temporal("$XYZ upcoming listing on Binance", source_kind="listing")
    assert r["phase"] == "FUTURE"


def test_listing_with_realized_markers():
    """Listing with 'listed' → REALIZED."""
    r = classify_temporal("$ARX listed on Coinbase", source_kind="listing", phase_prior="mixed")
    assert r["phase"] == "REALIZED"


def test_news_realized():
    """News with realized markers → REALIZED."""
    r = classify_temporal("SEC approved Bitcoin ETF", source_kind="news")
    assert r["phase"] == "REALIZED"


def test_news_future():
    """News with future markers → FUTURE."""
    r = classify_temporal("Bitcoin upgrade scheduled for next week", source_kind="news")
    assert r["phase"] == "FUTURE"


def test_news_context():
    """News with opinion markers → CONTEXT."""
    r = classify_temporal("Bitcoin price analysis: should you buy?", source_kind="news")
    assert r["phase"] == "CONTEXT"


def test_news_ambiguous():
    """News with no markers → AMBIGUOUS."""
    r = classify_temporal("Crypto market update today", source_kind="news")
    assert r["phase"] == "AMBIGUOUS"


def test_stale_marker_detected():
    """Item with 'last week' from non-listing source → STALE."""
    r = classify_temporal("Bitcoin moved last week", source_kind="rss_or_api")
    assert r["phase"] == "STALE"
    assert r["is_stale"] is True


def test_stale_listing_not_dropped():
    """Listing with 'last week' is still relevant (not stale)."""
    r = classify_temporal("$ARX listed last week", source_kind="listing")
    assert r["phase"] == "REALIZED"
    assert r["is_stale"] is False


def test_rss_old_timestamp_stale():
    """RSS item with old timestamp → STALE."""
    r = classify_temporal("Bitcoin news", source_kind="rss_or_api",
                          source_ts="2025-01-01T00:00:00Z")
    assert r["phase"] == "STALE"
    assert r["is_stale"] is True


def test_rss_recent_timestamp_not_stale():
    """RSS item with recent timestamp → not STALE."""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    ts = (now - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = classify_temporal("Bitcoin news", source_kind="rss_or_api", source_ts=ts)
    assert r["is_stale"] is False


def test_result_has_required_fields():
    """Every result has phase, temporal_reason, is_stale."""
    r = classify_temporal("test", source_kind="news")
    assert "phase" in r
    assert "temporal_reason" in r
    assert "is_stale" in r


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except (AssertionError, NameError) as e:
                failed += 1
                print(f"[FAIL] {name}: {e}")
    print(f"\n{'ALL PASSED' if not failed else f'{failed} failed'}")
    sys.exit(1 if failed else 0)
