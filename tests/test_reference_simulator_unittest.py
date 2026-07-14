from __future__ import annotations

import unittest

from src.research_lab.experiment import simulate_trades
from src.research_lab.reference_simulator import simulate_reference_fixed


class ReferenceSimulatorTest(unittest.TestCase):
    def test_matches_production_fixed_exit_identity_and_net_return(self) -> None:
        candles = [
            {"ts": 0, "open": 100, "high": 101, "low": 99, "close": 100},
            {"ts": 1, "open": 100, "high": 103, "low": 98, "close": 102},
            {"ts": 2, "open": 102, "high": 104, "low": 100, "close": 103},
            {"ts": 3, "open": 103, "high": 104, "low": 96, "close": 97},
        ]
        signals = [
            {"idx": 1, "side": "long", "reason": "fixture"},
            {"idx": 2, "side": "short", "reason": "fixture"},
        ]
        params = {"hold_bars": 2, "stop_pct": 2, "take_pct": 3}
        production = simulate_trades(
            candles, signals, params, fees_bps=7, slippage_bps=3,
        )
        reference = simulate_reference_fixed(
            candles, signals, params, fees_bps=7, slippage_bps=3,
        )
        self.assertEqual(len(production), len(reference))
        for actual, expected in zip(production, reference):
            self.assertEqual(actual["side"], expected["side"])
            self.assertEqual(actual["outcome"], expected["outcome"])
            self.assertEqual(actual["entry"], expected["entry"])
            self.assertEqual(actual["exit"], expected["exit"])
            self.assertAlmostEqual(actual["net_pct"], expected["net_pct"], places=4)


if __name__ == "__main__":
    unittest.main()
