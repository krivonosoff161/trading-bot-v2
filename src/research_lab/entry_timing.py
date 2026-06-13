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

# A late entry: little of the move captured, or most of it already gone at entry.
LATE_ENTRY_CAPTURE_MIN = 0.3
LATE_ENTRY_MISS_PCT = 50.0


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


def event_anchored_timing(
    candles: list[Candle],
    *,
    move_start_idx: int,
    move_end_idx: int,
    entry_idx: int,
    direction: str,
    signal_idx: int | None = None,
    eval_end_idx: int | None = None,
    adverse_tol_pct: float = 2.0,
) -> dict[str, Any]:
    """Entry timing anchored to a known event (event time vs signal vs entry vs outcome).

    No look-ahead is introduced here: `entry_idx`/`signal_idx` are produced by the
    no-look-ahead simulator (it decides on bar idx-1 and enters at the open of idx).
    Future bars (up to `eval_end_idx`) are used ONLY to measure the outcome
    (MFE/MAE/capture), never to choose the entry. Deterministic arithmetic; not a
    profitability claim.
    """
    base = entry_timing_metrics(
        candles,
        move_start_idx=move_start_idx,
        move_end_idx=move_end_idx,
        entry_idx=entry_idx,
        direction=direction,
        eval_end_idx=eval_end_idx,
        adverse_tol_pct=adverse_tol_pct,
    )
    if signal_idx is None:
        signal_idx = max(0, entry_idx - 1)
    if not (0 <= signal_idx <= entry_idx < len(candles)):
        raise ValueError(f"signal_idx out of range: {signal_idx} (entry_idx={entry_idx})")

    event_ts = int(candles[move_start_idx]["ts"])
    first_entry_ts = int(candles[entry_idx]["ts"])
    signal_ts = int(candles[signal_idx]["ts"])
    capture = float(base["capture_ratio"])
    missed = float(base["missed_move_pct"])
    zero = bool(base["zero_movement"])
    late = (not zero) and (capture < LATE_ENTRY_CAPTURE_MIN or missed >= LATE_ENTRY_MISS_PCT)
    return {
        "event_ts": event_ts,
        "signal_ts": signal_ts,
        "first_entry_ts": first_entry_ts,
        "lag_bars": base["entry_lag_bars"],
        "lag_minutes": round((first_entry_ts - event_ts) / 60000.0, 4),
        "capture_ratio": capture,
        "missed_move_pct": missed,
        "total_move_pct": base["total_move_pct"],
        "mfe_pct": base["max_favorable_excursion_pct"],
        "mae_pct": base["max_adverse_excursion_pct"],
        "late_entry": late,
        "false_early_entry": bool(base["false_early_entry"]),
        "zero_movement": zero,
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
