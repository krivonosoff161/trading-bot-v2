# -*- coding: utf-8 -*-
"""T1+T2 — tactical/rejected characterization, completion verdict, reconcile (all derive-only).

Invariant under test everywhere: a tactical/research label NEVER equals PAPER_FORWARD_READY
and grants no paper/trade access. No DB migration, no compute, no money path.
"""
from __future__ import annotations

from src.research_lab.farm_reconcile import completion_verdict, reconcile_dbs
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
from src.research_lab.setup_lifecycle import TACTICAL_STATUSES, derive_tactical_status
from src.research_lab.trade_path_diagnostics import classify_subreason

_OI = {"oi_funding_squeeze": "oi"}


def _facts(**over):
    base = dict(n_trades=5, avg_net_pct=0.0, best_net_pct=0.0, avg_mfe_pct=0.0,
                avg_capture_ratio=0.5, late_entry_rate=0.0, trades_available=True)
    base.update(over)
    return base


class TestClassifySubreason:
    def test_missing_oi_micro(self) -> None:
        assert classify_subreason(_facts(), "", "oi_funding_squeeze", {"oi_funding_squeeze"}) == "missing_oi_micro"

    def test_insufficient_data(self) -> None:
        assert classify_subreason(_facts(n_trades=0), "", "trend", set()) == "insufficient_data"

    def test_wrong_exit(self) -> None:
        f = _facts(n_trades=5, avg_net_pct=0.1, avg_mfe_pct=2.0, avg_capture_ratio=-0.5)
        assert classify_subreason(f, "FAILED_COSTS", "trend", set()) == "wrong_exit"

    def test_tactical_candidate(self) -> None:
        f = _facts(n_trades=1, avg_net_pct=0.5, avg_mfe_pct=0.4, avg_capture_ratio=0.9)
        assert classify_subreason(f, "", "trend", set()) == "tactical_candidate"

    def test_validator_too_strict(self) -> None:
        f = _facts(n_trades=5, avg_net_pct=0.5, avg_mfe_pct=0.5, avg_capture_ratio=0.9)
        assert classify_subreason(f, "", "trend", set()) == "validator_too_strict"

    def test_confirmed_bad(self) -> None:
        f = _facts(n_trades=15, avg_net_pct=-0.5, avg_mfe_pct=0.2, avg_capture_ratio=0.4)
        assert classify_subreason(f, "", "trend", set()) == "confirmed_bad"


class TestDeriveTacticalStatus:
    def _row(self, **over):
        base = dict(family="trend", decision="REJECT", validation_status="REJECT",
                    n_trades=5, avg_net_pct=0.0, regime_bucket="")
        base.update(over)
        return base

    def test_thin_window(self) -> None:
        assert derive_tactical_status(self._row(n_trades=1, avg_net_pct=0.5), "", _OI) == "TACTICAL_THIN_WINDOW"

    def test_cost_sensitive(self) -> None:
        assert derive_tactical_status(self._row(decision="", validation_status="FORWARD_PAPER"),
                                      "FAILED_COSTS", _OI) == "TACTICAL_COST_SENSITIVE"

    def test_regime_only(self) -> None:
        assert derive_tactical_status(self._row(), "REGIME_ONLY", _OI) == "TACTICAL_REGIME_ONLY"

    def test_confirmed_bad(self) -> None:
        assert derive_tactical_status(self._row(n_trades=15, avg_net_pct=-0.3), "", _OI) == "REJECTED_CONFIRMED_BAD"

    def test_oi_context(self) -> None:
        assert derive_tactical_status(self._row(family="oi_funding_squeeze"), "", _OI) == "NEEDS_OI_CONTEXT"

    def test_non_rejected_is_blank(self) -> None:
        row = self._row(decision="", validation_status="FORWARD_PAPER")
        assert derive_tactical_status(row, "", _OI) == ""

    def test_invariant_never_paper_forward_ready(self) -> None:
        # No tactical label is ever a promotion/trade status.
        assert "PAPER_FORWARD_READY" not in TACTICAL_STATUSES
        for n in (0, 1, 2, 5, 15):
            for hs in ("", "FAILED_COSTS", "REGIME_ONLY", "NEEDS_MORE_DATA", "HARD_REJECT"):
                out = derive_tactical_status(self._row(n_trades=n, avg_net_pct=0.1), hs, _OI)
                assert out != "PAPER_FORWARD_READY"
                assert out == "" or out in TACTICAL_STATUSES


class TestCompletionVerdict:
    def test_drained_on_empty(self, tmp_path) -> None:
        db = FarmTasksDB(tasks_db_path(tmp_path))
        db.close()
        assert completion_verdict(tmp_path)["state"] == "DRAINED"

    def test_paused_with_eligible_queued(self, tmp_path) -> None:
        db = FarmTasksDB(tasks_db_path(tmp_path))
        db.enqueue_task(task_type="run_sweep", task_key="k1", symbol="BTC-USDT-SWAP",
                        timeframe="1d", family="momentum_breakout", now=1.0)
        db.close()
        v = completion_verdict(tmp_path, now=1000.0)
        assert v["state"] == "PAUSED_WITH_WORK"
        assert v["reasons"]["eligible_now"] == 1


class TestReconcile:
    def test_available_false_without_dbs(self, tmp_path) -> None:
        rc = reconcile_dbs(tmp_path)
        assert rc["available"] is False
