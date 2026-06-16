# -*- coding: utf-8 -*-
"""Array-backend numeric kernels for the sweep core (CPU numpy / GPU cupy).

The rolling-window breakout computation is the genuinely data-parallel part of a
sweep: for one symbol it evaluates the breakout condition on every bar at once.
The same code runs on numpy (CPU) or cupy (GPU) by swapping the array module
``xp`` — and produces bit-identical signals to the scalar reference in
``strategies/breakout.py`` (guaranteed by a parity test).

This is the accelerable layer. The path-dependent trade simulation
(``simulate_trades``) stays on the CPU because it is sequential by nature; the
GPU support matrix is therefore limited to the families listed in
``gpu_runtime.GPU_SUPPORTED_FAMILIES``.
"""

from __future__ import annotations

from typing import Any


def _to_host(mask):
    """Move a cupy/numpy boolean (or float) array to a host numpy array."""
    get = getattr(mask, "get", None)
    if callable(get):  # cupy ndarray
        return get()
    return mask


def momentum_breakout_signals(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    lookback: int,
    threshold_pct: float,
    xp,
) -> list[dict[str, Any]]:
    """Vectorized equivalent of strategies.signals_momentum_breakout.

    Decision is made on bar ``d`` using the window ``[d-lookback, d)`` (strictly
    before ``d``); entry is at ``d+1``. Long takes priority over short on the same
    bar, exactly like the scalar reference. ``xp`` is numpy (CPU) or cupy (GPU).
    """
    n = len(closes)
    lookback = int(lookback)
    if lookback <= 0 or n <= lookback + 1:
        return []

    h = xp.asarray(highs, dtype=xp.float64)
    low_arr = xp.asarray(lows, dtype=xp.float64)
    c = xp.asarray(closes, dtype=xp.float64)

    # Decision bars d in range(lookback, n-1) (matches range(lookback, len-1)).
    decision = xp.arange(lookback, n - 1)
    # Window index matrix: row d -> [d-lookback, .., d-1].
    win = decision[:, None] - lookback + xp.arange(lookback)[None, :]
    window_high = h[win].max(axis=1)
    window_low = low_arr[win].min(axis=1)
    close_d = c[decision]

    long_mult = 1.0 + float(threshold_pct) / 100.0
    short_mult = 1.0 - float(threshold_pct) / 100.0
    long_mask = close_d > window_high * long_mult
    short_mask = (~long_mask) & (close_d < window_low * short_mult)

    # Build host-side signal dicts in ascending decision-bar order.
    decision_h = _to_host(decision)
    long_h = _to_host(long_mask)
    short_h = _to_host(short_mask)

    signals: list[dict[str, Any]] = []
    for i in range(len(decision_h)):
        d = int(decision_h[i])
        if bool(long_h[i]):
            signals.append({"idx": d + 1, "side": "long", "reason": "breakout_high"})
        elif bool(short_h[i]):
            signals.append({"idx": d + 1, "side": "short", "reason": "breakout_low"})
    return signals


# Dispatch table: family -> vectorized kernel. Extend the support matrix here.
KERNELS = {
    "momentum_breakout": momentum_breakout_signals,
}


def supported_family(family: str) -> bool:
    return family in KERNELS


def generate_signals_vectorized(
    candles: list[dict[str, Any]],
    family: str,
    params: dict[str, Any],
    *,
    xp,
) -> list[dict[str, Any]]:
    """Run the vectorized kernel for a supported family. Raises if unsupported."""
    kernel = KERNELS.get(family)
    if kernel is None:
        raise KeyError(f"no vectorized kernel for family '{family}'")
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    if family == "momentum_breakout":
        return kernel(
            highs, lows, closes,
            lookback=int(params.get("lookback", 20)),
            threshold_pct=float(params.get("threshold_pct", 0.0)),
            xp=xp,
        )
    raise KeyError(f"no vectorized dispatch for family '{family}'")  # pragma: no cover
