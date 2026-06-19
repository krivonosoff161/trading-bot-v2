# -*- coding: utf-8 -*-
"""Coordinator cycle: planning, OI block/unblock, anti-saturation pivot (no real compute)."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.farm_coordinator import run_coordinator_cycle  # noqa: E402
from src.research_lab.farm_tasks_db import FarmTasksDB  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402

PROFILES = load_timeframe_profiles()
POLICY = load_resource_policy()


def _usable_state(*, oi=False, enrichment=()):
    return _fixed_state("usable", rows=200, fingerprint="fp1", enrichment=enrichment, oi=oi)


def _fixed_state(status, *, rows=0, fingerprint=None, enrichment=(), oi=False):
    def _fn(_s, _t):
        return {"status": status, "rows": rows, "fingerprint": fingerprint,
                "enrichment": enrichment, "oi_available": oi}
    return _fn


def _event(symbol="BTC-USDT-SWAP", asset_class="crypto_major", source="okx_announcement", reason="listing"):
    return {"event_id": f"{symbol}:{source}:{reason}", "symbol": symbol, "source": source,
            "reason": reason, "observed_at": 1000.0, "priority": 2, "asset_class": asset_class,
            "suggested_timeframes": ["1h"], "evidence": {}, "raw_ref": {}}


def test_plan_creates_run_sweep_when_data_ready(tmp_path):
    tasks = FarmTasksDB(":memory:")
    out = run_coordinator_cycle(tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
                                intake_events=[_event()], data_state_fn=_usable_state(),
                                apply=False, now=1000.0)
    assert out["counters"]["planned_run_sweep"] >= 1
    assert len(tasks.tasks_in_state("queued", task_type="run_sweep")) >= 1
    # event was consumed -> not replanned next cycle
    assert tasks.status_counts()["intake_unconsumed"] == 0
    tasks.close()


def test_plan_missing_data_creates_prepare(tmp_path):
    tasks = FarmTasksDB(":memory:")
    run_coordinator_cycle(tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
                          intake_events=[_event()], data_state_fn=_fixed_state("missing"),
                          apply=False, now=1000.0)
    prepares = tasks.tasks_in_state("queued", task_type="prepare_data")
    assert prepares and prepares[0]["machine_reason"] == "data_missing"
    tasks.close()


def test_too_short_defers_not_every_cycle(tmp_path):
    tasks = FarmTasksDB(":memory:")
    run_coordinator_cycle(tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
                          intake_events=[_event()], data_state_fn=_fixed_state("too_short", rows=30),
                          apply=False, now=1000.0)
    deferred = tasks.tasks_in_state("deferred", task_type="prepare_data")
    assert deferred and deferred[0]["deferred_until"] > 1000.0
    tasks.close()


def test_oi_family_blocks_then_unblocks_when_slot_appears(tmp_path):
    tasks = FarmTasksDB(":memory:")
    run_coordinator_cycle(tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
                          intake_events=[_event()], families=("oi_price_quadrant",),
                          data_state_fn=_usable_state(oi=False), apply=False, now=1000.0)
    blocked = tasks.tasks_in_state("blocked", task_type="run_sweep")
    assert blocked and all(b["machine_reason"] == "NEEDS_OI_DATA" for b in blocked)
    # OI slot appears -> next cycle unblocks the SAME tasks (no duplicates)
    out = run_coordinator_cycle(tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
                                intake_events=[], families=("oi_price_quadrant",),
                                data_state_fn=_usable_state(oi=True), apply=False, now=2000.0)
    assert out["counters"]["unblocked"] == len(blocked)
    assert not tasks.tasks_in_state("blocked", task_type="run_sweep")
    assert len(tasks.tasks_in_state("queued", task_type="run_sweep")) == len(blocked)
    tasks.close()


def test_pivot_blocked_when_no_work(tmp_path):
    tasks = FarmTasksDB(":memory:")
    out = run_coordinator_cycle(tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
                                intake_events=[], data_state_fn=_usable_state(), apply=False, now=1000.0)
    assert out["pivot"] == "blocked:no_eligible_tasks"
    tasks.close()


def test_pivot_discovery_refill_when_idle(tmp_path):
    tasks = FarmTasksDB(":memory:")
    snapshot = {"instruments": {"NEW_USDT_SWAP": {"group": "crypto_alt", "inst_id": "NEW-USDT-SWAP"}}}
    out = run_coordinator_cycle(tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
                                intake_events=[], data_state_fn=_fixed_state("missing"), apply=False,
                                now=1000.0, discovery_snapshot=snapshot)
    assert out["pivot"] == "discovery_refill"
    assert tasks.tasks_in_state("queued", task_type="prepare_data")  # discovery created real work
    tasks.close()


def test_run_sweep_rearm_on_new_fingerprint(tmp_path):
    tasks = FarmTasksDB(":memory:")
    run_coordinator_cycle(tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
                          intake_events=[_event()], families=("momentum_breakout",),
                          data_state_fn=_usable_state(), apply=False, now=1000.0)
    first = {t["task_key"] for t in tasks.tasks_in_state("queued", task_type="run_sweep")}
    # same data fingerprint, fresh event window -> deduped (no new run_sweep)
    run_coordinator_cycle(tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
                          intake_events=[_event(reason="listing2")], families=("momentum_breakout",),
                          data_state_fn=_usable_state(), apply=False, now=1000.0)
    assert {t["task_key"] for t in tasks.tasks_in_state("queued", task_type="run_sweep")} == first
    # NEW fingerprint (fresh candles) -> re-armed new run_sweep task key
    run_coordinator_cycle(tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
                          intake_events=[_event(reason="listing3")], families=("momentum_breakout",),
                          data_state_fn=_fixed_state("usable", rows=260, fingerprint="fp2"),
                          apply=False, now=1000.0)
    keys = {t["task_key"] for t in tasks.tasks_in_state("queued", task_type="run_sweep")}
    assert any("fp2" in k for k in keys) and len(keys) > len(first)
    tasks.close()


def test_feedback_followup_becomes_typed_run_sweep_task(tmp_path):
    from src.research_lab.validation_feedback import generate_feedback, write_feedback
    from src.research_lab.hard_validation_contract import HardValidationReport

    tasks = FarmTasksDB(":memory:")
    tasks.upsert_unique_candidate({
        "uc_key": "BTC::1h::momentum_breakout::ph::fp",
        "symbol": "BTC", "timeframe": "1h", "family": "momentum_breakout",
        "params_hash": "ph", "data_fingerprint": "fp", "decision": "OBSERVE",
        "validation_status": "FORWARD_PAPER", "hard_status": "FAILED_FRAGILITY",
        "candidate_id": "c-follow",
        "params": {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16},
    }, now=1.0)
    report = HardValidationReport(
        candidate_id="c-follow", source_run_id="run", symbol="BTC", timeframe="1h",
        strategy_id="momentum_breakout",
        verdict={
            "candidate_id": "c-follow", "hard_status": "FAILED_FRAGILITY",
            "checks": [], "failed_checks": ["robustness"], "reason_codes": [],
        },
        checks_summary={},
    )
    fb = generate_feedback(report)
    assert fb is not None
    write_feedback(tmp_path, fb, dry_run=False)

    out = run_coordinator_cycle(
        tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
        intake_events=[], data_state_fn=_usable_state(), apply=True, now=1000.0,
        run_worker=False, run_validation=False, run_followups=True, max_followups=5,
    )
    assert out["counters"]["followups_scheduled"] == 1
    assert out["counters"]["followup_sweeps_planned"] == 1
    queued = tasks.tasks_in_state("queued", task_type="run_sweep")
    assert len(queued) == 1
    assert queued[0]["task_key"].startswith("run_sweep::followup::")
    assert '"origin": "feedback_followup"' in queued[0]["payload_json"]
    tasks.close()
