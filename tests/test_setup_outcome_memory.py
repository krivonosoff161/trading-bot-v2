# -*- coding: utf-8 -*-
"""Setup Outcome Memory: outcome-class unification, the read-through gate (so a repeated
signal consults prior outcomes before compute), derived rebuild, and the invariant that no
research outcome is ever paper-forward-ready. Deterministic — no real sweep/sim/money path."""
import sys
from pathlib import Path

import pytest

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
    gate_distribution,
    knowledge_base_counts,
    lookup,
    memory_prompt_digest,
    positive_setups,
    prior_for_cell,
    proposal_verdict,
    rejected_research,
    revisit_policy,
    summarize_memory,
    summarize_product_training_memory,
    tactical_setups,
)
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402
from src.research_lab.trade_path_diagnostics import characterize_rejects  # noqa: E402

PROFILES = load_timeframe_profiles()
POLICY = load_resource_policy()


def _bind_current_training_rows(rows):
    items = []
    subject_ids = []
    for index, row in enumerate(rows):
        signal_id = f"signal-v2-{index}"
        subject_id = f"subject-v2-{index}"
        terminal_id = f"terminal-v2-{index}"
        row.update(
            {
                "paper_only": True,
                "execution_allowed": False,
                "immutable_terminal_evidence": True,
                "paper_generation_run_id": "run-v2",
                "paper_subject_generation_id": subject_id,
                "terminal_lifecycle_event_id": terminal_id,
                "account_generation_id": "account-v2",
                "signal_id": signal_id,
            }
        )
        subject_ids.append(subject_id)
        items.append(
            {
                "source_signal_id": signal_id,
                "paper_generation_run_id": "run-v2",
                "paper_subject_generation_id": subject_id,
                "terminal_lifecycle_event_id": terminal_id,
                "account_generation_id": "account-v2",
                "paper_account_decision": "position_closed",
                "okx_inst_id": row.get("okx_inst_id") or row.get("symbol"),
                "timeframe": row.get("timeframe"),
                "setup_family": row.get("family"),
                "side": row.get("side") or "long",
                "boundary_ts": row.get("boundary_ts") or 100,
                "farm_geometry_profile_id": row.get("farm_geometry_profile_id")
                or "",
                "outcome": {"net_pct": row.get("net_pct")},
                "paper_account": {"pnl_usdt": row.get("paper_pnl_usdt")},
            }
        )
        row.setdefault("side", "long")
        row.setdefault("boundary_ts", 100)
    return {
        "current": True,
        "display_only": False,
        "generation_status": "completed",
        "paper_only": True,
        "execution_allowed": False,
        "paper_generation_run_id": "run-v2",
        "account_generation_id": "account-v2",
        "paper_subject_generation_ids": subject_ids,
        "items": items,
    }


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


class TestTacticalLibraryDimensions:
    def test_cost_class_ladder(self):
        from src.research_lab.setup_outcome_memory import cost_class
        assert cost_class(0.5) == "cost_ok"
        assert cost_class(-0.05) == "cost_bound_maker_unlock"   # taker-dead, maker would flip (+0.08)
        assert cost_class(-0.09) == "cost_marginal"             # only zero-cost positive
        assert cost_class(-0.5) == "cost_dead"

    def test_tactical_class_one_shot_vs_statistical(self):
        from src.research_lab.setup_outcome_memory import tactical_class
        assert tactical_class(3, 1.0, "") == "one_shot_candidate"      # thin & positive
        assert tactical_class(20, 1.0, "PAPER_FORWARD_READY") == "statistical_edge_candidate"
        assert tactical_class(20, -1.0, "") == ""                      # powered & negative -> not tactical
        assert tactical_class(3, -1.0, "") == ""                       # thin & negative -> not one-shot

    def test_next_action_routes_rejected_lifecycle(self):
        from src.research_lab.setup_outcome_memory import next_action
        assert next_action("CONFIRMED_BAD", "cost_dead", "") == "known_bad_freeze"
        assert next_action("WRONG_EXIT", "cost_ok", "") == "exit_grid_phase2"
        assert next_action("UNCHARACTERIZED", "cost_bound_maker_unlock", "") == "maker_cost_sensitivity_research"
        assert next_action("THIN_BUT_PROMISING", "cost_ok", "one_shot_candidate") == "shadow_forward_watch"
        assert next_action("NEEDS_OI_DATA", "cost_dead", "") == "await_new_data"


class TestRevisitPolicy:
    def _bad_cell_index(self):
        # one known-bad cell with a tried fingerprint/params and a timestamp
        rows = [_cand("X_USDT_SWAP", "4h", "mean_reversion_fade", "fpOLD", n_trades=20, avg=-0.4)]
        rows[0]["params_hash"] = "phOLD"
        rows[0]["updated_at"] = 1_000_000_000_000  # ms
        return build_gate_index(rows)

    def test_blocks_blind_recompute_of_identical(self):
        prior = prior_for_cell(self._bad_cell_index(), symbol="X_USDT_SWAP", timeframe="4h",
                               family="mean_reversion_fade")
        allowed, triggers = revisit_policy(prior, {"data_fingerprint": "fpOLD", "params_hash": "phOLD"})
        assert allowed is False and triggers == []

    def test_new_data_or_params_or_exit_allows(self):
        prior = prior_for_cell(self._bad_cell_index(), symbol="X_USDT_SWAP", timeframe="4h",
                               family="mean_reversion_fade")
        ok_fp, t1 = revisit_policy(prior, {"data_fingerprint": "fpNEW", "params_hash": "phOLD"})
        ok_ph, t2 = revisit_policy(prior, {"data_fingerprint": "fpOLD", "params_hash": "phNEW"})
        assert ok_fp and "new_data_fingerprint" in t1
        assert ok_ph and "new_params_or_exit" in t2

    def test_ttl_and_manual_go(self):
        prior = prior_for_cell(self._bad_cell_index(), symbol="X_USDT_SWAP", timeframe="4h",
                               family="mean_reversion_fade")
        # same identity, but TTL elapsed (now far beyond newest_ts)
        ok_ttl, t = revisit_policy(prior, {"data_fingerprint": "fpOLD", "params_hash": "phOLD"},
                                   ttl_days=30, now_ts=1_000_000_000_000 + 31 * 86_400_000)
        assert ok_ttl and "ttl_expired" in t
        ok_go, t2 = revisit_policy(prior, {"data_fingerprint": "fpOLD", "params_hash": "phOLD",
                                           "manual_go": True})
        assert ok_go and "manual_go" in t2

    def test_oi_funding_context_trigger(self):
        prior = prior_for_cell(self._bad_cell_index(), symbol="X_USDT_SWAP", timeframe="4h",
                               family="mean_reversion_fade")
        ok, t = revisit_policy({**prior, "had_oi_funding_context": False},
                               {"data_fingerprint": "fpOLD", "params_hash": "phOLD",
                                "oi_funding_context": True})
        assert ok and "new_oi_funding_context" in t


class TestKnowledgeBaseCounts:
    def test_gate_distribution_and_six_counts(self):
        idx = build_gate_index([
            _cand("A_USDT_SWAP", "1h", "momentum_breakout", "fp1", n_trades=20, avg=-0.5),  # known_bad
            _cand("B_USDT_SWAP", "4h", "mean_reversion_fade", "fp2", n_trades=15, avg=0.4,
                  lite="FORWARD_PAPER", decision="OBSERVE"),  # eligible -> revisit
        ])
        dist = gate_distribution(idx)
        assert dist["skip_known_bad"] >= 1 and dist["revisit"] >= 1
        counts = knowledge_base_counts([], idx, survived_shadow=2, tactical_probe=7)
        assert counts["known_bad"] == dist["skip_known_bad"]
        assert counts["revisit"] == dist["revisit"]
        assert counts["survived_shadow"] == 2 and counts["tactical_probe"] == 7
        assert set(counts) == {"known_bad", "revisit", "survived_shadow", "tactical_probe",
                               "rejected_recyclable", "rejected_confirmed_bad"}


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


class TestProposalGate:
    def test_exact_params_confirmed_bad_is_known_bad(self):
        idx = build_gate_index([_cand("X", "1h", "momentum_breakout", "fp1",
                                       n_trades=20, avg=-0.5)])
        # by_params keys on params_hash; reuse the candidate's params_hash by reading the index
        ph = next(iter(idx.by_params)).split("|")[-1]
        v = proposal_verdict(idx, symbol="X", timeframe="1h", family="momentum_breakout", params_hash=ph)
        assert v.action == "known_bad" and v.matched == "params"

    def test_dead_cell_with_power_is_known_bad(self):
        rows = [_cand("X", "1h", "momentum_breakout", f"fp{i}", n_trades=20, avg=-0.3) for i in range(6)]
        idx = build_gate_index(rows)
        v = proposal_verdict(idx, symbol="X", timeframe="1h", family="momentum_breakout", params_hash="NEW")
        assert v.action == "known_bad" and v.matched == "cell"

    def test_eligible_cell_is_revisit(self):
        idx = build_gate_index([_cand("X", "1h", "momentum_breakout", "fpE", n_trades=12, avg=0.4,
                                       lite="FORWARD_PAPER", decision="OBSERVE")])
        v = proposal_verdict(idx, symbol="X", timeframe="1h", family="momentum_breakout", params_hash="NEW")
        assert v.action == "revisit"

    def test_unseen_is_fresh(self):
        idx = build_gate_index([_cand("X", "1h", "momentum_breakout", "fp1", n_trades=20, avg=-0.3)])
        v = proposal_verdict(idx, symbol="OTHER", timeframe="4h", family="bb_volume_fade", params_hash="z")
        assert v.action == "fresh"

    def test_thin_cell_without_power_is_not_known_bad(self):
        # 6 rejects but all thin (n<10) -> no power -> not confidently dead -> allow (fresh)
        rows = [_cand("X", "1h", "momentum_breakout", f"fp{i}", n_trades=2, avg=-0.3) for i in range(6)]
        idx = build_gate_index(rows)
        v = proposal_verdict(idx, symbol="X", timeframe="1h", family="momentum_breakout", params_hash="NEW")
        assert v.action == "fresh"


class TestDigest:
    def test_digest_has_labels(self):
        recs = [{"outcome_class": "CONFIRMED_BAD", "family": "momentum_breakout", "paper_forward_ready": False,
                 "hard_status": ""},
                {"outcome_class": "WRONG_EXIT", "family": "mean_reversion_fade", "paper_forward_ready": False,
                 "hard_status": ""}]
        text = memory_prompt_digest(recs)
        assert "OUTCOME MEMORY DIGEST" in text
        assert "confirmed_bad=1" in text and "wrong_exit=1" in text
        assert "do NOT re-propose" in text

    def test_empty_digest(self):
        assert "empty" in memory_prompt_digest([])


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

    def test_reject_characterization_reports_real_chunks_and_is_cancellable(
        self,
        tmp_path,
    ):
        db = FarmTasksDB(tasks_db_path(tmp_path))
        for index in range(40):
            db.upsert_unique_candidate(
                {
                    "uc_key": f"X::{index}",
                    "symbol": "X",
                    "timeframe": "1h",
                    "family": "momentum_breakout",
                    "params_hash": f"ph-{index}",
                    "data_fingerprint": "fp",
                    "decision": "REJECT",
                    "validation_status": "REJECT",
                    "hard_status": "",
                    "n_trades": 20,
                    "avg_net_pct": -0.5,
                    "candidate_id": f"c-{index}",
                    "params": {},
                    "run_dir_label": "",
                },
                now=float(index + 1),
            )
        db.close()
        calls = 0
        milestones: list[tuple[str, int, int]] = []

        class Cancelled(RuntimeError):
            pass

        def check_active() -> None:
            nonlocal calls
            calls += 1
            if calls >= 30:
                raise Cancelled("synthetic stop")

        with pytest.raises(Cancelled, match="synthetic stop"):
            characterize_rejects(
                tmp_path,
                progress=lambda stage, completed, total: milestones.append(
                    (stage, completed, total)
                ),
                check_active=check_active,
            )

        assert ("rejected_candidates_loaded", 40, 40) in milestones
        assert ("rejects_characterized", 25, 40) in milestones

    def test_attaches_backfill_path_metrics_when_present(self, tmp_path):
        import json
        db = FarmTasksDB(tasks_db_path(tmp_path))
        uc = "X::1h::momentum_breakout::ph::fp"
        db.upsert_unique_candidate({
            "uc_key": uc, "symbol": "X", "timeframe": "1h", "family": "momentum_breakout",
            "params_hash": "ph", "data_fingerprint": "fp", "decision": "REJECT",
            "validation_status": "REJECT", "hard_status": "", "n_trades": 6, "avg_net_pct": -0.1,
            "candidate_id": "c1", "params": {}}, now=1.0)
        db.close()
        deriv = tmp_path / "state" / "derived"
        deriv.mkdir(parents=True, exist_ok=True)
        (deriv / "trade_path_backfill.json").write_text(json.dumps({
            "by_uc_key": {uc: {"avg_time_to_mfe": 4.0, "avg_time_to_mae": 2.0,
                               "tp_before_sl_share": 0.1, "adverse_first_rate": 0.5,
                               "path_quality": {"gave_back": 5}}}}), encoding="utf-8")
        r = build_memory_index(tmp_path)[0]
        assert r["time_to_mfe"] == 4.0 and r["path_quality"] == {"gave_back": 5}

    def test_revalidation_attaches_but_never_promotes(self, tmp_path):
        import json
        db = FarmTasksDB(tasks_db_path(tmp_path))
        uc = "X::4h::mean_reversion_fade::ph::fp"
        db.upsert_unique_candidate({
            "uc_key": uc, "symbol": "X", "timeframe": "4h", "family": "mean_reversion_fade",
            "params_hash": "ph", "data_fingerprint": "fp", "decision": "REJECT",
            "validation_status": "REJECT", "hard_status": "", "n_trades": 6, "avg_net_pct": -0.1,
            "candidate_id": "c1", "params": {}}, now=1.0)
        db.close()
        deriv = tmp_path / "state" / "derived"
        deriv.mkdir(parents=True, exist_ok=True)
        # a re-validation survivor must attach its status but NOT flip paper_forward_ready/outcome_class
        (deriv / "recyclable_revalidation.json").write_text(json.dumps({
            "by_uc_key": {uc: {"revalidation_status": "PAPER_FORWARD_READY", "bucket": "exit_recovered"}}}),
            encoding="utf-8")
        r = build_memory_index(tmp_path)[0]
        assert r["revalidation_status"] == "PAPER_FORWARD_READY"
        assert r["paper_forward_ready"] is False            # NOT auto-promoted
        assert r["outcome_class"] != "POSITIVE_VALIDATED"   # canonical class unchanged
        assert summarize_memory([r])["paper_ready_without_hard_pass"] == 0

    def test_attaches_product_training_money_without_promoting(
        self, tmp_path, monkeypatch
    ):
        import json
        from src.research_lab import setup_outcome_memory

        training_rows = [
            {
                "lifecycle_schema": "PaperSignalLifecycle.v2",
                "setup_candidate_id": "candidate_1",
                "candidate_id": "candidate_1",
                "symbol": "X",
                "timeframe": "1h",
                "family": "momentum_breakout",
                "paper_pnl_usdt": -1.5,
                "net_pct": -1.0,
                "diagnosis": "bad_exit_gave_back",
                "outcome_learning_bucket": "gave_back",
                "outcome_learning_actionability": "retest_exit_or_capture",
            },
            {
                "lifecycle_schema": "PaperSignalLifecycle.v2",
                "setup_candidate_id": "candidate_1",
                "candidate_id": "candidate_1",
                "symbol": "X",
                "timeframe": "1h",
                "family": "momentum_breakout",
                "paper_pnl_usdt": 0.6,
                "net_pct": 0.4,
                "outcome_learning_bucket": "win",
            },
        ]
        current_projection = _bind_current_training_rows(training_rows)
        monkeypatch.setattr(
            setup_outcome_memory,
            "read_projection_view",
            lambda *_args, **_kwargs: current_projection,
        )
        db = FarmTasksDB(tasks_db_path(tmp_path))
        uc = "X::1h::momentum_breakout::ph::fp"
        db.upsert_unique_candidate({
            "uc_key": uc, "symbol": "X", "timeframe": "1h", "family": "momentum_breakout",
            "params_hash": "ph", "data_fingerprint": "fp", "decision": "REJECT",
            "validation_status": "REJECT", "hard_status": "", "n_trades": 20, "avg_net_pct": -0.5,
            "candidate_id": "candidate_1", "params": {}}, now=1.0)
        db.close()
        derived = tmp_path / "state" / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "paper_signal_training.jsonl").write_text(
            "\n".join(
                json.dumps(row, sort_keys=True)
                for row in training_rows
            )
            + "\n",
            encoding="utf-8",
        )

        rec = build_memory_index(tmp_path)[0]
        summary = summarize_memory([rec])

        assert rec["paper_terminal_rows"] == 2
        assert rec["paper_pnl_usdt"] == -0.9
        assert rec["paper_avg_pnl_usdt"] == -0.45
        assert rec["paper_gave_back_rows"] == 1
        assert rec["product_training"]["outcome_bucket"] == {"gave_back": 1, "win": 1}
        assert rec["product_training"]["actionability"] == {"retest_exit_or_capture": 1}
        assert rec["paper_forward_ready"] is False
        assert summary["paper_terminal_rows"] == 2
        assert summary["paper_pnl_usdt"] == -0.9
        assert summary["paper_gave_back_rows"] == 1

    def test_attaches_completed_outcome_retest_without_promoting(self, tmp_path):
        import json

        db = FarmTasksDB(tasks_db_path(tmp_path))
        uc = "X::1h::momentum_breakout::ph::fp"
        db.upsert_unique_candidate({
            "uc_key": uc, "symbol": "X", "timeframe": "1h", "family": "momentum_breakout",
            "params_hash": "ph", "data_fingerprint": "fp", "decision": "REJECT",
            "validation_status": "REJECT", "hard_status": "", "n_trades": 20, "avg_net_pct": -0.5,
            "candidate_id": "candidate_1", "params": {}}, now=1.0)
        db.close()
        derived = tmp_path / "state" / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "outcome_retest_results.json").write_text(
            json.dumps({"items": [{
                "retest_id": "ort_1", "review_id": "review_1",
                "source_candidate_id": "candidate_1", "verdict": "improved_directional",
                "best_n_trades": 20, "delta_vs_baseline_pct": 1.2,
                "comparison_kind": "directional_retest_not_single_trade_pnl_attribution",
            }]}),
            encoding="utf-8",
        )

        rec = build_memory_index(tmp_path)[0]
        summary = summarize_memory([rec])

        assert rec["outcome_retest"]["retest_id"] == "ort_1"
        assert rec["outcome_retest"]["verdict"] == "improved_directional"
        assert rec["paper_forward_ready"] is False
        assert summary["outcome_retest_records"] == 1
        assert summary["outcome_retest_by_verdict"] == {"improved_directional": 1}

    def test_summarizes_product_training_memory_separately(
        self, tmp_path, monkeypatch
    ):
        import json
        from src.research_lab import setup_outcome_memory

        training_rows = [
            {
                "lifecycle_schema": "PaperSignalLifecycle.v2",
                "symbol": "X-USDT-SWAP",
                "timeframe": "15m",
                "family": "early_tp_tactical",
                "farm_geometry_profile_id": "base",
                "paper_pnl_usdt": 1.2,
                "net_pct": 1.0,
                "outcome_learning_bucket": "win",
            },
            {
                "lifecycle_schema": "PaperSignalLifecycle.v2",
                "okx_inst_id": "X-USDT-SWAP",
                "timeframe": "15m",
                "family": "early_tp_tactical",
                "farm_geometry_profile_id": "faster_capture",
                "paper_pnl_usdt": -0.3,
                "net_pct": -0.25,
                "diagnosis": "bad_exit_gave_back",
            },
        ]
        current_projection = _bind_current_training_rows(training_rows)
        monkeypatch.setattr(
            setup_outcome_memory,
            "read_projection_view",
            lambda *_args, **_kwargs: current_projection,
        )
        derived = tmp_path / "state" / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "paper_signal_training.jsonl").write_text(
            "\n".join(
                json.dumps(row, sort_keys=True)
                for row in training_rows
            )
            + "\n",
            encoding="utf-8",
        )

        summary = summarize_product_training_memory(tmp_path)

        assert summary["cells"] == 1
        assert summary["summary"]["terminal_rows"] == 2
        assert summary["summary"]["paper_pnl_usdt"] == 0.9
        assert summary["summary"]["gave_back_rows"] == 1
        assert summary["by_family"]["early_tp_tactical"]["rows"] == 2
        assert summary["geometry_profile_cells"] == 2
        assert summary["by_geometry_profile"]["base"]["win_rows"] == 1
        assert summary["by_geometry_profile"]["faster_capture"]["loss_rows"] == 1
        assert summary["eligible_rows"] == 2
        assert summary["excluded_rows"] == 0
        assert summary["current_generation_compatible"] is True
        assert summary["by_geometry_profile_cell"]["X_USDT_SWAP|15m|early_tp_tactical|faster_capture"][
            "gave_back_rows"
        ] == 1

    def test_product_training_memory_rejects_unversioned_file_authority(
        self, tmp_path
    ):
        import json

        derived = tmp_path / "state" / "derived"
        derived.mkdir(parents=True, exist_ok=True)
        (derived / "paper_signal_training.jsonl").write_text(
            json.dumps(
                {
                    "lifecycle_schema": "PaperSignalLifecycle.v2",
                    "symbol": "X-USDT-SWAP",
                    "timeframe": "15m",
                    "family": "early_tp_tactical",
                    "farm_geometry_profile_id": "base",
                    "paper_pnl_usdt": 1.2,
                    "net_pct": 1.0,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        summary = summarize_product_training_memory(tmp_path)

        assert summary["source_rows"] == 1
        assert summary["eligible_rows"] == 0
        assert summary["summary"]["terminal_rows"] == 0
        assert summary["display_only"] is True
        assert summary["evidence_rejection_counts"] == {
            "generation_not_current_or_complete": 1
        }


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
