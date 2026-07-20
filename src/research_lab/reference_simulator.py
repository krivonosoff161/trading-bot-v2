"""Small independent fixed-exit simulator used only as a semantic oracle."""

from __future__ import annotations

from typing import Any

from src.research_lab.simulator_contract import (
    build_cost_ledger,
    build_trade_quantity_ledger,
    legacy_fixture_manifest,
)


def simulate_reference_fixed(
    candles: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    params: dict[str, Any],
    *,
    fees_bps: float,
    slippage_bps: float,
) -> list[dict[str, Any]]:
    """Apply stop-first OHLC semantics without importing the production simulator."""
    hold = int(params.get("hold_bars", 5))
    stop_pct = float(params.get("stop_pct", 0.0))
    take_pct = float(params.get("take_pct", 0.0))
    cost_pct = (float(fees_bps) + float(slippage_bps)) / 10000.0
    rows: list[dict[str, Any]] = []
    for signal in signals:
        entry_index = int(signal["idx"])
        end_index = min(entry_index + hold, len(candles) - 1)
        if entry_index >= len(candles) or end_index <= entry_index:
            continue
        side = str(signal["side"])
        entry = float(candles[entry_index]["open"])
        if entry <= 0:
            continue
        long_side = side == "long"
        stop = entry * (1 - stop_pct / 100 if long_side else 1 + stop_pct / 100)
        take = entry * (1 + take_pct / 100 if long_side else 1 - take_pct / 100)
        exit_index = end_index
        exit_price = float(candles[end_index]["close"])
        outcome = "time_exit"
        for index in range(entry_index, end_index + 1):
            high = float(candles[index]["high"])
            low = float(candles[index]["low"])
            stop_hit = stop_pct > 0 and (low <= stop if long_side else high >= stop)
            take_hit = take_pct > 0 and (high >= take if long_side else low <= take)
            if stop_hit:
                exit_index, exit_price, outcome = index, stop, "stop"
                break
            if take_hit:
                exit_index, exit_price, outcome = index, take, "take"
                break
        gross = (
            (exit_price / entry - 1.0)
            if long_side
            else (1.0 - exit_price / entry)
        )
        rows.append(
            {
                "entry_ts": candles[entry_index]["ts"],
                "exit_ts": candles[exit_index]["ts"],
                "entry_idx": entry_index,
                "exit_idx": exit_index,
                "side": side,
                "entry": entry,
                "exit": exit_price,
                "outcome": outcome,
                "gross_pct": round(gross * 100, 6),
                "net_pct": round((gross - cost_pct) * 100, 6),
                "simulator_manifest": legacy_fixture_manifest(),
                "simulator_model_id": legacy_fixture_manifest()["simulator_model_id"],
                "simulator_evidence_tier": legacy_fixture_manifest()["evidence_tier"],
                "unsupported_simulator_dimensions": legacy_fixture_manifest()["unsupported_dimensions"],
                "cost_ledger": build_cost_ledger(
                    fees_bps=fees_bps, slippage_bps=slippage_bps,
                ),
                "quantity_ledger": build_trade_quantity_ledger(),
            }
        )
    return rows
