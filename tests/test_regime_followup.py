# -*- coding: utf-8 -*-
"""Phase 1.3 — revive the failure->sweep loop (REGIME_SWEEP) + bounded depth.

The dominant regime bucket now flows classify_run -> unique_candidates.regime_bucket ->
candidate_context.regime_summary, so a REGIME_SWEEP follow-up actually builds a filter
instead of no-oping on missing_regime_filter. Follow-up depth is capped.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from src.research_lab import feedback_reader as fr
from src.research_lab.farm_classifier import classify_run
from src.research_lab.farm_coordinator import (
    MAX_FOLLOWUP_DEPTH,
    _candidate_context_by_id,
    _drain_followups,
    _sweep_from_payload,
)
from src.research_lab.farm_tasks_db import FarmTasksDB
from src.research_lab.feedback_followup import plan_followup
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.sweep_spec import SweepSpec
from src.research_lab.timeframes import load_timeframe_profiles

PROFILES = load_timeframe_profiles()
POLICY = load_resource_policy()
BUCKET = "high_vol|trending"
PARENT = {
    "search_family_id": "sfd_parent",
    "search_trial_id": "stept_parent",
    "effective_n_trials": 4,
}


def _write_metrics(tmp_path: Path) -> str:
    run_dir = tmp_path / "rundir"
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {"results": [{
        "symbol": "BTC-USDT-SWAP", "family": "momentum_breakout",
        "params": {"lookback": 20, "hold_bars": 5, "stop_pct": 1.0, "take_pct": 2.0},
        "decision": "REGIME_SPECIFIC", "validation_status": "REGIME_SPECIFIC",
        "metrics": {"n_trades": 12, "avg_net_pct": 0.4, "effective_n_trials": 4},
        "search_family_id": "sfd_parent", "search_trial_id": "stept_parent",
        "run_id": "r1",
        "regime_summary": {"dominant_bucket": BUCKET, "bucket_count": 3},
    }]}
    (run_dir / "metrics.json").write_text(json.dumps(payload), encoding="utf-8")
    return "rundir"


def _regime_rec() -> fr.Recommendation:
    return fr.Recommendation(
        action=fr.REGIME_SWEEP, strategy_id="momentum_breakout", symbol="BTC-USDT-SWAP",
        timeframe="1d", reason="regime-only", hard_status="REGIME_ONLY", priority="normal",
        candidate_ids=["r1"], reason_codes=[],
    )


class TestRegimeChain:
    def test_classify_extracts_regime_bucket(self, tmp_path) -> None:
        rows = classify_run(tmp_path, _write_metrics(tmp_path), timeframe="1d", data_fingerprint="fp")
        assert rows and rows[0]["regime_bucket"] == BUCKET

    def test_unique_candidate_roundtrips_bucket(self, tmp_path) -> None:
        tasks = FarmTasksDB(":memory:")
        rows = classify_run(tmp_path, _write_metrics(tmp_path), timeframe="1d", data_fingerprint="fp")
        tasks.upsert_unique_candidate(rows[0], now=1.0)
        latest = tasks.latest_unique_candidates(limit=5)
        tasks.close()
        assert latest[0]["regime_bucket"] == BUCKET

    def test_context_carries_regime_summary(self, tmp_path) -> None:
        tasks = FarmTasksDB(":memory:")
        rows = classify_run(tmp_path, _write_metrics(tmp_path), timeframe="1d", data_fingerprint="fp")
        tasks.upsert_unique_candidate(rows[0], now=1.0)
        ctx = _candidate_context_by_id(tasks)
        tasks.close()
        assert ctx["r1"]["regime_summary"]["dominant_bucket"] == BUCKET


class TestRegimeSweepRevived:
    def test_queues_with_bucket(self) -> None:
        ctx = {"params": {"lookback": 20, "hold_bars": 5, **PARENT},
               "regime_summary": {"dominant_bucket": BUCKET}}
        plan = plan_followup(_regime_rec(), ctx)
        assert plan.queued is True
        assert plan.sweep is not None
        assert plan.sweep.filter_grid  # a real regime filter was built

    def test_noops_without_bucket(self) -> None:
        ctx = {"params": {"lookback": 20, "hold_bars": 5, **PARENT},
               "regime_summary": {"dominant_bucket": ""}}
        plan = plan_followup(_regime_rec(), ctx)
        assert plan.queued is False
        assert plan.not_queued_reason == "missing_regime_filter"


class TestSweepPayloadTier:
    def test_roundtrip_restores_variant_tier(self) -> None:
        spec = SweepSpec(sweep_id="s", anchor_symbol="BTC-USDT-SWAP", related_symbols=(),
                         timeframe="1d", setup_family="momentum_breakout", variant_tier="deep")
        restored = _sweep_from_payload(asdict(spec))
        assert restored.variant_tier == "deep"


class TestDepthCap:
    def test_drain_caps_depth(self, tmp_path: Path) -> None:
        tasks = FarmTasksDB(":memory:")
        rec = _regime_rec()
        tasks.enqueue_task(task_type="schedule_followup", task_key="sf::r1",
                           symbol="BTC-USDT-SWAP", timeframe="1d", family="momentum_breakout",
                           payload={"recommendation": rec.to_dict(),
                                    "followup_depth": MAX_FOLLOWUP_DEPTH}, now=1.0)
        counters: dict[str, int] = {}
        _drain_followups(
            tasks,
            private_root=tmp_path,
            profiles=PROFILES,
            policy=POLICY,
            limit=5,
            counters=counters,
            now=2.0,
        )
        # capped: no run_sweep produced
        assert not tasks.tasks_in_state("queued", task_type="run_sweep")
        assert counters.get("followup_notes", 0) >= 1
        tasks.close()
