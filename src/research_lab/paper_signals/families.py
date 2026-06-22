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
              mover, now, boundary_ts, mode, tactical=False, extra_risk_note="") -> tuple[PaperActionSignal | None, str]:
    price = float(candles[-1]["close"])
    long_ = side == "long"
    risk = (entry_lo - stop) if long_ else (stop - entry_hi)
    if risk <= 0:
        return None, "non_positive_risk"
    risk_pct = round(risk / price * 100, 3)
    if risk_pct > MAX_RISK_PCT:
        return None, f"risk_too_wide_{risk_pct}pct"
    ref = entry_hi if long_ else entry_lo
    tps = [{"label": f"tp{i+1}", "price": _round(ref + (m * risk if long_ else -m * risk), price),
            "size_frac": round(1.0 / len(tp_mults), 2)} for i, m in enumerate(tp_mults)]
    rr1 = tp_mults[0]
    if not tactical and rr1 < MIN_RR:
        return None, f"rr_below_2_{rr1}"
    tf_min = TF_MINUTES.get(tf, 15)
    hold = HORIZON_BARS.get(tf, 12)
    if tactical:
        hold = max(3, hold // 3)               # tactical = fast in/out
    fp = fingerprint(candles)
    sig = PaperActionSignal(
        signal_id=f"{symbol}_{tf}_{family}_{fp}", source="farm", symbol=symbol, okx_inst_id=inst,
        timeframe=tf, side=side, setup_family=family, entry_zone=[entry_lo, entry_hi], stop_loss=stop,
        invalidation_rule=(f"close beyond {stop}, OR no fill within {ARM_WINDOW_BARS} bars, OR no tp1 "
                           f"within {hold} bars"),
        take_profit_plan=tps, max_hold_bars=hold, max_hold_minutes=hold * tf_min, reason_now=reason,
        risk_notes=("tactical early-TP lane; " if tactical else "") + extra_risk_note + "paper-only watch",
        status="armed", created_at=now, expires_at=now + ARM_WINDOW_BARS * tf_min * 60, ref_price=price,
        risk_pct=risk_pct, boundary_ts=boundary_ts, data_fingerprint=fp,
        dedup_key=f"{symbol}|{tf}|{family}", mode=mode)
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
    atr, price, trend = _ctx(candles)
    if atr <= 0 or trend == 0:
        return None, "no_trend_or_flat"
    ext, _ = _extended(candles, atr)
    if ext:
        return None, "entry_after_move_exhausted"      # do NOT chase the end of a run
    side = "long" if trend > 0 else "short"
    lo = _round(price - 0.5 * atr, price) if side == "long" else _round(price, price)
    hi = _round(price, price) if side == "long" else _round(price + 0.5 * atr, price)
    stop = (_round(max(_swing_low(candles) - 0.2 * atr, lo - STOP_ATR_MULT * atr), price) if side == "long"
            else _round(min(_swing_high(candles) + 0.2 * atr, hi + STOP_ATR_MULT * atr), price))
    return _assemble(symbol, inst, tf, candles, side=side, entry_lo=lo, entry_hi=hi, stop=stop,
                     tp_mults=[2.0, 3.0], family="continuation",
                     reason=f"{side} continuation, not exhausted; trend over {TREND_LOOKBACK} bars", **kw)


def build_pullback(symbol, inst, tf, candles, **kw):
    atr, price, trend = _ctx(candles)
    if atr <= 0 or trend == 0:
        return None, "no_trend_or_flat"
    side = "long" if trend > 0 else "short"
    if not _pulled_back(candles, side, atr):
        return None, "no_pullback_yet"
    lo = _round(price - 0.3 * atr, price) if side == "long" else _round(price, price)
    hi = _round(price, price) if side == "long" else _round(price + 0.3 * atr, price)
    stop = (_round(_swing_low(candles, 6) - 0.2 * atr, price) if side == "long"
            else _round(_swing_high(candles, 6) + 0.2 * atr, price))
    return _assemble(symbol, inst, tf, candles, side=side, entry_lo=lo, entry_hi=hi, stop=stop,
                     tp_mults=[2.0, 3.5], family="pullback_continuation",
                     reason=f"{side} pullback-continuation; dip into trend", **kw)


def build_fade(symbol, inst, tf, candles, **kw):
    atr, price, trend = _ctx(candles)
    if atr <= 0:
        return None, "flat_atr"
    ext, run = _extended(candles, atr)
    if not ext:
        return None, "not_extended_no_fade"            # fade REQUIRES exhaustion
    side = "short" if trend > 0 else "long"            # fade against the thrust
    lo = _round(price, price) if side == "short" else _round(price - 0.3 * atr, price)
    hi = _round(price + 0.3 * atr, price) if side == "short" else _round(price, price)
    stop = (_round(_swing_high(candles, 4) + 0.3 * atr, price) if side == "short"
            else _round(_swing_low(candles, 4) - 0.3 * atr, price))
    return _assemble(symbol, inst, tf, candles, side=side, entry_lo=lo, entry_hi=hi, stop=stop,
                     tp_mults=[1.0, 1.5], family="reversal_fade", tactical=True,
                     reason=f"fade exhaustion (run {run} ATR); tactical mean-revert", **kw)


def build_sweep_reclaim(symbol, inst, tf, candles, **kw):
    atr, price, _ = _ctx(candles)
    if atr <= 0 or len(candles) < 12:
        return None, "insufficient"
    prev_low, prev_high = _swing_low(candles[:-1], 10), _swing_high(candles[:-1], 10)
    last = candles[-1]
    lo_b, cl = float(last["low"]), float(last["close"])
    hi_b = float(last["high"])
    # bullish sweep: dipped below prior low then closed back above it
    if lo_b < prev_low <= cl:
        side, lo, hi = "long", _round(cl - 0.2 * atr, price), _round(cl, price)
        stop = _round(lo_b - 0.2 * atr, price)
    elif hi_b > prev_high >= cl:
        side, lo, hi = "short", _round(cl, price), _round(cl + 0.2 * atr, price)
        stop = _round(hi_b + 0.2 * atr, price)
    else:
        return None, "no_sweep_reclaim"
    return _assemble(symbol, inst, tf, candles, side=side, entry_lo=lo, entry_hi=hi, stop=stop,
                     tp_mults=[2.0, 3.0], family="liquidity_sweep_reclaim",
                     reason=f"{side} liquidity-sweep + reclaim of structure", **kw)


def build_early_tp(symbol, inst, tf, candles, **kw):
    atr, price, trend = _ctx(candles)
    if atr <= 0 or trend == 0:
        return None, "no_trend_or_flat"
    ext, _ = _extended(candles, atr)
    if ext:
        return None, "entry_after_move_exhausted"
    side = "long" if trend > 0 else "short"
    lo = _round(price - 0.3 * atr, price) if side == "long" else _round(price, price)
    hi = _round(price, price) if side == "long" else _round(price + 0.3 * atr, price)
    stop = (_round(lo - 0.8 * atr, price) if side == "long" else _round(hi + 0.8 * atr, price))
    return _assemble(symbol, inst, tf, candles, side=side, entry_lo=lo, entry_hi=hi, stop=stop,
                     tp_mults=[1.0], family="early_tp_tactical", tactical=True,
                     reason=f"{side} tactical early-TP scalp; fast in/out", **kw)


FAMILIES = {
    "continuation": build_continuation,
    "pullback_continuation": build_pullback,
    "reversal_fade": build_fade,
    "liquidity_sweep_reclaim": build_sweep_reclaim,
    "early_tp_tactical": build_early_tp,
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
             families=None) -> list[tuple[PaperActionSignal, str]]:
    """Run the requested families over one candidate; return the signals that fire."""
    out = []
    for name in (families or list(FAMILIES)):
        builder = FAMILIES.get(name)
        if builder is None:
            continue
        sig, _why = builder(symbol, inst, tf, candles, mover=mover, now=now,
                            boundary_ts=boundary_ts, mode=mode)
        if sig is not None:
            out.append((sig, name))
    return out
