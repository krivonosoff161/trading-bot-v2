# -*- coding: utf-8 -*-
"""Signal-family registry — deterministic geometry per hypothesis (research-only, no order path).

The first lane only did continuation on extended movers, which reverted. This adds distinct families with
a key guard against "entering at the end of a move": continuation/pullback REQUIRE the move to NOT be
exhausted (and a pullback present), while reversal-fade REQUIRES exhaustion (that IS its setup). Every
non-tactical family must clear RR>=2; tactical early-TP is explicitly allowed a tighter target.

Each builder returns (PaperActionSignal | None, reason). All geometry uses bars up to the decision
boundary only — no look-ahead.
"""
from __future__ import annotations

from typing import Any

from src.research_lab.paper_signals.contract import PaperActionSignal, validate_signal
from src.research_lab.paper_signals.lane import (
    ARM_WINDOW_BARS, HORIZON_BARS, MAX_RISK_PCT, STOP_ATR_MULT, TF_MINUTES, TREND_LOOKBACK,
    _atr, _round, _swing_high, _swing_low, fingerprint,
)

EXHAUSTION_ATR = 3.0            # a run of >= this many ATR over TREND_LOOKBACK bars = "extended/exhausted"
MIN_RR = 2.0                   # non-tactical families must offer >= this reward:risk at tp1

GEOMETRY_PROFILES = {
    "base": {
        "id": "base",
        "entry_scale": 1.0,
        "stop_scale": 1.0,
        "tp_scale": 1.0,
        "hold_scale": 1.0,
        "reason": "default deterministic family geometry",
    },
    "stop_relief": {
        "id": "stop_relief",
        "entry_scale": 1.0,
        "stop_scale": 1.25,
        "tp_scale": 1.0,
        "hold_scale": 1.0,
        "reason": "memory says prior exits often looked stop-too-tight",
    },
    "faster_capture": {
        "id": "faster_capture",
        "entry_scale": 0.9,
        "stop_scale": 1.0,
        "tp_scale": 0.75,
        "hold_scale": 0.75,
        "reason": "memory says favorable moves were often given back",
    },
    "runner_probe": {
        "id": "runner_probe",
        "entry_scale": 1.0,
        "stop_scale": 1.1,
        "tp_scale": 1.35,
        "hold_scale": 1.5,
        "reason": "memory says this cell can justify a longer capture probe",
    },
}


def _profile(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(value, dict):
        profile_id = str(value.get("id") or value.get("profile_id") or "base")
        base = dict(GEOMETRY_PROFILES.get(profile_id, GEOMETRY_PROFILES["base"]))
        for key in ("entry_scale", "stop_scale", "tp_scale", "hold_scale", "reason"):
            if key in value:
                base[key] = value[key]
        base["id"] = profile_id if profile_id in GEOMETRY_PROFILES else "base"
        return base
    return dict(GEOMETRY_PROFILES.get(str(value or "base"), GEOMETRY_PROFILES["base"]))


def _scale(value: float, profile: dict[str, Any], key: str, *, floor: float = 0.1, cap: float = 3.0) -> float:
    try:
        mult = float(profile.get(key) or 1.0)
    except (TypeError, ValueError):
        mult = 1.0
    return value * max(floor, min(cap, mult))


def _tp_mults(values: list[float], profile: dict[str, Any], *, tactical: bool) -> list[float]:
    out = [round(_scale(v, profile, "tp_scale", floor=0.5, cap=2.0), 4) for v in values]
    if not tactical and out:
        out[0] = max(MIN_RR, out[0])
    return out


def _profile_suffix(profile: dict[str, Any]) -> str:
    profile_id = str(profile.get("id") or "base")
    return "" if profile_id == "base" else f"_{profile_id}"


def _extended(candles: list[dict[str, Any]], atr: float) -> tuple[bool, float]:
    """Is price extended (entry_after_move)? run = |close - close[-lookback]| in ATR units."""
    if atr <= 0 or len(candles) <= TREND_LOOKBACK:
        return False, 0.0
    run = abs(float(candles[-1]["close"]) - float(candles[-1 - TREND_LOOKBACK]["close"])) / atr
    return run >= EXHAUSTION_ATR, round(run, 2)


def _pulled_back(candles: list[dict[str, Any]], side: str, atr: float) -> bool:
    """For a long: has price pulled back >= 0.4 ATR off the recent high (a dip to buy)? Mirror for short."""
    if atr <= 0:
        return False
    c = float(candles[-1]["close"])
    if side == "long":
        return (_swing_high(candles, 6) - c) >= 0.4 * atr
    return (c - _swing_low(candles, 6)) >= 0.4 * atr


def _assemble(symbol, inst, tf, candles, *, side, entry_lo, entry_hi, stop, tp_mults, family, reason,
              mover, now, boundary_ts, mode, tactical=False, extra_risk_note="",
              geometry_profile=None) -> tuple[PaperActionSignal | None, str]:
    profile = _profile(geometry_profile)
    price = float(candles[-1]["close"])
    long_ = side == "long"
    risk = (entry_lo - stop) if long_ else (stop - entry_hi)
    if risk <= 0:
        return None, "non_positive_risk"
    risk_pct = round(risk / price * 100, 3)
    if risk_pct > MAX_RISK_PCT:
        return None, f"risk_too_wide_{risk_pct}pct"
    ref = entry_hi if long_ else entry_lo
    scaled_tps = _tp_mults(list(tp_mults), profile, tactical=tactical)
    tps = [{"label": f"tp{i+1}", "price": _round(ref + (m * risk if long_ else -m * risk), price),
            "size_frac": round(1.0 / len(scaled_tps), 2)} for i, m in enumerate(scaled_tps)]
    rr1 = scaled_tps[0]
    if not tactical and rr1 < MIN_RR:
        return None, f"rr_below_2_{rr1}"
    tf_min = TF_MINUTES.get(tf, 15)
    hold = HORIZON_BARS.get(tf, 12)
    if tactical:
        hold = max(3, hold // 3)               # tactical = fast in/out
    hold = max(2, int(round(_scale(hold, profile, "hold_scale", floor=0.5, cap=2.0))))
    fp = fingerprint(candles)
    suffix = _profile_suffix(profile)
    profile_id = str(profile.get("id") or "base")
    sig = PaperActionSignal(
        signal_id=f"{symbol}_{tf}_{family}{suffix}_{fp}", source="farm", symbol=symbol, okx_inst_id=inst,
        timeframe=tf, side=side, setup_family=family, entry_zone=[entry_lo, entry_hi], stop_loss=stop,
        invalidation_rule=(f"close beyond {stop}, OR no fill within {ARM_WINDOW_BARS} bars, OR no tp1 "
                           f"within {hold} bars"),
        take_profit_plan=tps, max_hold_bars=hold, max_hold_minutes=hold * tf_min, reason_now=reason,
        risk_notes=(("tactical early-TP lane; " if tactical else "") + extra_risk_note
                    + f"geometry_profile={profile_id}; paper-only watch"),
        validator_context={
            "geometry_profile_id": profile_id,
            "geometry_profile_reason": str(profile.get("reason") or ""),
            "geometry_entry_scale": float(profile.get("entry_scale") or 1.0),
            "geometry_stop_scale": float(profile.get("stop_scale") or 1.0),
            "geometry_tp_scale": float(profile.get("tp_scale") or 1.0),
            "geometry_hold_scale": float(profile.get("hold_scale") or 1.0),
        },
        status="armed", created_at=now, expires_at=now + ARM_WINDOW_BARS * tf_min * 60, ref_price=price,
        risk_pct=risk_pct, boundary_ts=boundary_ts, data_fingerprint=fp,
        dedup_key=(f"{symbol}|{tf}|{family}" if profile_id == "base" else f"{symbol}|{tf}|{family}|{profile_id}"),
        mode=mode)
    ok, problems = validate_signal(sig)
    if not ok:
        return None, "failed_validate:" + ";".join(problems)
    return sig, "ok"


def _ctx(candles):
    atr = _atr(candles)
    price = float(candles[-1]["close"])
    trend = price - float(candles[-1 - TREND_LOOKBACK]["close"])
    return atr, price, trend


def build_continuation(symbol, inst, tf, candles, **kw):
    profile = _profile(kw.pop("geometry_profile", None))
    atr, price, trend = _ctx(candles)
    if atr <= 0 or trend == 0:
        return None, "no_trend_or_flat"
    ext, _ = _extended(candles, atr)
    if ext:
        return None, "entry_after_move_exhausted"      # do NOT chase the end of a run
    side = "long" if trend > 0 else "short"
    entry_atr = _scale(0.5 * atr, profile, "entry_scale", floor=0.5, cap=1.5)
    stop_atr = _scale(STOP_ATR_MULT * atr, profile, "stop_scale", floor=0.75, cap=1.5)
    struct_atr = _scale(0.2 * atr, profile, "stop_scale", floor=0.75, cap=1.5)
    lo = _round(price - entry_atr, price) if side == "long" else _round(price, price)
    hi = _round(price, price) if side == "long" else _round(price + entry_atr, price)
    stop = (_round(max(_swing_low(candles) - struct_atr, lo - stop_atr), price) if side == "long"
            else _round(min(_swing_high(candles) + struct_atr, hi + stop_atr), price))
    return _assemble(symbol, inst, tf, candles, side=side, entry_lo=lo, entry_hi=hi, stop=stop,
                     tp_mults=[2.0, 3.0], family="continuation",
                     reason=f"{side} continuation, not exhausted; trend over {TREND_LOOKBACK} bars",
                     geometry_profile=profile, **kw)


def build_pullback(symbol, inst, tf, candles, **kw):
    profile = _profile(kw.pop("geometry_profile", None))
    atr, price, trend = _ctx(candles)
    if atr <= 0 or trend == 0:
        return None, "no_trend_or_flat"
    side = "long" if trend > 0 else "short"
    if not _pulled_back(candles, side, atr):
        return None, "no_pullback_yet"
    entry_atr = _scale(0.3 * atr, profile, "entry_scale", floor=0.5, cap=1.5)
    struct_atr = _scale(0.2 * atr, profile, "stop_scale", floor=0.75, cap=1.5)
    lo = _round(price - entry_atr, price) if side == "long" else _round(price, price)
    hi = _round(price, price) if side == "long" else _round(price + entry_atr, price)
    stop = (_round(_swing_low(candles, 6) - struct_atr, price) if side == "long"
            else _round(_swing_high(candles, 6) + struct_atr, price))
    return _assemble(symbol, inst, tf, candles, side=side, entry_lo=lo, entry_hi=hi, stop=stop,
                     tp_mults=[2.0, 3.5], family="pullback_continuation",
                     reason=f"{side} pullback-continuation; dip into trend",
                     geometry_profile=profile, **kw)


def build_fade(symbol, inst, tf, candles, **kw):
    profile = _profile(kw.pop("geometry_profile", None))
    atr, price, trend = _ctx(candles)
    if atr <= 0:
        return None, "flat_atr"
    ext, run = _extended(candles, atr)
    if not ext:
        return None, "not_extended_no_fade"            # fade REQUIRES exhaustion
    side = "short" if trend > 0 else "long"            # fade against the thrust
    band_atr = _scale(0.3 * atr, profile, "entry_scale", floor=0.5, cap=1.5)
    stop_atr = _scale(0.3 * atr, profile, "stop_scale", floor=0.75, cap=1.5)
    lo = _round(price, price) if side == "short" else _round(price - band_atr, price)
    hi = _round(price + band_atr, price) if side == "short" else _round(price, price)
    stop = (_round(_swing_high(candles, 4) + stop_atr, price) if side == "short"
            else _round(_swing_low(candles, 4) - stop_atr, price))
    return _assemble(symbol, inst, tf, candles, side=side, entry_lo=lo, entry_hi=hi, stop=stop,
                     tp_mults=[1.0, 1.5], family="reversal_fade", tactical=True,
                     reason=f"fade exhaustion (run {run} ATR); tactical mean-revert",
                     geometry_profile=profile, **kw)


def build_sweep_reclaim(symbol, inst, tf, candles, **kw):
    profile = _profile(kw.pop("geometry_profile", None))
    atr, price, _ = _ctx(candles)
    if atr <= 0 or len(candles) < 12:
        return None, "insufficient"
    prev_low, prev_high = _swing_low(candles[:-1], 10), _swing_high(candles[:-1], 10)
    last = candles[-1]
    lo_b, cl = float(last["low"]), float(last["close"])
    hi_b = float(last["high"])
    # bullish sweep: dipped below prior low then closed back above it
    entry_atr = _scale(0.2 * atr, profile, "entry_scale", floor=0.5, cap=1.5)
    stop_atr = _scale(0.2 * atr, profile, "stop_scale", floor=0.75, cap=1.5)
    if lo_b < prev_low <= cl:
        side, lo, hi = "long", _round(cl - entry_atr, price), _round(cl, price)
        stop = _round(lo_b - stop_atr, price)
    elif hi_b > prev_high >= cl:
        side, lo, hi = "short", _round(cl, price), _round(cl + entry_atr, price)
        stop = _round(hi_b + stop_atr, price)
    else:
        return None, "no_sweep_reclaim"
    return _assemble(symbol, inst, tf, candles, side=side, entry_lo=lo, entry_hi=hi, stop=stop,
                     tp_mults=[2.0, 3.0], family="liquidity_sweep_reclaim",
                     reason=f"{side} liquidity-sweep + reclaim of structure",
                     geometry_profile=profile, **kw)


def build_early_tp(symbol, inst, tf, candles, **kw):
    profile = _profile(kw.pop("geometry_profile", None))
    atr, price, trend = _ctx(candles)
    if atr <= 0 or trend == 0:
        return None, "no_trend_or_flat"
    ext, _ = _extended(candles, atr)
    if ext:
        return None, "entry_after_move_exhausted"
    side = "long" if trend > 0 else "short"
    entry_atr = _scale(0.3 * atr, profile, "entry_scale", floor=0.5, cap=1.5)
    stop_atr = _scale(0.8 * atr, profile, "stop_scale", floor=0.75, cap=1.5)
    lo = _round(price - entry_atr, price) if side == "long" else _round(price, price)
    hi = _round(price, price) if side == "long" else _round(price + entry_atr, price)
    stop = (_round(lo - stop_atr, price) if side == "long" else _round(hi + stop_atr, price))
    return _assemble(symbol, inst, tf, candles, side=side, entry_lo=lo, entry_hi=hi, stop=stop,
                     tp_mults=[1.0], family="early_tp_tactical", tactical=True,
                     reason=f"{side} tactical early-TP scalp; fast in/out",
                     geometry_profile=profile, **kw)


FAMILIES = {
    "continuation": build_continuation,
    "pullback_continuation": build_pullback,
    "reversal_fade": build_fade,
    "liquidity_sweep_reclaim": build_sweep_reclaim,
    "early_tp_tactical": build_early_tp,
}

# Structural metadata so the farm/calculator can read families as DATA (not from a prompt). Each entry:
# when applicable, timeframes, entry/stop/TP logic, invalidation, required data, class, failure modes.
FAMILY_META = {
    "continuation": {
        "class": "statistical_candidate", "when": "trending, NOT exhausted (<3 ATR run)",
        "timeframes": ["15m", "1h", "4h"], "entry": "shallow 0.5-ATR pullback limit in trend direction",
        "stop": "structure swing or ATR-bounded (1.2 ATR), tighter of the two", "tp": "1R/2R partial+BE",
        "invalidation": "close beyond stop / no fill in arm window / no tp1 in max_hold",
        "required_data": ["candles"], "failure_modes": ["entry_after_exhaustion", "no_follow_through"]},
    "pullback_continuation": {
        "class": "statistical_candidate", "when": "trend with a >=0.4-ATR pullback already printed",
        "timeframes": ["15m", "1h", "4h"], "entry": "0.3-ATR dip limit", "stop": "recent swing extreme",
        "tp": "2R/3.5R partial+BE", "invalidation": "close beyond stop / no fill / no tp1",
        "required_data": ["candles"], "failure_modes": ["missed_pullback", "wrong_direction"]},
    "reversal_fade": {
        "class": "tactical", "when": "EXHAUSTED (>=3 ATR run); fade the thrust",
        "timeframes": ["15m", "1h"], "entry": "fade at extreme", "stop": "beyond the swing extreme",
        "tp": "1R/1.5R (tactical, tighter)", "invalidation": "trend resumes / no fill",
        "required_data": ["candles"], "failure_modes": ["trend_continues", "bad_exit_gave_back"]},
    "liquidity_sweep_reclaim": {
        "class": "statistical_candidate", "when": "sweep of prior swing then reclaim (close back inside)",
        "timeframes": ["15m", "1h", "4h"], "entry": "in reclaim direction", "stop": "beyond the sweep wick",
        "tp": "2R/3R partial+BE", "invalidation": "no reclaim / close beyond sweep",
        "required_data": ["candles"], "failure_modes": ["false_reclaim", "no_follow_through"]},
    "early_tp_tactical": {
        "class": "tactical", "when": "trend, not exhausted; fast scalp", "timeframes": ["15m", "1h"],
        "entry": "0.3-ATR limit", "stop": "0.8 ATR", "tp": "1R single (fast, fixed)",
        "invalidation": "stop / no fill / timeout (short hold)", "required_data": ["candles"],
        "failure_modes": ["stop_too_tight", "wrong_direction"]},
    "watch_only": {
        "class": "no_trade", "when": "flat ATR or choppy (|trend|<0.5 ATR)", "timeframes": ["any"],
        "entry": "none", "stop": "none", "tp": "none", "invalidation": "n/a",
        "required_data": ["candles"], "failure_modes": ["n/a"]},
}


def watch_only_reason(candles: list[dict[str, Any]]) -> str | None:
    """Return a no-trade reason when the tape is too quiet/choppy to act (the watch-only family)."""
    atr, price, trend = _ctx(candles)
    if atr <= 0:
        return "watch_only_flat_atr"
    if abs(trend) / atr < 0.5:
        return "watch_only_choppy_no_trend"
    return None


def generate(symbol, inst, tf, candles, *, mover, now, boundary_ts, mode,
             families=None, geometry_profiles=None) -> list[tuple[PaperActionSignal, str]]:
    """Run the requested families over one candidate; return the signals that fire.

    Profiles are interleaved by rank: first every family gets its base/primary
    profile, then secondary probes are attempted.  This keeps a small max_new
    cap from spending all slots on one family before the rest are tested.
    """
    out = []
    names = [name for name in (families or list(FAMILIES)) if name in FAMILIES]
    profile_map: dict[str, list[str]] = {}
    max_depth = 1
    for name in names:
        profiles = None
        if isinstance(geometry_profiles, dict):
            profiles = geometry_profiles.get(name) or geometry_profiles.get("*")
        if not profiles:
            profiles = ["base"]
        selected = list(profiles)[:2]
        profile_map[name] = selected
        max_depth = max(max_depth, len(selected))
    for idx in range(max_depth):
        for name in names:
            profiles = profile_map.get(name) or ["base"]
            if idx >= len(profiles):
                continue
            builder = FAMILIES.get(name)
            if builder is None:
                continue
            profile_id = profiles[idx]
            sig, _why = builder(symbol, inst, tf, candles, mover=mover, now=now,
                                boundary_ts=boundary_ts, mode=mode,
                                geometry_profile=profile_id)
            if sig is not None:
                out.append((sig, name))
    return out
