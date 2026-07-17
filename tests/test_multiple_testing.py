# -*- coding: utf-8 -*-
"""Phase 1.2 — multiple-testing correction in hard validation.

A deeper sweep picks the best of N variants, inflating apparent significance. The
validator now deflates the permutation p by a Sidak family-wise adjustment over the
trial count, and requires CI>0 AND adjusted-p<0.05 (was a loose OR with p<0.10).
"""
from __future__ import annotations

import time

import pytest

from src.research_lab.experiment import ExperimentSpec, RunResult, _finalize_runtime_meta
from src.research_lab.hard_validation_contract import CandidateForValidation
from src.research_lab.honest_backtest_bridge import _check_overfit, _check_significance, _n_trials
from src.research_lab.search_trial_evidence import build_search_trial_evidence


def _terminal_metrics(spec: ExperimentSpec) -> dict:
    return {
        "data_snapshot_id": spec.data_snapshot_id,
        "data_evidence_hash": spec.data_evidence_hash,
        "family_data_snapshot_id": spec.data_snapshot_id,
        "family_data_evidence_hash": spec.data_evidence_hash,
        "execution_identity": {
            "requested_backend": spec.backend,
            "resolved_backend": "cpu",
            "backend_name": "numpy",
            "signal_backend": "cpu",
            "signal_kernel": "strategy_generator",
            "signal_backend_reason": "resolved_cpu",
            "signal_candle_count": 100,
            "signal_family_variant_count": len(
                spec.parameter_grid["momentum_breakout"]
            ),
            "simulation_backend": "cpu",
            "simulator": "cpu_simulator",
            "terminal_phase": "completed",
        },
    }


def _candidate(metrics: dict) -> CandidateForValidation:
    return CandidateForValidation(
        candidate_id="c", source_run_id="r", symbol="BTC-USDT-SWAP",
        normalized_symbol="BTC_USDT_SWAP", timeframe="1d", strategy_id="trend",
        params={}, filters={}, fees_bps=7.0, slippage_bps=3.0, lite_status="FORWARD_PAPER",
        lite_reasons=[], risk_flags=[], metrics=metrics, trades=[], equity_curve=[],
        data_window={}, created_at="2026-06-20T00:00:00Z",
    )


class TestNTrials:
    @staticmethod
    def _evidence() -> dict:
        spec = ExperimentSpec(
            experiment_id="family-count",
            data_glob="unused",
            symbols=["BTC"],
            families=["momentum_breakout"],
            parameter_grid={"momentum_breakout": [{"lookback": 10}, {"lookback": 20}]},
            data_snapshot_id="csnap-count",
            data_evidence_hash="evidence-count",
        )
        results = [
            RunResult(
                run_id=f"run-{lookback}",
                symbol="BTC",
                family="momentum_breakout",
                params={"lookback": lookback},
                metrics=_terminal_metrics(spec),
                decision="REJECT",
                reasons=[],
            )
            for lookback in (10, 20)
        ]
        return build_search_trial_evidence(
            spec,
            results,
            {
                "n_variants_evaluated": 2,
                "effective_backend": "cpu",
                "resolved_backend": "cpu",
                "signal_backend": "cpu",
                "simulation_backend": "cpu",
            },
        )

    def test_recomputes_from_verified_family(self) -> None:
        evidence = self._evidence()
        assert _n_trials(_candidate({"search_trial_evidence": evidence})) == 2

    def test_rejects_runtime_count_mismatch(self) -> None:
        evidence = self._evidence()
        with pytest.raises(ValueError, match="producer runtime.n_variants_evaluated mismatch"):
            _n_trials(
                _candidate(
                    {
                        "search_trial_evidence": evidence,
                        "runtime": {"n_variants_evaluated": 24},
                    }
                )
            )

    def test_missing_family_evidence_fails_closed(self) -> None:
        with pytest.raises(ValueError, match="verified search family evidence is required"):
            _n_trials(_candidate({}))

    def test_garbage_count_does_not_substitute(self) -> None:
        evidence = self._evidence()
        with pytest.raises(ValueError, match="producer n_trials is not an integer"):
            _n_trials(
                _candidate(
                    {
                        "search_trial_evidence": evidence,
                        "runtime": {"n_variants_evaluated": "x"},
                    }
                )
            )

    def test_execution_cap_does_not_confuse_attempted_and_family_counts(self) -> None:
        spec = ExperimentSpec(
            experiment_id="capped-multi-symbol",
            data_glob="unused",
            symbols=["BTC", "ETH"],
            families=["momentum_breakout"],
            parameter_grid={"momentum_breakout": [{"lookback": 10}]},
            max_runs=1,
            data_snapshot_id="csnap-capped",
            data_evidence_hash="evidence-capped",
        )
        result = RunResult(
            run_id="run-btc",
            symbol="BTC",
            family="momentum_breakout",
            params={"lookback": 10},
            metrics=_terminal_metrics(spec),
            decision="REJECT",
            reasons=[],
        )
        evidence = build_search_trial_evidence(
            spec,
            [result],
            {
                "n_variants_evaluated": 1,
                "effective_backend": "cpu",
                "signal_backend": "cpu",
                "simulation_backend": "cpu",
            },
        )
        candidate = _candidate(
            {
                "search_trial_evidence": evidence,
                "runtime": {"n_variants_evaluated": 1},
            }
        )
        assert _n_trials(candidate) == 2


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


class TestFamilyCoverageGate:
    RETURNS = [3.0, 2.0, 4.0, -1.0, 3.0, 2.0, 5.0, -2.0, 3.0, 2.0, 4.0, 1.0]

    def test_v2_evidence_without_coverage_fails_closed(self) -> None:
        result = _check_overfit(
            _candidate({"search_trial_evidence": {"schema": "SearchTrialEvidence.v2"}}),
            self.RETURNS,
        )
        assert result["passed"] is False
        assert result["details"]["family_coverage_error"] == (
            "missing_or_incomplete_family_coverage"
        )

    def test_fewer_than_two_comparable_trials_fails_closed(self) -> None:
        result = _check_overfit(
            _candidate(
                {
                    "search_trial_evidence": {"schema": "SearchTrialEvidence.v2"},
                    "pbo_dsr_family_coverage": {
                        "complete": True,
                        "included_count": 1,
                    },
                }
            ),
            self.RETURNS,
        )
        assert result["passed"] is False
        assert result["details"]["family_coverage_error"] == (
            "fewer_than_two_comparable_family_trials"
        )


class TestRuntimeRecordsVariants:
    def test_finalize_records_n_variants(self) -> None:
        meta: dict = {}
        _finalize_runtime_meta(meta, time.perf_counter(), 0, 0, set(), set(), n_variants=7)
        assert meta["n_variants_evaluated"] == 7
