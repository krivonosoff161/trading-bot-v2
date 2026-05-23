"""Shared signal contract for additive paper analyzers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Side = Literal["long", "short"]
ExitRuleType = Literal["ride", "scaled", "fade"]


@dataclass(frozen=True)
class ExitRule:
    type: ExitRuleType
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FollowRule:
    be_at_R: float | None = None
    trail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SignalContract:
    pair: str
    side: Side
    entry: float
    stop: float
    exit_rule: ExitRule
    max_hold_min: int
    follow: FollowRule
    regime: str
    analyzer_id: str
    snapshot_id: str
    ts: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.side not in {"long", "short"}:
            raise ValueError("side must be long or short")
        if self.entry <= 0 or self.stop <= 0:
            raise ValueError("entry and stop must be positive")
        if self.entry == self.stop:
            raise ValueError("entry and stop must differ")
        if self.max_hold_min <= 0:
            raise ValueError("max_hold_min must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
