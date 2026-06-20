# -*- coding: utf-8 -*-
"""Setup Outcome Memory: outcome-class unification, the read-through gate (so a repeated
signal consults prior outcomes before compute), derived rebuild, and the invariant that no
research outcome is ever paper-forward-ready. Deterministic — no real sweep/sim/money path."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.data_planner import eligible_farm_timeframes  # noqa: E402
from src.research_lab.farm_coordinator import run_coordinator_cycle  # noqa: E402
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.setup_outcome_memory import (  # noqa: E402
    OUTCOME_CLASSES,
    build_gate_index,
    build_memory_index,
    confirmed_bad_setups,
    derive_outcome_class,
    lookup,
    positive_setups,
    rejected_research,
    summarize_memory,
    tactical_setups,
)
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402

PROFILES = load_timeframe_profiles()
POLICY = load_resource_policy()


def _usable_state(*, fingerprint="fp1"):
    def _fn(_s, _t):
        return {"status": "usable", "rows": 200, "fingerprint": fingerprint,
                "enrichment": (), "oi_available": False}
    return _fn


def _event(symbol="BTC-USDT-SWAP", asset_class="crypto_major", reason="listing"):
    return {"event_id": f"{symbol}:okx:{reason}", "symbol": symbol, "source": "okx",
            "reason": reason, "observed_at": 1000.0, "priority": 2, "asset_class": asset_class,
            "suggested_timeframes": ["1h"], "evidence": {}, "raw_ref": {}}


def _cand(symbol, tf, family, fp, *, n_trades=0, avg=0.0, lite="REJECT", hard="", decision="REJECT"):
    return {"symbol": symbol, "timeframe": tf, "family": family, "params_hash": "ph",
            "data_fingerprint": fp, "decision": decision, "validation_status": lite,
            "hard_status": hard, "n_trades": n_trades, "avg_net_pct": avg, "regime_bucket": ""}


class TestOutcomeClass:
    def test_positive_wins_over_everything(self):
        assert derive_outcome_class("HARD_PASSED", "wrong_exit", True) == "POSITIVE_VALIDATED"
        assert derive_outcome_class("PAPER_POSITIVE_OBSERVED", "", False) == "POSITIVE_VALIDATED"

    def test_hard_pass_is_positive_even_if_paper_negative(self):
        # A validated setup that later went paper-negative stays POSITIVE_VALIDATED (it cleared
        # the validator); the negative paper sign lives in lifecycle_state, not the outcome class.
        assert derive_outcome_class(
            "PAPER_NEGATIVE_OBSERVED", "", False, "PAPER_FORWARD_READY") == "POSITIVE_VALIDATED"

    def test_recovered_over_subreason(self):
        assert derive_outcome_class("HARD_FAILED", "wrong_exit", True) == "EXIT_RECOVERED"

    def test_eligible_is_statistical_candidate(self):
        assert derive_outcome_class("HARD_ELIGIBLE", "", False) == "STATISTICAL_CANDIDATE"

    def test_subreason_table(self):
        table = {
            "tactical_candidate": "TACTICAL_1_2_TRADE",
            "validator_too_strict": "THIN_BUT_PROMISING",
            "wrong_exit": "WRONG_EXIT",
            "wrong_timeframe": "WRONG_TIMEFRAME",
            "wrong_costs": "COST_SENSITIVE",
            "missing_oi_micro": "NEEDS_OI_DATA",
            "confirmed_bad": "CONFIRMED_BAD",
            "insufficient_data": "INSUFFICIENT_DATA",
            "uncharacterized": "UNCHARACTERIZED",
        }
        for sub, cls in table.items():
            assert derive_outcome_class("LITE_REJECTED", sub, False) == cls
            assert cls in OUTCOME_CLASSES


class TestGate:
    def test_skip_known_bad_on_identical_confirmed_bad(self):
        idx = build_gate_index([_cand("BTC_USDT_SWAP", "1h", "momentum_breakout", "fp1",
                                       n_trades=20, avg=-0.5)])
        v = lookup(idx, symbol="BTC_USDT_SWAP", timeframe="1h", family="momentum_breakout",
                   data_fingerprint="fp1")
        assert v.action == "skip_known_bad"
        assert v.matched == "exact" and v.reason == "confirmed_bad_identical_data"

    def test_skip_known_bad_on_identical_no_eligible_even_thin(self):
        idx = build_gate_index([_cand("X", "15m", "bb_volume_fade", "fp1", n_trades=2, avg=0.5)])
        v = lookup(idx, symbol="X", timeframe="15m", family="bb_volume_fade", data_fingerprint="fp1")
        assert v.action == "skip_known_bad" and v.reason == "no_eligible_identical_data"

    def test_revisit_when_identical_has_eligible(self):
        idx = build_gate_index([_cand("X", "1h", "momentum_breakout", "fp1",
                                       n_trades=12, avg=0.4, lite="FORWARD_PAPER", decision="OBSERVE")])
        v = lookup(idx, symbol="X", timeframe="1h", family="momentum_breakout", data_fingerprint="fp1")
        assert v.action == "revisit" and v.reason == "prior_eligible_on_identical_data"

    def test_deprioritize_cell_all_rejected_on_other_data(self):
        rows = [_cand("X", "1h", "momentum_breakout", f"fp{i}", n_trades=20, avg=-0.3) for i in range(6)]
        idx = build_gate_index(rows)
        v = lookup(idx, symbol="X", timeframe="1h", family="momentum_breakout", data_fingerprint="NEW")
        assert v.action == "deprioritize" and v.matched == "cell"

    def test_fresh_when_unseen(self):
        idx = build_gate_index([_cand("X", "1h", "momentum_breakout", "fp1", n_trades=20, avg=-0.3)])
        v = lookup(idx, symbol="OTHER", timeframe="1h", family="momentum_breakout", data_fingerprint="z")
        assert v.action == "fresh"

    def test_cell_with_eligible_revisits_not_deprioritizes(self):
        rows = [_cand("X", "1h", "momentum_breakout", f"fp{i}", n_trades=20, avg=-0.3) for i in range(6)]
        rows.append(_cand("X", "1h", "momentum_breakout", "fpE", n_trades=12, avg=0.4,
                          lite="FORWARD_PAPER", decision="OBSERVE"))
        idx = build_gate_index(rows)
        v = lookup(idx, symbol="X", timeframe="1h", family="momentum_breakout", data_fingerprint="NEW")
        assert v.action == "revisit"


class TestViews:
    def _records(self):
        return [
            {"outcome_class": "POSITIVE_VALIDATED", "paper_forward_ready": True,
             "hard_status": "PAPER_FORWARD_READY"},
            {"outcome_class": "EXIT_RECOVERED", "paper_forward_ready": False, "hard_status": ""},
            {"outcome_class": "TACTICAL_1_2_TRADE", "paper_forward_ready": False, "hard_status": ""},
            {"outcome_class": "THIN_BUT_PROMISING", "paper_forward_ready": False, "hard_status": ""},
            {"outcome_class": "WRONG_EXIT", "paper_forward_ready": False, "hard_status": ""},
            {"outcome_class": "CONFIRMED_BAD", "paper_forward_ready": False, "hard_status": ""},
        ]

    def test_views_partition(self):
        recs = self._records()
        assert len(positive_setups(recs)) == 1
        assert len(confirmed_bad_setups(recs)) == 1
        assert len(tactical_setups(recs)) == 2
        assert {r["outcome_class"] for r in rejected_research(recs)} == {
            "TACTICAL_1_2_TRADE", "THIN_BUT_PROMISING", "WRONG_EXIT"}

    def test_summary_invariant_paper_ready_only_behind_hard_pass(self):
        s = summarize_memory(self._records())
        assert s["positive"] == 1 and s["confirmed_bad"] == 1
        assert s["paper_ready_without_hard_pass"] == 0

    def test_invariant_flags_a_violation(self):
        # paper_forward_ready True but NO hard PAPER_FORWARD_READY verdict -> a real leak.
        bad = [{"outcome_class": "WRONG_EXIT", "paper_forward_ready": True, "hard_status": ""}]
        assert summarize_memory(bad)["paper_ready_without_hard_pass"] == 1


class TestBuildMemoryIndex:
    def test_joins_lifecycle_and_subreason(self, tmp_path):
        db = FarmTasksDB(tasks_db_path(tmp_path))
        db.upsert_unique_candidate({
            "uc_key": "X::1h::momentum_breakout::ph::fp", "symbol": "X", "timeframe": "1h",
            "family": "momentum_breakout", "params_hash": "ph", "data_fingerprint": "fp",
            "decision": "REJECT", "validation_status": "REJECT", "hard_status": "",
            "n_trades": 20, "avg_net_pct": -0.5, "candidate_id": "c1", "params": {}}, now=1.0)
        db.close()
        recs = build_memory_index(tmp_path)
        assert len(recs) == 1
        r = recs[0]
        assert r["outcome_class"] == "CONFIRMED_BAD"
        assert r["okx_inst"] == "X"  # symbol has no underscores -> unchanged
        assert r["paper_forward_ready"] is False
        assert summarize_memory(recs)["paper_ready_without_hard_pass"] == 0


class TestCoordinatorGateUsedNextCycle:
    """Acceptance: a confirmed-bad prior makes the NEXT planning cycle skip the re-sweep."""

    def test_known_bad_is_skipped_next_cycle(self, tmp_path):
        tasks = FarmTasksDB(":memory:")
        tfs = eligible_farm_timeframes("crypto_major")
        for tf in tfs:  # seed a confirmed-bad prior on the exact fingerprint for every eligible tf
            tasks.upsert_unique_candidate({
                "uc_key": f"BTC_USDT_SWAP::{tf}::momentum_breakout::ph::fp1",
                "symbol": "BTC_USDT_SWAP", "timeframe": tf, "family": "momentum_breakout",
                "params_hash": "ph", "data_fingerprint": "fp1", "decision": "REJECT",
                "validation_status": "REJECT", "hard_status": "", "n_trades": 20,
                "avg_net_pct": -0.5, "candidate_id": f"c-{tf}", "params": {}}, now=1.0)
        out = run_coordinator_cycle(
            tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
            intake_events=[_event()], families=("momentum_breakout",),
            data_state_fn=_usable_state(fingerprint="fp1"), apply=False, now=1000.0)
        assert out["counters"]["sweeps_skipped_memory"] == len(tfs)
        assert out["counters"]["planned_run_sweep"] == 0
        assert not tasks.tasks_in_state("queued", task_type="run_sweep")
        tasks.close()

    def test_memory_off_still_sweeps(self, tmp_path):
        tasks = FarmTasksDB(":memory:")
        for tf in eligible_farm_timeframes("crypto_major"):
            tasks.upsert_unique_candidate({
                "uc_key": f"BTC_USDT_SWAP::{tf}::momentum_breakout::ph::fp1",
                "symbol": "BTC_USDT_SWAP", "timeframe": tf, "family": "momentum_breakout",
                "params_hash": "ph", "data_fingerprint": "fp1", "decision": "REJECT",
                "validation_status": "REJECT", "hard_status": "", "n_trades": 20,
                "avg_net_pct": -0.5, "candidate_id": f"c-{tf}", "params": {}}, now=1.0)
        out = run_coordinator_cycle(
            tasks, private_root=tmp_path, profiles=PROFILES, policy=POLICY,
            intake_events=[_event()], families=("momentum_breakout",),
            data_state_fn=_usable_state(fingerprint="fp1"), apply=False, now=1000.0,
            use_outcome_memory=False)
        assert out["counters"]["sweeps_skipped_memory"] == 0
        assert out["counters"]["planned_run_sweep"] >= 1
        tasks.close()
