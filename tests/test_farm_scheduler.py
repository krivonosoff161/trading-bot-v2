# -*- coding: utf-8 -*-
"""Tests for the pure farm scheduler/refill planner + status counters."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import farm_scheduler as FS  # noqa: E402
from src.research_lab.farm_priority import priority_label, priority_value  # noqa: E402


def _watch(symbol, *, inst=None, resolved=True, eligible=True, readiness="usable",
           tf="1h", source="cointelegraph", event_type="etf_flow", created="2026-06-17T00:00:00Z",
           pending_reason=None, watch_id=None, verdict="WATCH"):
    return {
        "watch_id": watch_id or f"watch_{symbol}",
        "created_at": created,
        "scanner": {"verdict": verdict, "event_type": event_type},
        "trigger": {"source": source},
        "asset": {"symbol": symbol, "okx_inst": inst or f"{symbol}-USDT-SWAP", "okx_resolved": resolved},
        "farm": {"eligible": eligible, "pending_reason": pending_reason,
                 "data_readiness_status": readiness, "selected_timeframe": tf},
    }


def test_eligible_watch_is_queued_with_its_timeframe():
    plan = FS.plan_jobs([_watch("RE", tf="15m")])
    assert plan["counters"]["queued"] == 1
    job = plan["jobs"][0]
    assert job.symbol == "RE" and job.timeframe == "15m"
    assert plan["counters"]["resolved"] == 1
    assert plan["counters"]["data_usable"] == 1


def test_unresolved_instrument_is_skipped_missing_instrument():
    plan = FS.plan_jobs([_watch("GEOD", resolved=False, eligible=False,
                                readiness="", pending_reason="no_okx_instrument")])
    assert plan["counters"]["queued"] == 0
    assert plan["counters"]["unresolved"] == 1
    assert plan["counters"]["skipped_missing_instrument"] == 1
    assert plan["skipped"][0]["reason"] == "no_okx_instrument"


def test_too_short_is_skipped_missing_data():
    plan = FS.plan_jobs([_watch("FRESH", eligible=False, readiness="too_short",
                                pending_reason="too_short")])
    assert plan["counters"]["queued"] == 0
    assert plan["counters"]["data_too_short"] == 1
    assert plan["counters"]["skipped_missing_data"] == 1


def test_fresh_listing_pending_counts_recheck_not_queued():
    plan = FS.plan_jobs([_watch("NEW", eligible=False, readiness="fresh_listing_pending",
                                pending_reason="fresh_listing_pending")])
    assert plan["counters"]["pending_recheck"] == 1
    assert plan["counters"]["queued"] == 0


def test_priority_order_go_watch_announcement_mover():
    watches = [
        _watch("AAA", source="cointelegraph", event_type="etf_flow"),
        _watch("BBB", source="okx_announcements", event_type="listing"),
        _watch("CCC", source="okx_market_tape", event_type="market_mover"),
        _watch("DDD", source="cointelegraph", event_type="etf_flow", verdict="GO"),
    ]
    plan = FS.plan_jobs(watches)
    kinds = [j.source_kind for j in plan["jobs"]]
    assert kinds[0] == "scanner_go"
    assert kinds.index("scanner_go") < kinds.index("scanner_watch")
    assert kinds.index("okx_announcement") < kinds.index("okx_market_mover")


def test_priority_labels_are_stable_and_readable():
    assert priority_label(0) == "manual urgent"
    assert priority_label(90) == "background sweep"
    assert priority_value(4) == 90
    assert priority_value(0) == 0


def test_dedup_by_instrument():
    plan = FS.plan_jobs([_watch("RE"), _watch("RE", watch_id="watch_RE_dup")])
    assert plan["counters"]["queued"] == 1


def test_already_pending_and_completed_are_skipped():
    plan = FS.plan_jobs(
        [_watch("RE"), _watch("DOGE")],
        pending_inst={"RE-USDT-SWAP"}, completed_inst={"DOGE-USDT-SWAP"},
    )
    assert plan["counters"]["queued"] == 0
    assert plan["counters"]["already_pending"] == 1
    assert plan["counters"]["already_completed"] == 1


def test_max_jobs_cap():
    watches = [_watch(f"A{i}") for i in range(20)]
    plan = FS.plan_jobs(watches, max_jobs=3)
    assert plan["counters"]["queued"] == 3
    assert any(s["reason"] == "queue_cap_reached" for s in plan["skipped"])


def test_refill_from_universe_when_under_cap():
    plan = FS.plan_jobs([_watch("RE")], max_jobs=5, backlog_symbols=["BTC_USDT_SWAP", "ETH_USDT_SWAP"])
    kinds = [j.source_kind for j in plan["jobs"]]
    assert "universe_backlog" in kinds
    assert plan["counters"]["queued"] == 3


def test_refill_skips_already_seen_symbol():
    plan = FS.plan_jobs([_watch("BTC", inst="BTC-USDT-SWAP")], max_jobs=5,
                        backlog_symbols=["BTC-USDT-SWAP"])
    # BTC already queued from the watch; backlog must not duplicate it.
    assert plan["counters"]["queued"] == 1
