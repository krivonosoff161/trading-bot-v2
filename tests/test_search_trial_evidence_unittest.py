from __future__ import annotations

import unittest

from src.research_lab.experiment import ExperimentSpec, RunResult, stable_run_id
from src.research_lab.search_trial_evidence import build_search_trial_evidence
from src.research_lab.strategy_registry import get_strategy


class SearchTrialEvidenceTest(unittest.TestCase):
    def test_full_denominator_and_strong_run_identity(self) -> None:
        family = "momentum_breakout"
        spec = ExperimentSpec(
            experiment_id="experiment-1",
            data_glob="unused",
            symbols=["BTC"],
            families=[family],
            parameter_grid={family: [{"lookback": 10}, {"lookback": 20}]},
            timeframe="1h",
            plan_meta={"search_space": {"cartesian_total": 3, "eligible_total": 2}},
        )
        params = {
            **dict(get_strategy(family).parameter_defaults),
            "lookback": 10,
        }
        run_id = stable_run_id(
            "BTC",
            family,
            params,
            experiment_id=spec.experiment_id,
            data_fingerprint="data-a",
            timeframe=spec.timeframe,
            fees_bps=spec.fees_bps,
            slippage_bps=spec.slippage_bps,
            split_ratio=spec.split_ratio,
            filters=spec.filters,
        )
        other_data_run_id = stable_run_id(
            "BTC",
            family,
            params,
            experiment_id=spec.experiment_id,
            data_fingerprint="data-b",
            timeframe=spec.timeframe,
            fees_bps=spec.fees_bps,
            slippage_bps=spec.slippage_bps,
            split_ratio=spec.split_ratio,
            filters=spec.filters,
        )
        self.assertEqual(len(run_id), 64)
        self.assertNotEqual(run_id, other_data_run_id)

        result = RunResult(
            run_id=run_id,
            symbol="BTC",
            family=family,
            params=params,
            metrics={
                "data_fingerprint": "data-a",
                "execution_identity": {
                    "requested_backend": "cpu",
                    "resolved_backend": "cpu",
                    "backend_name": "numpy",
                    "signal_backend": "cpu",
                    "signal_kernel": "strategy_generator",
                    "signal_backend_reason": "resolved_cpu",
                    "signal_candle_count": 100,
                    "signal_family_variant_count": 2,
                    "simulation_backend": "cpu",
                    "simulator": "cpu_simulator",
                    "terminal_phase": "completed",
                },
            },
            decision="REJECT",
            reasons=[],
        )
        evidence = build_search_trial_evidence(spec, [result])
        self.assertEqual(evidence["search_space"]["evaluated"], 1)
        self.assertEqual(evidence["search_space"]["not_evaluated"], 1)
        self.assertEqual(len(evidence["trials"]), 2)
        self.assertTrue(evidence["search_trial_evidence_id"].startswith("ste_"))


if __name__ == "__main__":
    unittest.main()
