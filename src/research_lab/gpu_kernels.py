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


def prepare_candle_arrays(candles: list[dict[str, Any]], *, xp) -> dict[str, Any]:
    """Upload one candle packet once and reuse it across all parameter variants."""
    return {
        "opens": xp.asarray([float(c["open"]) for c in candles], dtype=xp.float64),
        "highs": xp.asarray([float(c["high"]) for c in candles], dtype=xp.float64),
        "lows": xp.asarray([float(c["low"]) for c in candles], dtype=xp.float64),
        "closes": xp.asarray([float(c["close"]) for c in candles], dtype=xp.float64),
        "vols": xp.asarray([float(c.get("vol") or 0.0) for c in candles], dtype=xp.float64),
    }


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


def donchian_breakout_signals(highs, lows, *, lookback: int, xp) -> list[dict[str, Any]]:
    n, lookback = len(highs), int(lookback)
    if lookback <= 0 or n <= lookback + 1:
        return []
    h = xp.asarray(highs, dtype=xp.float64)
    low_arr = xp.asarray(lows, dtype=xp.float64)
    d = xp.arange(lookback, n - 1)
    win = d[:, None] - lookback + xp.arange(lookback)[None, :]
    long_mask = h[d] > h[win].max(axis=1)
    short_mask = (~long_mask) & (low_arr[d] < low_arr[win].min(axis=1))
    return _breakout_signals(
        _to_host(d), _to_host(long_mask), _to_host(short_mask),
        "donchian_pierce_high", "donchian_pierce_low",
    )


def range_breakout_signals(highs, lows, closes, *, lookback: int, max_range_pct: float, xp):
    n, lookback = len(closes), int(lookback)
    if lookback <= 0 or n <= lookback + 1:
        return []
    h = xp.asarray(highs, dtype=xp.float64)
    low_arr = xp.asarray(lows, dtype=xp.float64)
    c = xp.asarray(closes, dtype=xp.float64)
    d = xp.arange(lookback, n - 1)
    win = d[:, None] - lookback + xp.arange(lookback)[None, :]
    win_high, win_low = h[win].max(axis=1), low_arr[win].min(axis=1)
    safe_low = xp.where(win_low > 0, win_low, 1.0)
    tight = (win_low > 0) & (((win_high / safe_low - 1.0) * 100.0) <= float(max_range_pct))
    long_mask = tight & (c[d] > win_high)
    short_mask = tight & (~long_mask) & (c[d] < win_low)
    return _breakout_signals(
        _to_host(d), _to_host(long_mask), _to_host(short_mask),
        "range_breakout_up", "range_breakout_down",
    )


def volatility_squeeze_breakout_signals(
    highs, lows, closes, *, lookback: int, squeeze_ratio: float, xp,
):
    n, lookback = len(closes), int(lookback)
    start = 2 * lookback
    if lookback <= 0 or n <= start + 1:
        return []
    h = xp.asarray(highs, dtype=xp.float64)
    low_arr = xp.asarray(lows, dtype=xp.float64)
    c = xp.asarray(closes, dtype=xp.float64)
    d = xp.arange(start, n - 1)
    offsets = xp.arange(lookback)[None, :]
    cur = d[:, None] - lookback + offsets
    prev = d[:, None] - 2 * lookback + offsets
    cur_high, cur_low = h[cur].max(axis=1), low_arr[cur].min(axis=1)
    prev_range = h[prev].max(axis=1) - low_arr[prev].min(axis=1)
    squeezed = (prev_range > 0) & ((cur_high - cur_low) <= prev_range * float(squeeze_ratio))
    long_mask = squeezed & (c[d] > cur_high)
    short_mask = squeezed & (~long_mask) & (c[d] < cur_low)
    return _breakout_signals(
        _to_host(d), _to_host(long_mask), _to_host(short_mask),
        "squeeze_break_up", "squeeze_break_down",
    )


def mean_reversion_fade_signals(closes, *, lookback: int, move_pct: float, xp):
    n, lookback = len(closes), int(lookback)
    if lookback <= 0 or n <= lookback + 1:
        return []
    c = xp.asarray(closes, dtype=xp.float64)
    d = xp.arange(lookback, n - 1)
    base = c[d - lookback]
    safe = xp.where(base > 0, base, 1.0)
    move = (c[d] / safe - 1.0) * 100.0
    valid = base > 0
    short_mask = valid & (move >= float(move_pct))
    long_mask = valid & (~short_mask) & (move <= -float(move_pct))
    # _breakout_signals takes long first; reasons are deliberately inverted for a fade.
    return _breakout_signals(
        _to_host(d), _to_host(long_mask), _to_host(short_mask),
        "fade_down_move", "fade_up_move",
    )


def volume_shock_continuation_signals(
    opens, closes, vols, *, lookback: int, vol_mult: float, min_body_pct: float, xp,
):
    n, lookback = len(closes), int(lookback)
    if lookback <= 0 or n <= lookback + 1:
        return []
    o = xp.asarray(opens, dtype=xp.float64)
    c = xp.asarray(closes, dtype=xp.float64)
    v = xp.asarray(vols, dtype=xp.float64)
    d = xp.arange(lookback, n - 1)
    win = d[:, None] - lookback + xp.arange(lookback)[None, :]
    avg = v[win].sum(axis=1) / lookback
    safe_o = xp.where(o[d] > 0, o[d], 1.0)
    body = (c[d] / safe_o - 1.0) * 100.0
    valid = (avg != 0) & (o[d] > 0) & (v[d] >= avg * float(vol_mult)) & (xp.abs(body) >= float(min_body_pct))
    long_mask = valid & (body > 0)
    short_mask = valid & (~long_mask)
    return _breakout_signals(
        _to_host(d), _to_host(long_mask), _to_host(short_mask), "volume_shock", "volume_shock",
    )


def impulse_continuation_signals(
    opens, highs, lows, closes, *, min_body_pct: float, min_close_pos: float, xp,
):
    n = len(closes)
    if n <= 2:
        return []
    o = xp.asarray(opens, dtype=xp.float64)
    h = xp.asarray(highs, dtype=xp.float64)
    low_arr = xp.asarray(lows, dtype=xp.float64)
    c = xp.asarray(closes, dtype=xp.float64)
    d = xp.arange(1, n - 1)
    safe_o = xp.where(o[d] > 0, o[d], 1.0)
    body = (c[d] / safe_o - 1.0) * 100.0
    rng = h[d] - low_arr[d]
    close_pos = xp.where(rng > 0, (c[d] - low_arr[d]) / xp.where(rng > 0, rng, 1.0), 0.5)
    valid = (o[d] > 0) & (rng > 0) & (xp.abs(body) >= float(min_body_pct))
    long_mask = valid & (body > 0) & (close_pos >= float(min_close_pos))
    short_mask = valid & (~long_mask) & (body < 0) & (close_pos <= 1.0 - float(min_close_pos))
    return _breakout_signals(
        _to_host(d), _to_host(long_mask), _to_host(short_mask),
        "impulse_up_strong_close", "impulse_down_weak_close",
    )


def range_volume_breakout_signals(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    vols: list[float],
    *,
    lookback: int,
    max_range_pct: float,
    min_accumulation: float,
    min_vol_ratio: float,
    vol_period: int,
    xp,
) -> list[dict[str, Any]]:
    """Vectorized equivalent of strategies.signals_range_volume_breakout.

    Mirrors the scalar gates exactly, including the round(.,4) applied to range_pct,
    accumulation and vol_ratio (so the gate booleans match the feature helpers).
    Decision on bar d, entry at d+1.
    """
    n = len(closes)
    lookback, vol_period = int(lookback), int(vol_period)
    start = max(2 * lookback, vol_period)
    if lookback <= 0 or n <= start + 1:
        return []
    h = xp.asarray(highs, dtype=xp.float64)
    low_arr = xp.asarray(lows, dtype=xp.float64)
    c = xp.asarray(closes, dtype=xp.float64)
    v = xp.asarray(vols, dtype=xp.float64)

    d = xp.arange(start, n - 1)
    cur_win = d[:, None] - lookback + xp.arange(lookback)[None, :]
    prev_win = d[:, None] - 2 * lookback + xp.arange(lookback)[None, :]
    vol_win = d[:, None] - vol_period + xp.arange(vol_period)[None, :]
    win_high = h[cur_win].max(axis=1)
    win_low = low_arr[cur_win].min(axis=1)
    window_vol = v[cur_win].sum(axis=1)
    prior_vol = v[prev_win].sum(axis=1)
    vol_base = v[vol_win].sum(axis=1) / vol_period
    c_d = c[d]

    safe_low = xp.where(win_low > 0, win_low, 1.0)
    range_pct = xp.round((win_high / safe_low - 1.0) * 100.0, 4)
    sideways_ok = (win_low > 0) & (range_pct <= max_range_pct)
    safe_prior = xp.where(prior_vol > 0, prior_vol, 1.0)
    accumulation = xp.round(window_vol / safe_prior, 4)
    accum_ok = (prior_vol > 0) & (accumulation >= min_accumulation)
    safe_base = xp.where(vol_base > 0, vol_base, 1.0)
    vol_ratio = xp.round(v[d] / safe_base, 4)
    vr_ok = (vol_base > 0) & (vol_ratio >= min_vol_ratio)

    all_ok = sideways_ok & accum_ok & vr_ok
    long_mask = all_ok & (c_d > win_high)
    short_mask = all_ok & (c_d < win_low)
    return _breakout_signals(
        _to_host(d), _to_host(long_mask), _to_host(short_mask),
        "range_accum_breakout_up", "range_accum_breakout_down",
    )


def pump_dump_scalp_signals(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    vols: list[float],
    *,
    min_body_pct: float,
    vol_mult: float,
    vol_period: int,
    min_close_pos: float,
    xp,
) -> list[dict[str, Any]]:
    """Vectorized equivalent of strategies.signals_pump_dump_scalp (no rounding in the
    scalar gates -> the only float-order-sensitive term is the volume baseline)."""
    n = len(closes)
    vol_period = int(vol_period)
    if vol_period <= 0 or n <= vol_period + 1:
        return []
    o = xp.asarray(opens, dtype=xp.float64)
    h = xp.asarray(highs, dtype=xp.float64)
    low_arr = xp.asarray(lows, dtype=xp.float64)
    c = xp.asarray(closes, dtype=xp.float64)
    v = xp.asarray(vols, dtype=xp.float64)

    d = xp.arange(vol_period, n - 1)
    vol_win = d[:, None] - vol_period + xp.arange(vol_period)[None, :]
    base = v[vol_win].sum(axis=1) / vol_period
    o_d, c_d, h_d, l_d = o[d], c[d], h[d], low_arr[d]

    safe_o = xp.where(o_d > 0, o_d, 1.0)
    body_pct = (c_d / safe_o - 1.0) * 100.0
    safe_base = xp.where(base > 0, base, 1.0)
    vol_ratio = v[d] / safe_base
    rng = h_d - l_d
    close_pos = xp.where(rng > 0, (c_d - l_d) / xp.where(rng > 0, rng, 1.0), 0.5)
    valid = (o_d > 0) & (base > 0) & (vol_ratio >= vol_mult)
    long_mask = valid & (body_pct >= min_body_pct) & (close_pos >= min_close_pos)
    short_mask = valid & (~long_mask) & (body_pct <= -min_body_pct) & (close_pos <= (1.0 - min_close_pos))
    return _breakout_signals(
        _to_host(d), _to_host(long_mask), _to_host(short_mask),
        "pump_volume_shock_up", "dump_volume_shock_down",
    )


def _breakout_signals(decision_h, long_h, short_h, up_reason: str, down_reason: str) -> list[dict[str, Any]]:
    """Build host-side {idx,side,reason} dicts from decision bars + long/short masks."""
    signals: list[dict[str, Any]] = []
    for i in range(len(decision_h)):
        d = int(decision_h[i])
        if bool(long_h[i]):
            signals.append({"idx": d + 1, "side": "long", "reason": up_reason})
        elif bool(short_h[i]):
            signals.append({"idx": d + 1, "side": "short", "reason": down_reason})
    return signals


# Dispatch table: family -> vectorized kernel. Extend the support matrix here.
KERNELS = {
    "momentum_breakout": momentum_breakout_signals,
    "donchian_breakout": donchian_breakout_signals,
    "range_breakout": range_breakout_signals,
    "volatility_squeeze_breakout": volatility_squeeze_breakout_signals,
    "mean_reversion_fade": mean_reversion_fade_signals,
    "volume_shock_continuation": volume_shock_continuation_signals,
    "impulse_continuation": impulse_continuation_signals,
    "range_volume_breakout": range_volume_breakout_signals,
    "pump_dump_scalp": pump_dump_scalp_signals,
}


def supported_family(family: str) -> bool:
    return family in KERNELS


def generate_signals_vectorized(
    candles: list[dict[str, Any]],
    family: str,
    params: dict[str, Any],
    *,
    xp,
    arrays: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run the vectorized kernel for a supported family. Raises if unsupported."""
    if family not in KERNELS:
        raise KeyError(f"no vectorized kernel for family '{family}'")
    prepared = arrays or prepare_candle_arrays(candles, xp=xp)
    highs = prepared["highs"]
    lows = prepared["lows"]
    closes = prepared["closes"]
    if family == "momentum_breakout":
        return momentum_breakout_signals(
            highs, lows, closes,
            lookback=int(params.get("lookback", 20)),
            threshold_pct=float(params.get("threshold_pct", 0.0)),
            xp=xp,
        )
    if family == "donchian_breakout":
        return donchian_breakout_signals(
            highs, lows, lookback=int(params.get("lookback", 20)), xp=xp,
        )
    if family == "range_breakout":
        return range_breakout_signals(
            highs, lows, closes,
            lookback=int(params.get("lookback", 30)),
            max_range_pct=float(params.get("max_range_pct", 12.0)), xp=xp,
        )
    if family == "volatility_squeeze_breakout":
        return volatility_squeeze_breakout_signals(
            highs, lows, closes,
            lookback=int(params.get("lookback", 20)),
            squeeze_ratio=float(params.get("squeeze_ratio", 0.6)), xp=xp,
        )
    if family == "mean_reversion_fade":
        return mean_reversion_fade_signals(
            closes, lookback=int(params.get("lookback", 5)),
            move_pct=float(params.get("move_pct", 8.0)), xp=xp,
        )
    vols = prepared["vols"]
    if family == "volume_shock_continuation":
        opens = prepared["opens"]
        return volume_shock_continuation_signals(
            opens, closes, vols,
            lookback=int(params.get("lookback", 20)),
            vol_mult=float(params.get("vol_mult", 2.0)),
            min_body_pct=float(params.get("min_body_pct", 3.0)), xp=xp,
        )
    if family == "impulse_continuation":
        opens = prepared["opens"]
        return impulse_continuation_signals(
            opens, highs, lows, closes,
            min_body_pct=float(params.get("min_body_pct", 6.0)),
            min_close_pos=float(params.get("min_close_pos", 0.7)), xp=xp,
        )
    if family == "range_volume_breakout":
        return range_volume_breakout_signals(
            highs, lows, closes, vols,
            lookback=int(params.get("range_lookback", 20)),
            max_range_pct=float(params.get("max_range_pct", 12.0)),
            min_accumulation=float(params.get("min_accumulation", 1.0)),
            min_vol_ratio=float(params.get("min_vol_ratio", 1.5)),
            vol_period=int(params.get("vol_period", 20)),
            xp=xp,
        )
    if family == "pump_dump_scalp":
        opens = prepared["opens"]
        return pump_dump_scalp_signals(
            opens, highs, lows, closes, vols,
            min_body_pct=float(params.get("min_body_pct", 3.0)),
            vol_mult=float(params.get("vol_mult", 3.0)),
            vol_period=int(params.get("vol_period", 20)),
            min_close_pos=float(params.get("min_close_pos", 0.6)),
            xp=xp,
        )
    raise KeyError(f"no vectorized dispatch for family '{family}'")  # pragma: no cover
