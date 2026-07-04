# -*- coding: utf-8 -*-
"""Phase 1.2 — multiple-testing correction in hard validation.

A deeper sweep picks the best of N variants, inflating apparent significance. The
validator now deflates the permutation p by a Sidak family-wise adjustment over the
trial count, and requires CI>0 AND adjusted-p<0.05 (was a loose OR with p<0.10).
"""
from __future__ import annotations

import time

from src.research_lab.experiment import _finalize_runtime_meta
from src.research_lab.hard_validation_contract import CandidateForValidation
from src.research_lab.honest_backtest_bridge import _check_significance, _n_trials


def _candidate(metrics: dict) -> CandidateForValidation:
    return CandidateForValidation(
        candidate_id="c", source_run_id="r", symbol="BTC-USDT-SWAP",
        normalized_symbol="BTC_USDT_SWAP", timeframe="1d", strategy_id="trend",
        params={}, filters={}, fees_bps=7.0, slippage_bps=3.0, lite_status="FORWARD_PAPER",
        lite_reasons=[], risk_flags=[], metrics=metrics, trades=[], equity_curve=[],
        data_window={}, created_at="2026-06-20T00:00:00Z",
    )


class TestNTrials:
    def test_from_runtime(self) -> None:
        assert _n_trials(_candidate({"runtime": {"n_variants_evaluated": 24}})) == 24

    def test_from_variant_count(self) -> None:
        assert _n_trials(_candidate({"variant_count": 12})) == 12

    def test_default_one(self) -> None:
        assert _n_trials(_candidate({})) == 1

    def test_ignores_garbage(self) -> None:
        assert _n_trials(_candidate({"runtime": {"n_variants_evaluated": "x"}})) == 1


class TestSignificanceAdjustment:
    RETURNS = [3.0, 2.0, 4.0, -1.0, 3.0, 2.0, 5.0, -2.0, 3.0, 2.0, 4.0, 1.0]

    def test_single_trial_unchanged(self) -> None:
        res = _check_significance(self.RETURNS, n_trials=1)
        assert res["details"]["permutation_p_adjusted"] == res["details"]["permutation_p"]
        assert res["details"]["n_trials"] == 1

    def test_p_adjusted_grows_with_trials(self) -> None:
        one = _check_significance(self.RETURNS, n_trials=1)["details"]["permutation_p_adjusted"]
        many = _check_significance(self.RETURNS, n_trials=100)["details"]["permutation_p_adjusted"]
        assert many >= one

    def test_deep_sweep_can_flip_pass_to_fail(self) -> None:
        # Clear positive edge -> passes at 1 trial; a huge trial count deflates it to fail.
        shallow = _check_significance(self.RETURNS, n_trials=1)
        deep = _check_significance(self.RETURNS, n_trials=100000)
        assert shallow["passed"] is True
        assert deep["passed"] is False

    def test_requires_both_ci_and_p(self) -> None:
        # Noise around zero: CI lower bound not > 0 -> fail regardless of p.
        noisy = [1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0]
        assert _check_significance(noisy, n_trials=1)["passed"] is False


class TestRuntimeRecordsVariants:
    def test_finalize_records_n_variants(self) -> None:
        meta: dict = {}
        _finalize_runtime_meta(meta, time.perf_counter(), 0, 0, set(), set(), n_variants=7)
        assert meta["n_variants_evaluated"] == 7
