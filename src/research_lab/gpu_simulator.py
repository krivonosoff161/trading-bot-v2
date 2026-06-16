# -*- coding: utf-8 -*-
"""Batched trade simulator (CPU numpy / GPU cupy) with exact parity to the scalar.

The path-dependent first-touch barrier scan (which bar, if any, hits stop or take
first) is the heavy inner loop of a sweep. This module batches that scan across all
signals of one variant on a numpy/cupy ``xp`` array backend, then hands the decided
exit back to the SAME ``finalize_trade`` the CPU path uses. The result is
bit-identical to ``experiment.simulate_trades`` by construction:

  * Every per-signal scalar (entry, stop/take levels, enable flags, exit_idx, the
    skip filter) is computed on the HOST in pure CPython float64, exactly like the
    CPU reference (including the ``if stop``/``if take`` float-truthiness that
    DISABLES a level that lands on 0.0, e.g. a long stop_pct==100).
  * The GPU does ONLY the (B x (H+1)) boolean barrier comparison + first-touch
    search, fed those host-computed float64 levels (no device-side level math, no
    device reductions for MFE/MAE) so a boundary price can never flip vs the host.
  * ``finalize_trade`` (the shared CPU arithmetic) builds every trade dict, so
    ret/net/mfe/mae/capture/rounding/order match the CPU exactly.

Supported mode: fixed stop-loss / take-profit / max-holding (long & short) with
fees+slippage. Any other exit modifier is reported unsupported -> CPU fallback.
A batch whose (B x (H+1)) matrix would exceed the VRAM cap also falls back to CPU.

No live trading, no order engine, no secrets.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.experiment import finalize_trade

# Exit-mode params the batched simulator faithfully reproduces. Anything else is
# an unsupported mode (honest CPU fallback, never a silent wrong GPU result).
SUPPORTED_EXIT_PARAMS = frozenset({"hold_bars", "stop_pct", "take_pct"})
# Param keys that change the exit logic in ways this simulator does NOT model.
UNSUPPORTED_EXIT_PARAMS = frozenset({
    "trailing_stop_pct", "trail_pct", "atr_stop_mult", "atr_take_mult",
    "exit_mode", "time_stop_bars_atr", "breakeven_pct", "partial_take_pct",
})

# Conservative VRAM cap for the (B x (H+1)) batch on a shared 3GB GTX 1050:
# ~28 bytes/cell (int64 bar idx + 2 float64 gathered prices + 4 bool masks),
# 256 MiB named-set budget keeps realistic peak (pools+context+temporaries) well
# inside what is free after the Windows desktop. See gpu-simulator-design audit.
GPU_SIM_MEMORY_CAP_BYTES = 256 * 1024 * 1024
GPU_SIM_BYTES_PER_CELL = 28
GPU_SIM_MEMORY_CAP_CELLS = GPU_SIM_MEMORY_CAP_BYTES // GPU_SIM_BYTES_PER_CELL  # 9_586_980


def supported_simulation_mode(params: dict[str, Any]) -> tuple[bool, str]:
    """Is this exit mode reproducible on the batched simulator? (supported, reason)."""
    for key in UNSUPPORTED_EXIT_PARAMS:
        if key in params:
            return False, f"unsupported_exit_mode:{key}"
    return True, ""


def estimate_batch_cells(n_signals: int, hold_bars: int) -> int:
    return max(0, int(n_signals)) * (max(0, int(hold_bars)) + 1)


def within_memory_cap(n_signals: int, hold_bars: int) -> bool:
    return estimate_batch_cells(n_signals, hold_bars) <= GPU_SIM_MEMORY_CAP_CELLS


def _host_setup(candles, signals, hold_bars, stop_pct, take_pct):
    """Per-signal scalars mirroring the CPU reference EXACTLY (incl. skip filter).

    Returns (kept_signals, idx, is_long, entry, exit_idx, stop_level, take_level,
    stop_enabled, take_enabled) where the arrays are plain python lists aligned to
    kept_signals (original signal order preserved).
    """
    n = len(candles)
    kept, idxs, is_long, entry, exit_idx = [], [], [], [], []
    stop_level, take_level, stop_en, take_en = [], [], [], []
    for sig in signals:
        idx = int(sig["idx"])
        ex = min(idx + hold_bars, n - 1)
        if idx >= n or ex <= idx:  # CPU line 201
            continue
        e = float(candles[idx]["open"])
        if e <= 0:  # CPU line 205 (NaN<=0 is False -> kept, matching CPU)
            continue
        long = str(sig["side"]) == "long"
        if long:
            s_lvl = e * (1 - stop_pct / 100) if stop_pct > 0 else 0.0
            t_lvl = e * (1 + take_pct / 100) if take_pct > 0 else 0.0
        else:
            s_lvl = e * (1 + stop_pct / 100) if stop_pct > 0 else 0.0
            t_lvl = e * (1 - take_pct / 100) if take_pct > 0 else 0.0
        kept.append(sig)
        idxs.append(idx)
        is_long.append(long)
        entry.append(e)
        exit_idx.append(ex)
        stop_level.append(s_lvl)
        take_level.append(t_lvl)
        # bool(level) reproduces CPU `if stop`/`if take`: a level of 0.0 disables it.
        stop_en.append(bool(stop_pct > 0 and s_lvl))
        take_en.append(bool(take_pct > 0 and t_lvl))
    return kept, idxs, is_long, entry, exit_idx, stop_level, take_level, stop_en, take_en


def _first_touch_batched(
    highs, lows, *, idxs, is_long, exit_idx, stop_level, take_level,
    stop_en, take_en, hold_bars, xp,
):
    """Return host arrays (touched, first_k, is_stop) for the batch via xp.

    GPU/numpy does only the (B x (H+1)) boolean comparison + first-true search,
    using the host-computed float64 levels (no device-side level arithmetic).
    """
    n = highs.shape[0]
    b = len(idxs)
    h = int(hold_bars)
    idx_a = xp.asarray(idxs, dtype=xp.int64)[:, None]            # (B,1)
    exit_a = xp.asarray(exit_idx, dtype=xp.int64)[:, None]        # (B,1)
    long_a = xp.asarray(is_long, dtype=bool)[:, None]            # (B,1)
    s_lvl = xp.asarray(stop_level, dtype=xp.float64)[:, None]     # (B,1)
    t_lvl = xp.asarray(take_level, dtype=xp.float64)[:, None]     # (B,1)
    s_en = xp.asarray(stop_en, dtype=bool)[:, None]              # (B,1)
    t_en = xp.asarray(take_en, dtype=bool)[:, None]              # (B,1)

    k = xp.arange(h + 1, dtype=xp.int64)[None, :]                # (1,H+1)
    bar = idx_a + k                                              # (B,H+1) unclamped
    valid = bar <= exit_a                                        # exclude bars past exit
    bar_c = xp.minimum(bar, n - 1)                              # clamp for safe gather
    hi = highs[bar_c]                                           # (B,H+1)
    lo = lows[bar_c]

    stop_hit = valid & s_en & xp.where(long_a, lo <= s_lvl, hi >= s_lvl)
    take_hit = valid & t_en & xp.where(long_a, hi >= t_lvl, lo <= t_lvl)
    any_touch = stop_hit | take_hit

    touched = any_touch.any(axis=1)                            # (B,)
    first_k = any_touch.argmax(axis=1)                         # first True (0 if none)
    rows = xp.arange(b)
    is_stop = stop_hit[rows, first_k]                          # stop priority on tie

    def to_host(a):
        return a.get() if hasattr(a, "get") else a

    return to_host(touched), to_host(first_k), to_host(is_stop)


def simulate_trades_batched(
    candles: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    params: dict[str, Any],
    *,
    fees_bps: float,
    slippage_bps: float,
    xp,
) -> list[dict[str, Any]]:
    """Batched equivalent of experiment.simulate_trades for the supported exit mode.

    Caller must ensure supported_simulation_mode(params) and within_memory_cap(...)
    first; this function assumes the supported mode.
    """
    hold_bars = int(params.get("hold_bars", 5))
    stop_pct = float(params.get("stop_pct", 0.0))
    take_pct = float(params.get("take_pct", 0.0))
    cost_pct = (fees_bps + slippage_bps) / 10000.0

    (kept, idxs, is_long, entry, exit_idx,
     stop_level, take_level, stop_en, take_en) = _host_setup(
        candles, signals, hold_bars, stop_pct, take_pct,
    )
    if not kept:
        return []

    highs = xp.asarray([float(c["high"]) for c in candles], dtype=xp.float64)
    lows = xp.asarray([float(c["low"]) for c in candles], dtype=xp.float64)
    touched, first_k, is_stop = _first_touch_batched(
        highs, lows, idxs=idxs, is_long=is_long, exit_idx=exit_idx,
        stop_level=stop_level, take_level=take_level, stop_en=stop_en,
        take_en=take_en, hold_bars=hold_bars, xp=xp,
    )

    trades: list[dict[str, Any]] = []
    for b, sig in enumerate(kept):
        side = "long" if is_long[b] else "short"
        if bool(touched[b]):
            actual_exit_idx = idxs[b] + int(first_k[b])
            if bool(is_stop[b]):
                outcome, exit_price = "stop", stop_level[b]
            else:
                outcome, exit_price = "take", take_level[b]
        else:
            actual_exit_idx = exit_idx[b]
            outcome, exit_price = "time_exit", float(candles[exit_idx[b]]["close"])
        trades.append(
            finalize_trade(candles, idxs[b], actual_exit_idx, side, entry[b],
                           exit_price, outcome, sig, cost_pct)
        )
    return trades
