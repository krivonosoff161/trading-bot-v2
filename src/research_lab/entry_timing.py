# -*- coding: utf-8 -*-
"""Entry-timing metrics: measure the lab's recurring pain honestly.

Direction can be right while the entry is late, so final PnL alone hides where a
setup actually fails. These metrics separate "was the move real" from "did we get
in at a usable point": how late the entry was, how much of the move was already
gone, how much was captured, and the heat taken before it worked.

All inputs are bar indices into one OHLCV list. Entry price is the open of the
entry bar (matching the simulator). Everything is deterministic arithmetic.
"""

from __future__ import annotations

from typing import Any

Candle = dict[str, Any]

_LONG = {"long", "up", "buy", "+1", "1"}
_SHORT = {"short", "down", "sell", "-1"}


def _is_long(direction: str) -> bool:
    token = str(direction).strip().lower()
    if token in _LONG:
        return True
    if token in _SHORT:
        return False
    raise ValueError(f"unknown direction: {direction!r}")


def entry_timing_metrics(
    candles: list[Candle],
    *,
    move_start_idx: int,
    move_end_idx: int,
    entry_idx: int,
    direction: str,
    eval_end_idx: int | None = None,
    adverse_tol_pct: float = 2.0,
) -> dict[str, Any]:
    """Return entry-quality metrics for one move/entry pair (no profitability claim)."""
    n = len(candles)
    long = _is_long(direction)
    _validate_indices(n, move_start_idx, move_end_idx, entry_idx)
    eval_end = move_end_idx if eval_end_idx is None else int(eval_end_idx)
    eval_end = max(entry_idx, eval_end)
    if not (entry_idx <= eval_end < n):
        raise ValueError("eval_end_idx out of range relative to entry_idx")

    start_price = float(candles[move_start_idx]["close"])
    end_price = float(candles[move_end_idx]["close"])
    entry_price = float(candles[entry_idx]["open"])
    if entry_price <= 0 or start_price <= 0:
        raise ValueError("non-positive price at entry or move start")

    total = (end_price - start_price) if long else (start_price - end_price)
    progress = (entry_price - start_price) if long else (start_price - entry_price)
    captured = (end_price - entry_price) if long else (entry_price - end_price)
    zero_movement = abs(total) < 1e-12

    mfe, mae = _excursions(candles, entry_idx, eval_end, entry_price, long)
    entry_before_impulse = entry_idx <= move_start_idx
    return {
        "entry_lag_bars": entry_idx - move_start_idx,
        "total_move_pct": round(total / start_price * 100, 4),
        "missed_move_pct": 0.0 if zero_movement else round(max(0.0, progress / total) * 100, 4),
        "capture_ratio": 0.0 if zero_movement else round(captured / total, 4),
        "max_favorable_excursion_pct": round(mfe, 4),
        "max_adverse_excursion_pct": round(mae, 4),
        "entry_before_impulse": entry_before_impulse,
        "false_early_entry": bool(entry_before_impulse and mae >= float(adverse_tol_pct)),
        "zero_movement": zero_movement,
        "eval_end_idx": eval_end,
    }


def _excursions(
    candles: list[Candle],
    entry_idx: int,
    eval_end: int,
    entry_price: float,
    long: bool,
) -> tuple[float, float]:
    highs = [float(candles[j]["high"]) for j in range(entry_idx, eval_end + 1)]
    lows = [float(candles[j]["low"]) for j in range(entry_idx, eval_end + 1)]
    hi, lo = max(highs), min(lows)
    if long:
        favorable = (hi - entry_price) / entry_price * 100
        adverse = (entry_price - lo) / entry_price * 100
    else:
        favorable = (entry_price - lo) / entry_price * 100
        adverse = (hi - entry_price) / entry_price * 100
    return max(0.0, favorable), max(0.0, adverse)


def _validate_indices(n: int, move_start_idx: int, move_end_idx: int, entry_idx: int) -> None:
    if n == 0:
        raise ValueError("no candles provided")
    for name, idx in (("move_start_idx", move_start_idx), ("move_end_idx", move_end_idx), ("entry_idx", entry_idx)):
        if not isinstance(idx, int) or idx < 0 or idx >= n:
            raise ValueError(f"{name} out of range: {idx} (n={n})")
    if move_end_idx < move_start_idx:
        raise ValueError("move_end_idx must be >= move_start_idx")
