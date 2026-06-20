# -*- coding: utf-8 -*-
"""Universe-driven refill: pure planning + the prepare->plan->queue runner.

The runner is exercised with the offline SyntheticMarketDataProvider and a temp
private root + temp DB, so it never touches the network or the real private data.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.market_data_provider import get_provider
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.state_db import connect, default_db_path, init_db
from src.research_lab.timeframes import load_timeframe_profiles
from src.research_lab.universe import load_universe
from src.research_lab.universe_refill import build_worklist, families_for_group, rotate_worklist
from src.research_lab.universe_refill_runner import RefillState, run_refill_cycle

NOW_MS = 1_750_000_000_000


class _EmptyProvider:
    name = "empty"
    configured = True

    def fetch_ohlcv(self, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        return []


def test_build_worklist_filters_and_orders():
    uni = load_universe()
    units = build_worklist(uni, timeframes=["1h", "1d"])
    assert units, "worklist should not be empty"
    for u in units:
        assert u.families, "every unit must carry timeframe-compatible families"
        assert u.timeframe in ("1h", "1d")
    # 1m would be filtered out entirely (no full-sweep families on the trigger TF)
    assert all(u.timeframe != "1m" for u in build_worklist(uni, timeframes=["1m"]))


def test_rotate_worklist_wraps():
    uni = load_universe()
    units = build_worklist(uni, timeframes=["1h"])
    first, cursor = rotate_worklist(units, 0, 2)
    second, cursor2 = rotate_worklist(units, cursor, 2)
    assert len(first) == 2 and len(second) == 2
    assert cursor == 2 % len(units)
    # full rotation returns to start
    _, back = rotate_worklist(units, 0, len(units))
    assert back == 0


def test_families_for_group_timeframe_filtered():
    fams_1h = families_for_group("meme_flow", "1h")
    assert "pump_dump_scalp" in fams_1h
    # pump_dump_scalp is 15m/1h only -> absent on 1d
    assert "pump_dump_scalp" not in families_for_group("meme_flow", "1d")


def test_oi_families_are_optin_default_off():
    from src.research_lab.universe_refill import OI_OPTIN_FAMILIES
    # default (no opt-in): the continuous loop does NOT plan unproven OI/flow families
    off = families_for_group("core_market", "1h", include_oi=False)
    assert not any(f in OI_OPTIN_FAMILIES for f in off)
    # explicit opt-in research group brings them back
    on = families_for_group("core_market", "1h", include_oi=True)
    assert any(f in OI_OPTIN_FAMILIES for f in on)


def test_runner_prepares_plans_and_queues(tmp_path):
    uni = load_universe()
    profiles = load_timeframe_profiles()
    policy = load_resource_policy()
    provider = get_provider("synthetic", allow_synthetic=True)
    units = build_worklist(uni, groups=["core_market"], timeframes=["1h"])
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    try:
        result = run_refill_cycle(
            units, universe=uni, profiles=profiles, policy=policy, private_root=tmp_path,
            provider=provider, state=RefillState(), apply=True, now_ms=NOW_MS, conn=conn,
            max_prepares=8, full=False, allow_public_output=True,
        )
        counters = result["counters"]
        assert counters["prepared"] > 0, "synthetic provider should prepare candles"
        assert counters["jobs_queued"] > 0, "a multi-family plan should be queued"
        queued = conn.execute("SELECT COUNT(*) AS n FROM queue WHERE status='queued'").fetchone()["n"]
        assert queued > 0
    finally:
        conn.close()
    # spec file actually written under the private root
    specs = list((tmp_path / "plans" / "specs").glob("*.json"))
    assert specs, "a research-plan spec should be written"


def test_runner_backoff_stops_retrying(tmp_path):
    uni = load_universe()
    profiles = load_timeframe_profiles()
    policy = load_resource_policy()
    units = build_worklist(uni, groups=["core_market"], timeframes=["1h"])
    state = RefillState()
    conn = connect(default_db_path(tmp_path))
    init_db(conn)
    try:
        # repeated failing prepares accumulate failures, then back off
        for _ in range(4):
            run_refill_cycle(
                units, universe=uni, profiles=profiles, policy=policy, private_root=tmp_path,
                provider=_EmptyProvider(), state=state, apply=True, now_ms=NOW_MS, conn=conn,
                max_prepares=8, full=False, max_failures=3, allow_public_output=True,
            )
        last = run_refill_cycle(
            units, universe=uni, profiles=profiles, policy=policy, private_root=tmp_path,
            provider=_EmptyProvider(), state=state, apply=True, now_ms=NOW_MS, conn=conn,
            max_prepares=8, full=False, max_failures=3, allow_public_output=True,
        )
    finally:
        conn.close()
    assert last["counters"]["prepare_skipped_backoff"] > 0, "failing symbols must eventually back off"
