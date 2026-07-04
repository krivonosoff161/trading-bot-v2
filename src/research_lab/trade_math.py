"""Deterministic paper-trading math shared by farm, paper, and exports.

This module is the formula authority for paper/research metrics. It has no
exchange, account, Telegram, env, or order dependencies.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


DEFAULT_FEE_BPS = 7.0
DEFAULT_SLIPPAGE_BPS = 3.0


@dataclass(frozen=True)
class CostAssumptions:
    fees_bps_round_trip: float = DEFAULT_FEE_BPS
    slippage_bps_round_trip: float = DEFAULT_SLIPPAGE_BPS

    @property
    def total_cost_pct(self) -> float:
        return (self.fees_bps_round_trip + self.slippage_bps_round_trip) / 100.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def midpoint(values: list[float]) -> float:
    if len(values) != 2:
        return 0.0
    return round((float(values[0]) + float(values[1])) / 2.0, 10)


def first_tp(take_profit_plan: list[dict[str, Any]]) -> float:
    if not take_profit_plan:
        return 0.0
    try:
        return float(take_profit_plan[0].get("price") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def stop_distance(entry: float, stop: float) -> float:
    return abs(float(entry) - float(stop))


def risk_pct(entry: float, stop: float) -> float:
    entry = float(entry)
    if entry <= 0:
        return 0.0
    return round(stop_distance(entry, stop) / entry * 100.0, 6)


def reward_r(entry: float, stop: float, target: float, side: str) -> float:
    risk = stop_distance(entry, stop)
    if risk <= 0:
        return 0.0
    if side == "long":
        reward = float(target) - float(entry)
    else:
        reward = float(entry) - float(target)
    return round(reward / risk, 6)


def gross_pct(entry: float, exit_price: float, side: str) -> float:
    entry = float(entry)
    if entry <= 0:
        return 0.0
    if side == "long":
        value = (float(exit_price) - entry) / entry * 100.0
    else:
        value = (entry - float(exit_price)) / entry * 100.0
    return round(value, 6)


def net_pct(gross_value_pct: float, assumptions: CostAssumptions | None = None) -> float:
    costs = assumptions or CostAssumptions()
    return round(float(gross_value_pct) - costs.total_cost_pct, 6)


def capture(net_value_pct: float, mfe_pct: float) -> float:
    mfe = float(mfe_pct or 0.0)
    if mfe <= 0:
        return 0.0
    return round(float(net_value_pct or 0.0) / mfe, 6)


def geometry(entry_zone: list[float], stop: float, take_profit_plan: list[dict[str, Any]], side: str) -> dict[str, Any]:
    entry = midpoint(entry_zone)
    tp1 = first_tp(take_profit_plan)
    return {
        "entry_mid": entry,
        "stop_distance": round(stop_distance(entry, stop), 10),
        "risk_pct": risk_pct(entry, stop),
        "tp1": tp1,
        "rr_tp1": reward_r(entry, stop, tp1, side) if tp1 else 0.0,
        "cost_assumptions": CostAssumptions().to_dict(),
    }
