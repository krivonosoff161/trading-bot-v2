from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.research_lab.feature_packet import (
    build_feature_packet,
    build_outcome_feature_packet,
)
from src.research_lab.lineage_contract import scanner_event_from_mover
from src.research_lab.market_data_packet import (
    build_market_data_packet,
    write_market_data_packet,
)
from src.research_lab.research_envelope import (
    build_decision_envelope,
    extend_with_outcome,
    write_research_envelope,
)


class ResearchEnvelopeTest(unittest.TestCase):
    def test_decision_identity_is_not_replaced_by_outcome(self) -> None:
        candles = [
            {
                "ts": index * 900_000,
                "open": 100 + index / 100,
                "high": 101 + index / 100,
                "low": 99 + index / 100,
                "close": 100.5 + index / 100,
                "vol": 10,
                "confirm": 1,
            }
            for index in range(300)
        ]
        event = scanner_event_from_mover(
            {"event_id": "event-1", "source": "scanner", "reason": "test"},
            symbol="BTC_USDT_SWAP",
            instrument="BTC-USDT-SWAP",
            timeframe="15m",
            mode="replay",
            timestamp="2026-07-14T00:00:00+00:00",
        )
        data = build_market_data_packet(
            scanner_event_id=event.scanner_event_id,
            symbol=event.symbol,
            instrument=event.instrument,
            timeframe=event.timeframe,
            mode="replay",
            candles=candles,
        )
        decision = build_feature_packet(
            data,
            side="long",
            entry_zone=[101, 102],
            stop_loss=99,
            take_profit_plan=[{"price": 105, "size_frac": 1}],
        )
        outcome = build_outcome_feature_packet(data, decision, side="long")
        self.assertIsNotNone(outcome)
        assert outcome is not None
        changed_future = [dict(row) for row in data.future_window]
        changed_future[len(changed_future) // 2]["close"] += 0.001
        changed_outcome = build_outcome_feature_packet(
            replace(data, future_window=changed_future), decision, side="long",
        )
        self.assertIsNotNone(changed_outcome)
        assert changed_outcome is not None
        self.assertNotEqual(outcome.outcome_packet_id, changed_outcome.outcome_packet_id)

        decision_envelope = build_decision_envelope(
            event, data, decision, paper_signal_id="signal-1",
        )
        outcome_envelope = extend_with_outcome(decision_envelope, outcome)

        self.assertNotEqual(
            decision_envelope.research_envelope_id,
            outcome_envelope.research_envelope_id,
        )
        self.assertEqual(
            outcome_envelope.parent_envelope_id,
            decision_envelope.research_envelope_id,
        )
        self.assertEqual(decision_envelope.evidence["outcome_packet_id"], "")
        self.assertEqual(
            outcome_envelope.evidence["outcome_packet_id"], outcome.outcome_packet_id,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = write_market_data_packet(root, data)
            second = write_market_data_packet(root, data)
            self.assertEqual(first, second)
            write_research_envelope(root, decision_envelope)
            write_research_envelope(root, outcome_envelope)


if __name__ == "__main__":
    unittest.main()
