from __future__ import annotations

import json

from src.research_lab.feature_packet import build_feature_packet
from src.research_lab.llm_provider import LLMUsage
from src.research_lab.local_calculator_swarm import request_local_calculator_swarm
from src.research_lab.market_data_packet import build_market_data_packet


def _packet():
    candles = [
        {"ts": index * 60_000, "open": 100 + index, "high": 101 + index, "low": 99 + index, "close": 100.5 + index, "volume": 10}
        for index in range(80)
    ]
    data = build_market_data_packet(
        scanner_event_id="event1",
        symbol="X_USDT_SWAP",
        instrument="X-USDT-SWAP",
        timeframe="15m",
        mode="live",
        candles=candles,
    )
    return build_feature_packet(data)


class _SwarmProvider:
    name = "synthetic"
    model_name = "offline"
    configured = True

    def __init__(self):
        self.calls = 0

    def generate(self, system, user):
        self.calls += 1
        assert "feature_packet" in user
        if "situation_class" in system:
            payload = {"situation_class": "trend", "missing_data": [], "confidence": 0.7, "warnings": []}
        elif "advisory_reason" in system:
            payload = {"advisory_reason": "compare hold", "sweep_suggestions": ["hold"], "confidence": 0.6, "warnings": []}
        else:
            payload = {"proposal_quality": "accept", "rejection_reason": "", "confidence": 0.8, "warnings": []}
        return json.dumps(payload), LLMUsage(provider=self.name, model=self.model_name, total_tokens=10)


def test_local_swarm_runs_three_sequential_bounded_passes(tmp_path):
    provider = _SwarmProvider()

    advice = request_local_calculator_swarm(tmp_path, _packet(), provider)

    assert provider.calls == 3
    assert advice.accepted is True
    assert advice.advice["situation_class"] == "trend"
    assert advice.advice["sweep_suggestions"] == ["hold"]
    assert advice.execution_allowed is False
    rows = [json.loads(line) for line in (tmp_path / "state" / "llm_advice" / "invocations.jsonl").read_text().splitlines()]
    assert [row["role_id"] for row in rows] == [
        "calculator_context_classifier",
        "calculator_hypothesis_proposer",
        "calculator_hypothesis_critic",
    ]


def test_local_swarm_pre_call_dedup_avoids_repeat_calls(tmp_path):
    provider = _SwarmProvider()
    packet = _packet()
    first = request_local_calculator_swarm(tmp_path, packet, provider)
    second = request_local_calculator_swarm(tmp_path, packet, provider)

    assert first.accepted is True
    assert second.accepted is False
    assert provider.calls == 3
    assert "duplicate_completed" in second.problems
