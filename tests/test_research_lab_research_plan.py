# -*- coding: utf-8 -*-

from scripts.strategy_lab.enqueue_research_plan import apply_plan, plan_spec_dir
from src.research_lab.research_plan import build_research_plan
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.timeframes import load_timeframe_profiles
from src.research_lab.universe import load_universe

GLOB = "data/{symbol}_*.json"


def _inventory(usable: list[str]) -> dict:
    return {"rows": [{"symbol": s, "quality_status": "usable"} for s in usable]}


def _ctx():
    return load_universe(), load_timeframe_profiles(), load_resource_policy()


def test_dry_run_plan_selects_capped_symbols_and_families():
    universe, profiles, policy = _ctx()
    inv = _inventory(["BTC_USDT_SWAP", "ETH_USDT_SWAP", "SOL_USDT_SWAP", "BNB_USDT_SWAP", "XRP_USDT_SWAP"])
    plan = build_research_plan(
        universe, profiles, policy,
        group="core_market", timeframe="1d", data_glob=GLOB, inventory=inv, smoke=True,
    )
    assert len(plan.jobs) == 1
    job = plan.jobs[0]
    assert len(job.symbols) == 3  # smoke cap
    assert len(job.families) == 3
    assert job.role == "regime"
    assert job.variant_count == 9
    assert job.spec["plan_meta"]["timeframe_role"] == "regime"
    assert "BTC_USDT_SWAP" in job.spec["plan_meta"]["relation_hints"]


def test_missing_data_is_reported_not_crashed():
    universe, profiles, policy = _ctx()
    inv = _inventory(["BTC_USDT_SWAP", "ETH_USDT_SWAP"])  # SOL/BNB/XRP missing
    plan = build_research_plan(
        universe, profiles, policy,
        group="core_market", timeframe="1d", data_glob=GLOB, inventory=inv, smoke=True,
    )
    skipped = {s.symbol: s.reason for s in plan.skipped}
    assert skipped.get("SOL_USDT_SWAP") == "no_usable_data"
    assert plan.jobs[0].symbols == ("BTC_USDT_SWAP", "ETH_USDT_SWAP")


def test_1m_has_no_compatible_family():
    universe, profiles, policy = _ctx()
    plan = build_research_plan(
        universe, profiles, policy,
        group="core_market", timeframe="1m", data_glob=GLOB, inventory=None, smoke=True,
    )
    assert plan.jobs == []
    assert all(s.reason == "no_timeframe_compatible_family" for s in plan.skipped)


def test_unknown_group_raises():
    universe, profiles, policy = _ctx()
    try:
        build_research_plan(universe, profiles, policy, group="ghost", timeframe="1d", data_glob=GLOB)
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown group")


def test_apply_is_idempotent(tmp_path):
    universe, profiles, policy = _ctx()
    inv = _inventory(["BTC_USDT_SWAP", "ETH_USDT_SWAP", "SOL_USDT_SWAP"])
    plan = build_research_plan(
        universe, profiles, policy,
        group="core_market", timeframe="1d", data_glob=GLOB, inventory=inv, smoke=True,
    )
    first = apply_plan(plan, tmp_path, priority=80, allow_public_output=False)
    second = apply_plan(plan, tmp_path, priority=80, allow_public_output=False)
    assert first["queued"] == 1
    assert second["queued"] == 0
    assert second["already_pending"] == 1
    # spec file written under the private root, deterministic name
    specs = list(plan_spec_dir(tmp_path).glob("*.json"))
    assert len(specs) == 1
