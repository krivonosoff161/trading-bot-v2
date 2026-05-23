"""Research-only per-regime contract skeleton - 23.05.2026.

This module is intentionally detached from the live bot. It proves the target
decomposition shape:

classifier/router -> one regime analyzer -> self-contained signal contract ->
execution orchestrator stub.

It is not imported by production code and does not place orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class Regime(str, Enum):
    TRENDING_IMPULSE = "TRENDING_IMPULSE"
    DRIFT = "DRIFT"
    RANGING = "RANGING"
    NO_TRADE = "NO_TRADE"


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class FeatureSnapshot:
    symbol: str
    ts_ms: int
    price: float
    indicators: dict[str, Any]
    candles: dict[str, list[list[Any]]]
    tape: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExitRule:
    name: str
    params: dict[str, Any]


@dataclass(frozen=True)
class FollowRule:
    breakeven_at_r: float | None = None
    trail_name: str | None = None
    trail_params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalContract:
    symbol: str
    regime: Regime
    side: Side
    entry_price: float
    stop_price: float
    exit_rule: ExitRule
    max_hold_min: int
    follow: FollowRule
    analyzer: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Analyzer(Protocol):
    namespace: str

    def analyze(self, snapshot: FeatureSnapshot) -> SignalContract | None:
        ...


class RegimeClassifier:
    def classify(self, snapshot: FeatureSnapshot) -> Regime:
        ind = snapshot.indicators
        if bool(ind.get("impulse_trigger")):
            return Regime.TRENDING_IMPULSE
        if bool(ind.get("bb_boundary_touch")) and ind.get("adx_1h", 99) <= 22:
            return Regime.RANGING
        if bool(ind.get("drift_walk")):
            return Regime.DRIFT
        return Regime.NO_TRADE


class TrendingImpulseAnalyzer:
    namespace = "trending_impulse"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    def analyze(self, snapshot: FeatureSnapshot) -> SignalContract | None:
        side = Side.LONG if snapshot.indicators.get("impulse_side") == "long" else Side.SHORT
        stop = float(snapshot.indicators["structural_stop"])
        return SignalContract(
            symbol=snapshot.symbol,
            regime=Regime.TRENDING_IMPULSE,
            side=side,
            entry_price=snapshot.price,
            stop_price=stop,
            exit_rule=ExitRule("scaled_tp_or_structure", dict(self.params["exit"])),
            max_hold_min=int(self.params["max_hold_min"]),
            follow=FollowRule(trail_name="structure", trail_params=dict(self.params["trail"])),
            analyzer=self.namespace,
        )


class DriftAnalyzer:
    namespace = "drift"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    def analyze(self, snapshot: FeatureSnapshot) -> SignalContract | None:
        return None


class RangingFadeAnalyzer:
    namespace = "ranging_fade"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    def analyze(self, snapshot: FeatureSnapshot) -> SignalContract | None:
        side = Side.SHORT if snapshot.indicators.get("bb_position", 0.5) >= 0.95 else Side.LONG
        return SignalContract(
            symbol=snapshot.symbol,
            regime=Regime.RANGING,
            side=side,
            entry_price=snapshot.price,
            stop_price=float(snapshot.indicators["range_stop"]),
            exit_rule=ExitRule("bb_middle_or_opposite", dict(self.params["exit"])),
            max_hold_min=int(self.params["max_hold_min"]),
            follow=FollowRule(),
            analyzer=self.namespace,
        )


class ResearchOrchestrator:
    def __init__(self, classifier: RegimeClassifier, analyzers: dict[Regime, Analyzer]) -> None:
        self.classifier = classifier
        self.analyzers = analyzers
        self.open_owner_by_symbol: dict[str, str] = {}

    def route(self, snapshot: FeatureSnapshot) -> SignalContract | None:
        if snapshot.symbol in self.open_owner_by_symbol:
            return None
        regime = self.classifier.classify(snapshot)
        analyzer = self.analyzers.get(regime)
        if analyzer is None:
            return None
        signal = analyzer.analyze(snapshot)
        if signal is None:
            return None
        self._assert_invariants(snapshot, signal)
        return signal

    def mark_open(self, signal: SignalContract) -> None:
        self.open_owner_by_symbol[signal.symbol] = signal.analyzer

    def mark_closed(self, symbol: str) -> None:
        self.open_owner_by_symbol.pop(symbol, None)

    def _assert_invariants(self, snapshot: FeatureSnapshot, signal: SignalContract) -> None:
        if signal.symbol != snapshot.symbol:
            raise ValueError("signal must belong to the routed snapshot symbol")
        if signal.analyzer not in {a.namespace for a in self.analyzers.values()}:
            raise ValueError("signal analyzer must be a registered owner")
        if signal.entry_price <= 0 or signal.stop_price <= 0:
            raise ValueError("signal must carry executable entry and stop")


def demo_config() -> dict[Regime, Analyzer]:
    return {
        Regime.TRENDING_IMPULSE: TrendingImpulseAnalyzer(
            {
                "max_hold_min": 60,
                "exit": {"tp_ref": "mfe_p50_or_impulse_body_1x", "structure_k": 2},
                "trail": {"structure_k": 2, "giveback_pct": 40},
            }
        ),
        Regime.DRIFT: DriftAnalyzer({"mode": "disabled_until_edge_proven"}),
        Regime.RANGING: RangingFadeAnalyzer(
            {
                "max_hold_min": 80,
                "exit": {"target": "bb_middle", "fallback": "opposite_band"},
            }
        ),
    }


if __name__ == "__main__":
    orchestrator = ResearchOrchestrator(RegimeClassifier(), demo_config())
    snapshot = FeatureSnapshot(
        symbol="TEST-USDT-SWAP",
        ts_ms=0,
        price=100.0,
        indicators={
            "impulse_trigger": True,
            "impulse_side": "long",
            "structural_stop": 98.5,
        },
        candles={},
    )
    contract = orchestrator.route(snapshot)
    print(contract)
