# -*- coding: utf-8 -*-

from src.research_lab.listing_policy import plan_listing_window

HOUR = 3_600_000


def test_live_young_listing_clamps_start_and_reports_exact_eligibility():
    listed = 100 * HOUR
    result = plan_listing_window(
        "1h", 0, listed + 9 * HOUR,
        minimum_bars=60, list_time_ms=listed, state="live",
    )
    assert result.status == "fresh_listing_pending"
    assert result.start_ts == listed
    assert result.available_bars == 10
    assert result.eligible_at_ms == listed + 59 * HOUR
    assert result.may_fetch is True
    assert result.may_full_validate is False


def test_old_live_listing_keeps_desired_bounded_window():
    result = plan_listing_window(
        "1h", 100 * HOUR, 200 * HOUR,
        minimum_bars=60, list_time_ms=0, state="live",
    )
    assert result.status == "eligible"
    assert result.start_ts == 100 * HOUR
    assert result.available_bars == 101


def test_preopen_and_terminal_states_do_not_fetch():
    preopen = plan_listing_window("15m", 0, HOUR, minimum_bars=60, state="preopen")
    suspended = plan_listing_window("15m", 0, HOUR, minimum_bars=60, state="suspend")
    assert preopen.status == "preopen" and preopen.may_fetch is False
    assert suspended.status == "invalid_instrument" and suspended.may_fetch is False
