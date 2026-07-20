# -*- coding: utf-8 -*-
"""Foundation tests: data fingerprint, farm_tasks lifecycle (re-arm/defer/block), intake dedup."""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import data_fingerprint as DF  # noqa: E402
from src.research_lab.farm_tasks_db import FarmTasksDB  # noqa: E402
from src.research_lab.intake_adapter import (  # noqa: E402
    discovery_intake_events,
    event_id,
    watch_to_intake,
    watches_to_intake,
)


def test_legacy_priority_scale_migrates_once(tmp_path):
    path = tmp_path / "farm_tasks.sqlite"
    db = FarmTasksDB(path)
    db.enqueue_task(task_type="prepare_data", task_key="legacy", priority=1)
    db.upsert_intake_event({"event_id": "legacy-event", "priority": 2})
    db._conn.execute("DELETE FROM farm_meta WHERE key='priority_scale'")
    db._conn.commit()
    db.close()

    migrated = FarmTasksDB(path)
    try:
        task_priority = migrated._conn.execute(
            "SELECT priority FROM tasks WHERE task_key='legacy'"
        ).fetchone()[0]
        event_priority = migrated._conn.execute(
            "SELECT priority FROM intake_events WHERE event_id='legacy-event'"
        ).fetchone()[0]
        assert task_priority == 20
        assert event_priority == 30
    finally:
        migrated.close()


# ── data_fingerprint ────────────────────────────────────────────────────────
def test_fingerprint_stable_and_data_sensitive():
    a = DF.compute_fingerprint("BTC-USDT-SWAP", "1h", 200, 1000, 2000)
    assert a == DF.compute_fingerprint("BTC_USDT_SWAP", "1H", 200, 1000, 2000)  # normalized
    assert a != DF.compute_fingerprint("BTC-USDT-SWAP", "1h", 201, 1000, 2000)  # +1 bar
    assert a != DF.compute_fingerprint("BTC-USDT-SWAP", "1h", 200, 1000, 3000)  # new end_ts
    # enrichment changes meaning -> changes fingerprint
    assert a != DF.compute_fingerprint("BTC-USDT-SWAP", "1h", 200, 1000, 2000, enrichment=("funding",))


def _write_candles(path: Path, n: int, *, funding: bool = False) -> None:
    rows = []
    for i in range(n):
        row = {"ts": 1_700_000_000_000 + i * 3_600_000, "date": "x",
               "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "vol": 5.0}
        if funding:
            row["funding"] = 0.0001
        rows.append(row)
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_fingerprint_for_symbol_reflects_enrichment(tmp_path):
    d = tmp_path / "market_data" / "1h"
    d.mkdir(parents=True)
    f = d / "BTC_USDT_SWAP_1700000000000_1700700000000_1h.json"
    _write_candles(f, 100)
    glob = str(d / "{symbol}_*_*.json")
    fp1 = DF.fingerprint_for_symbol(glob, "BTC_USDT_SWAP", "1h")
    assert fp1 is not None
    _write_candles(f, 100, funding=True)  # enrich in place
    fp2 = DF.fingerprint_for_symbol(glob, "BTC_USDT_SWAP", "1h")
    assert fp2 is not None and fp2 != fp1


# ── farm_tasks lifecycle ─────────────────────────────────────────────────────
def test_enqueue_rearm_dedup_and_ttl():
    db = FarmTasksDB(":memory:", clock=lambda: 1000.0)
    t0 = 1000.0
    tid, created = db.enqueue_task(task_type="run_sweep", task_key="k1", now=t0)
    assert created
    # active duplicate -> not created
    _, again = db.enqueue_task(task_type="run_sweep", task_key="k1", now=t0)
    assert not again
    # complete it, then identical within TTL -> not re-armed
    db.complete_task(tid, now=t0 + 10)
    _, within_ttl = db.enqueue_task(task_type="run_sweep", task_key="k1", now=t0 + 100, ttl_seconds=3600)
    assert not within_ttl
    # past TTL -> re-armed (new task)
    new_id, rearmed = db.enqueue_task(task_type="run_sweep", task_key="k1", now=t0 + 5000, ttl_seconds=3600)
    assert rearmed and new_id != tid
    db.close()


def test_claim_respects_priority_and_deferral():
    db = FarmTasksDB(":memory:", clock=lambda: 1000.0)
    now = 1000.0
    db.enqueue_task(task_type="run_sweep", task_key="low", priority=80, now=now)
    hi, _ = db.enqueue_task(task_type="run_sweep", task_key="high", priority=10, now=now)
    claimed = db.claim_next_task(now=now)
    assert claimed["task_id"] == hi  # lower priority number first
    # defer the remaining; not eligible until its time
    rest = db.tasks_in_state("queued")[0]
    db.defer_task(rest["task_id"], until=now + 500, reason="too_short", now=now)
    assert db.claim_next_task(now=now + 100) is None
    woke = db.claim_next_task(now=now + 600)
    assert woke["task_id"] == rest["task_id"]
    db.close()


def test_active_duplicate_promotes_existing_task_to_urgent():
    db = FarmTasksDB(":memory:")
    now = 1000.0
    background, created = db.enqueue_task(
        task_type="run_sweep", task_key="same-work", priority=90, now=now,
    )
    db.enqueue_task(task_type="run_sweep", task_key="other-work", priority=40, now=now)

    promoted, duplicate = db.enqueue_task(
        task_type="run_sweep", task_key="same-work", priority=0,
        source_event_id="manual-urgent", now=now + 1,
    )

    assert created and not duplicate and promoted == background
    claimed = db.claim_next_task(now=now + 1)
    assert claimed["task_id"] == background
    assert claimed["priority"] == 0
    assert claimed["source_event_id"] == "manual-urgent"
    db.close()


def test_reconcile_orphan_running_requeues_stale():
    db = FarmTasksDB(":memory:", lease_seconds=2, clock=lambda: 1000.0)
    now = 1000.0
    a, _ = db.enqueue_task(task_type="run_sweep", task_key="a", now=now)
    b, _ = db.enqueue_task(task_type="run_sweep", task_key="b", now=now)
    db.claim_next_task(now=now)  # one -> running (stale after a stop)
    db.claim_next_task(now=now)  # two -> running
    assert len(db.tasks_in_state("running")) == 2
    n = db.reconcile_orphan_running(now=now + 5)
    assert n == 2
    assert db.tasks_in_state("running") == []
    assert {t["task_key"] for t in db.tasks_in_state("queued")} == {"a", "b"}
    assert db.reconcile_orphan_running(now=now + 6) == 0  # idempotent: nothing running now
    db.close()


def test_depends_on_gates_claim():
    db = FarmTasksDB(":memory:")
    now = 1000.0
    parent, _ = db.enqueue_task(task_type="prepare_data", task_key="p", now=now)
    child, _ = db.enqueue_task(task_type="run_sweep", task_key="c", depends_on=parent, now=now)
    first = db.claim_next_task(now=now)
    assert first["task_id"] == parent  # child blocked until parent completes
    assert db.claim_next_task(now=now) is None
    db.complete_task(parent, now=now)
    assert db.claim_next_task(now=now)["task_id"] == child
    db.close()


def test_block_and_unblock_keeps_same_task():
    db = FarmTasksDB(":memory:")
    now = 1000.0
    tid, _ = db.enqueue_task(task_type="run_sweep", task_key="oi", family="oi_price_quadrant", now=now)
    db.claim_next_task(now=now)
    db.block_task(tid, "NEEDS_OI_DATA", now=now)
    # blocked holds the active slot -> no duplicate is created
    _, created = db.enqueue_task(task_type="run_sweep", task_key="oi", now=now)
    assert not created
    db.requeue_task(tid, reason="oi_slot_available", now=now)
    assert db.tasks_in_state("queued")[0]["task_id"] == tid
    db.close()


def test_latest_unique_candidate_dedup():
    db = FarmTasksDB(":memory:")
    base = {"symbol": "BTC", "timeframe": "1h", "family": "trend", "params_hash": "ph",
            "decision": "OBSERVE", "n_trades": 5, "avg_net_pct": 0.1}
    # two fingerprints for the same logical candidate -> latest wins, one row returned
    db.upsert_unique_candidate({**base, "uc_key": "BTC::1h::trend::ph::fp1", "data_fingerprint": "fp1"}, now=1.0)
    db.upsert_unique_candidate({**base, "uc_key": "BTC::1h::trend::ph::fp2", "data_fingerprint": "fp2",
                                "decision": "PROMOTE_FOR_PRESSURE_TEST"}, now=2.0)
    latest = db.latest_unique_candidates()
    assert len(latest) == 1
    assert latest[0]["data_fingerprint"] == "fp2"
    assert latest[0]["decision"] == "PROMOTE_FOR_PRESSURE_TEST"
    db.close()


def test_intake_event_upsert_dedup():
    db = FarmTasksDB(":memory:")
    ev = {"event_id": "e1", "symbol": "BTC", "source": "scanner", "reason": "listing",
          "observed_at": 1.0, "priority": 1, "asset_class": "crypto_major",
          "suggested_timeframes": ["1h"], "evidence": {}, "raw_ref": {}}
    _, created = db.upsert_intake_event(ev)
    assert created
    _, dup = db.upsert_intake_event(ev)
    assert not dup
    assert len(db.unconsumed_events()) == 1
    db.mark_event_consumed("e1")
    assert db.unconsumed_events() == []
    db.close()


# ── intake adapter ────────────────────────────────────────────────────────────
def _watch(**over):
    base = {
        "watch_id": "watch_abc", "card_id": "abc", "event_key": "BTC::listing",
        "created_at": "2026-06-18T10:00:00+00:00",
        "scanner": {"event_type": "listing", "escalation_gate": "GO", "materiality_score": 0.8,
                    "agent_confidence": 0.7, "normalized_side": "buy"},
        "asset": {"symbol": "BTC-USDT-SWAP", "okx_inst": "BTC-USDT-SWAP",
                  "okx_asset_class": "crypto_major", "okx_resolved": True},
        "farm": {"eligible": True, "selected_timeframe": "1h", "data_readiness_status": "usable"},
        "trigger": {"source": "okx_announcement", "catalyst": "new listing", "source_url": "http://x"},
    }
    base.update(over)
    return base


def test_watch_to_intake_contract():
    ev = watch_to_intake(_watch())
    assert ev["symbol"] == "BTC-USDT-SWAP"
    assert ev["source"] == "okx_announcement"
    assert ev["asset_class"] == "crypto_major"
    assert ev["suggested_timeframes"] == ["1h"]
    assert ev["priority"] == 30  # official-announcement tier
    assert ev["raw_ref"]["watch_id"] == "watch_abc"
    assert ev["evidence"]["materiality_score"] == 0.8


def test_watch_intake_dedup_same_event_window():
    # same symbol+source+reason in the same 24h window -> one event_id
    a = watch_to_intake(_watch(created_at="2026-06-18T10:00:00+00:00"))
    b = watch_to_intake(_watch(created_at="2026-06-18T12:00:00+00:00"))
    assert a["event_id"] == b["event_id"]
    deduped = watches_to_intake([_watch(), _watch(created_at="2026-06-18T12:00:00+00:00")])
    assert len(deduped) == 1


def test_watch_without_symbol_skipped():
    w = _watch(asset={"symbol": None, "okx_inst": None})
    assert watch_to_intake(w) is None


def test_discovery_events_skip_covered():
    snap = {"instruments": {"BTC_USDT_SWAP": {"group": "crypto_major", "inst_id": "BTC-USDT-SWAP"},
                            "DOGE_USDT_SWAP": {"group": "meme_or_high_beta", "inst_id": "DOGE-USDT-SWAP"}}}
    events = discovery_intake_events(snap, covered={"BTC_USDT_SWAP"})
    syms = {e["symbol"] for e in events}
    assert syms == {"DOGE_USDT_SWAP"}
    assert events[0]["asset_class"] == "meme"  # group->asset_class mapping
    assert events[0]["priority"] == 90  # background sweep tier


def test_discovery_events_limit_zero_returns_no_events():
    snap = {"instruments": {"BTC_USDT_SWAP": {"group": "crypto_major", "inst_id": "BTC-USDT-SWAP"}}}

    assert discovery_intake_events(snap, limit=0) == []


def test_event_id_changes_across_windows():
    assert event_id("BTC", "s", "r", 0.0) != event_id("BTC", "s", "r", 48 * 3600)
