# -*- coding: utf-8 -*-
"""Selection -> deterministic generation -> lifecycle -> review for the paper-watch lane (research-only).

Everything here is deterministic and keyless. An LLM may add colour to reason/risk text, but the signal
geometry and every gate decision are computed from candles + universe stats only — there is no path to an
order, .env, AUTO_TRADE, or a private endpoint.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.research_lab.paper_signals.contract import PaperActionSignal, validate_signal
from src.research_lab.trade_math import CostAssumptions, capture, gross_pct, net_pct
from src.research_lab.simulator_contract import (
    build_cost_ledger,
    build_trade_quantity_ledger,
    incremental_paper_lane_manifest,
    reconcile_partial_fills,
)

# Per-timeframe holding horizon (the owner's gradation): max_hold in BARS.
HORIZON_BARS = {"1m": 5, "5m": 24, "15m": 28, "1h": 30, "4h": 9, "1d": 5}
TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
ARM_WINDOW_BARS = 6              # bars allowed to fill the entry zone before expiry (no_entry)
MAX_SPREAD_BPS = 15.0
MIN_VOL_USD = 20_000_000.0
ATR_N = 14
TREND_LOOKBACK = 10
STOP_ATR_MULT = 1.2             # stop = entry -/+ this many ATR (bounded risk; structure only if tighter)
MAX_RISK_PCT = 8.0              # a signal whose 1R exceeds this is too volatile to be actionable
LIFECYCLE_SCHEMA = "PaperSignalLifecycle.v2"


def _lane_provenance() -> dict[str, Any]:
    manifest = incremental_paper_lane_manifest()
    return {
        "simulator_manifest": manifest,
        "simulator_model_id": manifest["simulator_model_id"],
        "simulator_evidence_tier": manifest["evidence_tier"],
        "unsupported_simulator_dimensions": manifest["unsupported_dimensions"],
    }


# ── small pure helpers ────────────────────────────────────────────────────────
def _atr(candles: list[dict[str, Any]], n: int = ATR_N) -> float:
    if len(candles) < n + 1:
        return 0.0
    trs = []
    for i in range(len(candles) - n, len(candles)):
        h, lo = float(candles[i]["high"]), float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def _swing_low(candles: list[dict[str, Any]], k: int = 10) -> float:
    return min(float(c["low"]) for c in candles[-k:])


def _swing_high(candles: list[dict[str, Any]], k: int = 10) -> float:
    return max(float(c["high"]) for c in candles[-k:])


def _round(x: float, ref: float) -> float:
    """Round by price magnitude so sub-cent instruments keep usable TP/SL precision."""
    if ref <= 0 or x <= 0:
        return round(x, 8)
    magnitude = math.floor(math.log10(abs(ref)))
    digits = max(0, min(10, 4 - magnitude))
    return round(x, digits)


# ── geometry: candles -> a continuation watch signal (deterministic, no look-ahead) ──
def fingerprint(candles: list[dict[str, Any]], k: int = 20) -> str:
    """Stable hash of the decision window (last k ts+close). New bars -> new fingerprint."""
    import hashlib
    tail = candles[-k:]
    raw = ";".join(f"{int(c.get('ts') or 0)}:{c.get('close')}" for c in tail)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def build_signal(symbol: str, inst_id: str, timeframe: str, candles: list[dict[str, Any]],
                 *, source: str, mover: dict[str, Any] | None = None, now: float,
                 boundary_ts: int, validator_ctx: dict | None = None,
                 memory_ctx: dict | None = None, mode: str = "live") -> tuple[PaperActionSignal | None, str]:
    """Return (signal, "ok") or (None, gate_reason). Continuation: trade WITH the trend, enter on a
    shallow pullback into the zone, stop beyond recent structure, TP at 1R/2R."""
    if len(candles) < ATR_N + TREND_LOOKBACK + 2:
        return None, "insufficient_bars"
    atr = _atr(candles)
    if atr <= 0:
        return None, "flat_atr"
    price = float(candles[-1]["close"])
    trend = price - float(candles[-1 - TREND_LOOKBACK]["close"])
    if trend == 0:
        return None, "no_trend"
    side = "long" if trend > 0 else "short"
    tf_min = TF_MINUTES.get(timeframe, 15)
    hold_bars = HORIZON_BARS.get(timeframe, 12)

    # ATR-bounded stop keeps risk sane on volatile movers (use structure only when it is TIGHTER).
    if side == "long":
        entry_hi = _round(price, price)
        entry_lo = _round(price - 0.5 * atr, price)
        struct = _swing_low(candles) - 0.2 * atr
        stop = _round(max(struct, entry_lo - STOP_ATR_MULT * atr), price)
        risk = entry_lo - stop
        if risk <= 0:
            return None, "non_positive_risk"
        tps = [{"label": "tp1", "price": _round(entry_hi + risk, price), "size_frac": 0.5},
               {"label": "tp2", "price": _round(entry_hi + 2 * risk, price), "size_frac": 0.5}]
    else:
        entry_lo = _round(price, price)
        entry_hi = _round(price + 0.5 * atr, price)
        struct = _swing_high(candles) + 0.2 * atr
        stop = _round(min(struct, entry_hi + STOP_ATR_MULT * atr), price)
        risk = stop - entry_hi
        if risk <= 0:
            return None, "non_positive_risk"
        tps = [{"label": "tp1", "price": _round(entry_lo - risk, price), "size_frac": 0.5},
               {"label": "tp2", "price": _round(entry_lo - 2 * risk, price), "size_frac": 0.5}]
    risk_pct = round(risk / price * 100, 3)
    if risk_pct > MAX_RISK_PCT:
        return None, f"risk_too_wide_{risk_pct}pct"

    mv = mover or {}
    reason = (f"live mover (move%={mv.get('move_pct', '?')}, vol=${mv.get('vol_usd', 0) / 1e6:.0f}M, "
              f"spread={mv.get('spread_bps', '?')}bps); {side} momentum continuation; trend over "
              f"{TREND_LOOKBACK} bars; not known-bad in memory")
    invalidation = (f"close beyond {stop} (structure), OR no entry-fill within {ARM_WINDOW_BARS} bars "
                    f"(expired_no_entry), OR no follow-through (no tp1) within {hold_bars} bars")
    family = "momentum_continuation"
    fp = fingerprint(candles)
    sig = PaperActionSignal(
        signal_id=f"{symbol}_{timeframe}_{family}_{fp}",
        source=source, symbol=symbol, okx_inst_id=inst_id, timeframe=timeframe, side=side,
        setup_family=family,
        entry_zone=[entry_lo, entry_hi], stop_loss=stop, invalidation_rule=invalidation,
        take_profit_plan=tps, max_hold_bars=hold_bars, max_hold_minutes=hold_bars * tf_min,
        reason_now=reason, risk_notes="continuation can fail into reversal; paper-only watch",
        validator_context=validator_ctx or {}, outcome_memory_context=memory_ctx or {},
        status="armed", created_at=now, expires_at=now + ARM_WINDOW_BARS * tf_min * 60,
        ref_price=price, risk_pct=risk_pct, boundary_ts=boundary_ts,
        data_fingerprint=fp, dedup_key=f"{symbol}|{timeframe}|{family}", mode=mode)
    ok, problems = validate_signal(sig)
    if not ok:
        return None, "failed_validate:" + ";".join(problems)
    return sig, "ok"


# ── selection gates (deterministic; every rejection is recorded by reason) ──
KnownBadSetup = tuple[str, str, str, str]


class KnownBadAuthorityUnavailable(RuntimeError):
    """A required complete known-bad snapshot is missing or not trustworthy."""


@dataclass(frozen=True)
class KnownBadAuthority:
    """Exact known-bad identities with explicit derived-snapshot provenance."""

    setups: frozenset[KnownBadSetup]
    state: str
    snapshot_state: str


def is_known_bad_setup(
    known_bad: set[KnownBadSetup],
    *,
    symbol: str,
    timeframe: str,
    family: str,
    params_hash: str = "",
) -> bool:
    """Return true only for the exact outcome-memory setup identity.

    A record with a params hash can reject only that parameter set.  A legacy
    record without a params hash remains bounded to its exact symbol, timeframe
    and family; missing identity fields never become wildcards.
    """
    identity = (str(symbol), str(timeframe), str(family), str(params_hash))
    if not all(identity[:3]):
        return False
    return identity in known_bad


def gate_candidate(mover: dict[str, Any], candles: list[dict[str, Any]], *, now_ms: int,
                   known_bad: set[KnownBadSetup], family: str = "",
                   params_hash: str = "") -> str:
    inst = str(mover.get("inst_id") or "")
    if not inst.endswith("-USDT-SWAP"):
        return "not_tradeable_inst"
    if len(candles) < ATR_N + TREND_LOOKBACK + 2:
        return "insufficient_bars"
    tf = mover.get("_tf", "15m")
    last_ts = int(candles[-1].get("ts") or 0)
    if now_ms - last_ts > 3 * TF_MINUTES.get(tf, 15) * 60_000:
        return "stale_data"
    if float(mover.get("spread_bps") or 999) > MAX_SPREAD_BPS:
        return "spread_too_wide"
    if float(mover.get("vol_usd") or 0) < MIN_VOL_USD:
        return "volume_too_thin"
    sym = str(mover.get("symbol") or inst.replace("-", "_"))
    if family and is_known_bad_setup(
        known_bad,
        symbol=sym,
        timeframe=str(tf),
        family=family,
        params_hash=params_hash,
    ):
        return "known_bad_in_memory"
    return "ok"


# ── lifecycle: observe each post-boundary candle exactly once ──
def _ordered_post_boundary(sig: PaperActionSignal, candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {
        int(row.get("ts") or 0): row
        for row in candles
        if int(row.get("ts") or 0) > int(sig.boundary_ts)
    }
    return [unique[ts] for ts in sorted(unique)]


def _legacy_progress(sig: PaperActionSignal, outcome: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, int]:
    has_cursor = outcome.get("last_observed_bar_ts") not in (None, "")
    stored_observed = max(0, int(outcome.get("observed_bars_total") or outcome.get("new_bars") or 0))
    observed = stored_observed if has_cursor else min(stored_observed, len(rows))
    cursor = int(outcome.get("last_observed_bar_ts") or sig.boundary_ts)
    if not has_cursor and observed:
        cursor = int(rows[observed - 1].get("ts") or sig.boundary_ts)
    open_index = max(0, int(outcome.get("open_index") or 0))
    held = outcome.get("bars_held")
    if sig.status == "opened_paper":
        bars_held = max(0, int(held)) if held is not None else max(0, observed - open_index - 1)
    else:
        bars_held = 0
    waited = outcome.get("bars_waited")
    bars_waited = max(0, int(waited)) if waited is not None else min(observed, ARM_WINDOW_BARS)
    return {"cursor": cursor, "observed": observed, "bars_held": bars_held, "bars_waited": bars_waited}


def _entry_filled(sig: PaperActionSignal, high: float, low: float) -> bool:
    lo, hi = sig.entry_zone
    trigger = str((sig.validator_context or {}).get("entry_trigger") or "limit_pullback")
    if trigger == "breakout_stop":
        return high >= hi if sig.side == "long" else low <= lo
    return low <= lo if sig.side == "long" else high >= hi


def _entry_price(sig: PaperActionSignal) -> float:
    lo, hi = sig.entry_zone
    trigger = str((sig.validator_context or {}).get("entry_trigger") or "limit_pullback")
    if trigger == "breakout_stop":
        return hi if sig.side == "long" else lo
    return lo if sig.side == "long" else hi


def _pending_outcome(state: dict[str, Any], *, opened: bool) -> dict[str, Any]:
    state.pop("open_index", None)
    return {
        **state,
        **_lane_provenance(),
        "lifecycle_schema": LIFECYCLE_SCHEMA,
        "result": "pending_open" if opened else "pending_arm",
    }


def observe(sig: PaperActionSignal, candles: list[dict[str, Any]]) -> PaperActionSignal:
    """Advance lifecycle using a durable candle cursor, with no replay across cycles."""
    if sig.status not in ("armed", "opened_paper"):
        return sig
    rows = _ordered_post_boundary(sig, candles)
    original = dict(sig.outcome or {})
    progress = _legacy_progress(sig, original, rows)
    fresh = [row for row in rows if int(row.get("ts") or 0) > progress["cursor"]]
    if not fresh:
        sig.outcome = _pending_outcome({**original, "fresh_bars_this_cycle": 0}, opened=sig.status == "opened_paper")
        return sig

    long_ = sig.side == "long"
    opened = sig.status == "opened_paper"
    entry_px = float(original.get("entry") or 0.0)
    eff_stop = float(original.get("eff_stop") or sig.stop_loss)
    partial_done = bool(original.get("partial_done"))
    be_done = bool(original.get("be_done"))
    banked = float(original.get("banked_pct") or 0.0)
    mfe = float(original.get("mfe_pct") or 0.0)
    mae = float(original.get("mae_pct") or 0.0)
    fav_wait = float(original.get("fav_wait_pct") or 0.0)
    bars_held, bars_waited = progress["bars_held"], progress["bars_waited"]
    observed = progress["observed"]
    processed = 0
    state = dict(original)

    for candle in fresh:
        processed += 1
        ts = int(candle.get("ts") or 0)
        high, low, close = float(candle["high"]), float(candle["low"]), float(candle["close"])
        observed += 1
        state.update({
            "last_observed_bar_ts": ts,
            "last_observed_close": close,
            "observed_bars_total": observed,
            "fresh_bars_this_cycle": processed,
        })
        if not opened:
            if bars_waited >= ARM_WINDOW_BARS:
                return _expire_no_entry(sig, state, bars_waited, fav_wait)
            lo, hi = sig.entry_zone
            fav_wait = max(fav_wait, ((high - hi) / hi if long_ else (lo - low) / lo) * 100)
            bars_waited += 1
            state.update({"bars_waited": bars_waited, "fav_wait_pct": round(fav_wait, 3)})
            if _entry_filled(sig, high, low):
                entry_px, opened, eff_stop = _entry_price(sig), True, sig.stop_loss
                sig.status = "opened_paper"
                state.update({"entry": entry_px, "opened_at_bar_ts": ts, "eff_stop": eff_stop})
            elif bars_waited >= ARM_WINDOW_BARS:
                return _expire_no_entry(sig, state, bars_waited, fav_wait)
            continue

        bars_held += 1
        mfe = max(mfe, (high - entry_px if long_ else entry_px - low) / entry_px * 100)
        mae = max(mae, (entry_px - low if long_ else high - entry_px) / entry_px * 100)
        state.update({"bars_held": bars_held, "mfe_pct": round(mfe, 3), "mae_pct": round(mae, 3)})
        if (low <= eff_stop) if long_ else (high >= eff_stop):
            kind = "simple_be" if be_done and not partial_done else "partial_be" if partial_done else "stop"
            return _close(sig, entry_px, banked, partial_done, mfe, mae, bars_held, long_, state,
                          kind=kind, exit_px=eff_stop)
        risk_dist = abs(entry_px - sig.stop_loss)
        if not be_done and sig.exit_mode == "partial_be" and risk_dist > 0:
            be_price = entry_px + 0.5 * risk_dist if long_ else entry_px - 0.5 * risk_dist
            if (high >= be_price) if long_ else (low <= be_price):
                eff_stop, be_done = entry_px, True
        tp1 = float(sig.take_profit_plan[0]["price"])
        do_partial = sig.exit_mode == "partial_be" and len(sig.take_profit_plan) > 1
        if do_partial and not partial_done and ((high >= tp1) if long_ else (low <= tp1)):
            leg = (tp1 - entry_px if long_ else entry_px - tp1) / entry_px * 100
            banked, partial_done, eff_stop = round(0.5 * leg, 4), True, entry_px
        state.update({
            "eff_stop": eff_stop,
            "be_done": be_done,
            "partial_done": partial_done,
            "banked_pct": round(banked, 4),
        })
        tp_final = float(sig.take_profit_plan[-1]["price"])
        if (high >= tp_final) if long_ else (low <= tp_final):
            return _close(sig, entry_px, banked, partial_done, mfe, mae, bars_held, long_, state,
                          kind="take", exit_px=tp_final)
        if bars_held >= sig.max_hold_bars:
            return _close(sig, entry_px, banked, partial_done, mfe, mae, bars_held, long_, state,
                          kind="timeout", exit_px=close)

    state.update({
        "bars_waited": bars_waited,
        "fav_wait_pct": round(fav_wait, 3),
        "fresh_bars_this_cycle": processed,
    })
    if opened:
        state.update({
            "entry": entry_px,
            "eff_stop": eff_stop,
            "partial_done": partial_done,
            "be_done": be_done,
            "banked_pct": round(banked, 4),
            "mfe_pct": round(mfe, 3),
            "mae_pct": round(mae, 3),
            "bars_held": bars_held,
        })
    sig.outcome = _pending_outcome(state, opened=opened)
    return sig


def _expire_no_entry(
    sig: PaperActionSignal,
    state: dict[str, Any],
    bars_waited: int,
    fav_wait: float,
) -> PaperActionSignal:
    sig.status = "expired"
    for key in (
        "open_index",
        "entry",
        "eff_stop",
        "partial_done",
        "be_done",
        "banked_pct",
        "mfe_pct",
        "mae_pct",
        "bars_held",
        "opened_at_bar_ts",
    ):
        state.pop(key, None)
    sig.outcome = {
        **state,
        **_lane_provenance(),
        "lifecycle_schema": LIFECYCLE_SCHEMA,
        "result": "expired_no_entry",
        "bars_waited": bars_waited,
        "fav_wait_pct": round(fav_wait, 3),
        "ran_away": fav_wait >= (sig.risk_pct or 99),
    }
    return sig


def _close(sig, entry_px, banked, partial_done, mfe, mae, bars_held, long_, state, *, kind, exit_px):
    side = "long" if long_ else "short"
    leg2 = gross_pct(entry_px, exit_px, side)
    # remaining size is 0.5 after a partial, else full
    gross = banked + 0.5 * leg2 if partial_done else leg2
    assumptions = CostAssumptions()
    net = net_pct(gross, assumptions)
    quantities = [0.5, 0.5] if partial_done else [1.0]
    prices = [float(sig.take_profit_plan[0]["price"]), float(exit_px)] if partial_done else [float(exit_px)]
    reconciliation = reconcile_partial_fills(
        1.0,
        [
            {"quantity": quantity, "price": price, "cost_pct": assumptions.total_cost_pct}
            for quantity, price in zip(quantities, prices)
        ],
        entry_price=float(entry_px), side=side,
    )
    if abs(float(reconciliation["net_return_pct"]) - net) > 1e-3:
        raise ValueError("paper lane fill reconciliation does not match outcome")
    sig.status = "closed_paper"
    state.pop("open_index", None)
    sig.outcome = {**state, **_lane_provenance(), "lifecycle_schema": LIFECYCLE_SCHEMA,
                   "result": kind, "entry": round(entry_px, 8), "exit": round(exit_px, 8),
                   "gross_pct": round(gross, 3), "net_pct": round(net, 3),
                   "fees_bps_round_trip": assumptions.fees_bps_round_trip,
                   "slippage_bps_round_trip": assumptions.slippage_bps_round_trip,
                   "cost_ledger": build_cost_ledger(
                       fees_bps=assumptions.fees_bps_round_trip,
                       slippage_bps=assumptions.slippage_bps_round_trip,
                   ),
                   "quantity_ledger": build_trade_quantity_ledger(closed_legs=quantities),
                   "fill_reconciliation": reconciliation,
                   "banked_pct": round(banked, 4), "partial_done": partial_done,
                   "mfe_pct": round(mfe, 3), "mae_pct": round(mae, 3), "bars_held": bars_held,
                   "reached_tp1": bool(partial_done or kind == "take")}
    return sig


def age_out(sig: PaperActionSignal, now: float, *, max_no_data: int = 4) -> bool:
    """Expire an unfilled watch or invalidate an opened watch with repeated data loss."""
    if sig.status not in ("armed", "opened_paper"):
        return False
    o = sig.outcome or {}
    if sig.status == "armed" and sig.expires_at and now > sig.expires_at:
        sig.status = "expired"
        sig.outcome = {**o, **_lane_provenance(), "lifecycle_schema": LIFECYCLE_SCHEMA,
                       "result": "expired_no_entry", "reason": "stale_past_expiry"}
        return True
    if int(o.get("no_data_count") or 0) >= max_no_data:
        if sig.status == "opened_paper":
            sig.status = "invalidated"
            sig.outcome = {**o, **_lane_provenance(), "lifecycle_schema": LIFECYCLE_SCHEMA,
                           "result": "no_data", "reason": "no_data_repeated_opened"}
        else:
            sig.status = "expired"
            sig.outcome = {**o, **_lane_provenance(), "lifecycle_schema": LIFECYCLE_SCHEMA,
                           "result": "expired_no_entry", "reason": "no_data_repeated"}
        return True
    return False


# ── review: deterministic metrics + diagnosis (the visual-review backbone) ──
def review(sig: PaperActionSignal) -> PaperActionSignal:
    o = sig.outcome or {}
    res = o.get("result")
    net = float(o.get("net_pct") or 0.0)
    mfe = float(o.get("mfe_pct") or 0.0)
    mae = float(o.get("mae_pct") or 0.0)
    captured = round(capture(net, mfe), 3)
    r1 = sig.risk_pct or 1e-9                 # 1R in % — diagnose on R-multiples, not absolute %
    mfe_r, mae_r = mfe / r1, mae / r1
    if res in ("no_data",):
        diag = "data_issue"
    elif res == "expired_no_entry":
        diag = "missed_pullback" if o.get("ran_away") else "expired_no_entry"
    elif res in ("pending", "pending_arm", "pending_open"):
        diag = "pending"
    elif res == "take":
        diag = "good_signal"
    elif res == "simple_be":
        # 0.5R was reached then price returned to entry; net ~= 0 (small negative with real costs)
        diag = "breakeven_save"
    elif res == "partial_be":
        # banked half at tp1 then exited the rest at breakeven — the give-back remedy worked
        diag = "partial_breakeven_save" if net >= 0 else "valid_loss"
    elif res == "stop":
        if mfe_r < 0.2:
            diag = "wrong_direction"               # went straight to the stop, no favourable move
        elif mfe_r < 0.5:
            diag = "valid_loss"
        elif mae_r < 1.1 and mfe_r >= 1.0:
            diag = "stop_too_tight"                # barely past the stop after a >=1R favourable move
        else:
            diag = "bad_exit_gave_back"            # gave back >0.5R of a favourable move
    elif res == "timeout":
        if mfe_r >= 1.5:
            diag = "target_too_far"                # ran far but the TP was set out of reach
        elif mfe_r < 0.5:
            diag = "no_follow_through"
        else:
            diag = "bad_exit_gave_back"
    else:
        diag = "uncharacterized"
    sig.review = {"diagnosis": diag, "net_pct": round(net, 3), "net_r": round(net / r1, 2),
                  "mfe_pct": mfe, "mae_pct": mae, "mfe_r": round(mfe_r, 2), "risk_pct": sig.risk_pct,
                  "capture_of_mfe": captured, "bars_held": o.get("bars_held"),
                  "deterministic": True, "llm_diagnosis": None,
                  "note": "deterministic metrics first; llm_diagnosis is an optional constrained hook"}
    if sig.status == "closed_paper" or sig.status == "expired":
        sig.status = "reviewed"
    return sig


def render_review_md(sig: PaperActionSignal, candles: list[dict[str, Any]]) -> str:
    """A text visual-replay artifact: ASCII path of the bars after entry vs the levels. Pure string."""
    new = [c for c in candles if int(c.get("ts") or 0) > int(sig.boundary_ts)][: sig.max_hold_bars + ARM_WINDOW_BARS]
    lines = [f"# Paper review · {sig.okx_inst_id} {sig.timeframe} {sig.side.upper()}",
             f"diagnosis: {sig.review.get('diagnosis')}  net%={sig.review.get('net_pct')}  "
             f"capture={sig.review.get('capture_of_mfe')}  mfe={sig.review.get('mfe_pct')} mae={sig.review.get('mae_pct')}",
             f"entry_zone {sig.entry_zone}  stop {sig.stop_loss}  tp {[t['price'] for t in sig.take_profit_plan]}",
             "", "bar  close      vs_entry%"]
    entry = float(sig.outcome.get("entry") or sig.ref_price or 0) or 1.0
    for i, c in enumerate(new):
        cl = float(c["close"])
        d = (cl - entry) / entry * 100 * (1 if sig.side == "long" else -1)
        bar = "+" * min(20, int(abs(d) * 4))
        lines.append(f"{i:>3}  {cl:<10} {d:+6.2f} {bar}")
    lines.append("\npaper/research-only — NOT an order.")
    return "\n".join(lines)


def write_review_artifact(private_root: Path, sig: PaperActionSignal, candles: list[dict[str, Any]]) -> Path:
    out_dir = Path(private_root) / "state" / "derived" / "paper_reviews"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{sig.signal_id}.md"
    path.write_text(render_review_md(sig, candles), encoding="utf-8")
    # best-effort PNG chart if matplotlib is present (never a hard dependency)
    try:
        _write_chart_png(out_dir / f"{sig.signal_id}.png", sig, candles)
    except Exception:  # noqa: BLE001 - chart is optional; text artifact is the source of truth
        pass
    return path


def _write_chart_png(path: Path, sig: PaperActionSignal, candles: list[dict[str, Any]]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    tail = candles[-(sig.max_hold_bars + 30):]
    if not tail:
        return
    ohlc = [
        (
            float(c["open"]),
            float(c["high"]),
            float(c["low"]),
            float(c["close"]),
        )
        for c in tail
    ]
    fig, ax = plt.subplots(figsize=(8, 4))
    highs = [row[1] for row in ohlc]
    lows = [row[2] for row in ohlc]
    levels = [float(sig.entry_zone[0]), float(sig.entry_zone[1]), float(sig.stop_loss)]
    levels.extend(float(t["price"]) for t in sig.take_profit_plan if t.get("price") is not None)
    y_min = min(lows + levels)
    y_max = max(highs + levels)
    y_span = max(y_max - y_min, max(abs(y_max), 1.0) * 0.01)
    body_min = y_span * 0.003
    for i, (open_, high, low, close) in enumerate(ohlc):
        color = "#26a69a" if close >= open_ else "#ef5350"
        ax.vlines(i, low, high, color=color, linewidth=1.0, alpha=0.95)
        bottom = min(open_, close)
        height = max(abs(close - open_), body_min)
        ax.add_patch(
            Rectangle(
                (i - 0.28, bottom),
                0.56,
                height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8,
                alpha=0.85,
            )
        )
    ax.axhspan(sig.entry_zone[0], sig.entry_zone[1], color="tab:blue", alpha=0.2, label="entry zone")
    ax.axhline(sig.stop_loss, color="tab:red", ls="--", lw=1, label="stop")
    entry_mid = (float(sig.entry_zone[0]) + float(sig.entry_zone[1])) / 2
    ax.axhline(entry_mid, color="tab:blue", ls="-", lw=0.8, alpha=0.7, label="entry")
    for idx, t in enumerate(sig.take_profit_plan):
        label = "take profit" if idx == 0 else None
        ax.axhline(t["price"], color="tab:green", ls=":", lw=1, label=label)
    ax.set_xlim(-1, len(ohlc))
    ax.set_ylim(y_min - y_span * 0.08, y_max + y_span * 0.08)
    ax.grid(True, axis="y", alpha=0.18, linewidth=0.6)
    ax.set_title(f"{sig.okx_inst_id} {sig.timeframe} {sig.side.upper()} [{sig.review.get('diagnosis', 'paper')}]")
    ax.legend(loc="best", fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)


def _known_bad_from_records(records: list[object]) -> set[KnownBadSetup]:
    """Extract only exact, fully identified confirmed-bad setup keys."""
    bad = set()
    for r in records:
        if not isinstance(r, dict):
            continue
        if (
            r.get("outcome_class") == "CONFIRMED_BAD"
            or r.get("tactical_status") == "REJECTED_CONFIRMED_BAD"
            or r.get("tactical_class") == "REJECTED_CONFIRMED_BAD"
        ):
            sym = str(r.get("symbol") or "")
            tf = str(r.get("timeframe") or "")
            fam = str(r.get("family") or "")
            if not (sym and tf and fam):
                continue
            bad.add((sym, tf, fam, str(r.get("params_hash") or "")))
    return bad


def _known_bad_digest(items: set[KnownBadSetup]) -> str:
    payload = [list(item) for item in sorted(items)]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_known_bad_authority(private_root: Path) -> KnownBadAuthority:
    """Load the exact known-bad gate with a fail-closed snapshot contract.

    A complete snapshot is the only historical authority available to this
    paper-lane reader.  The reject cache is merely a refresh accelerator: when
    it is absent, a structurally complete snapshot continues to gate the exact
    identities.  Missing, corrupt, incomplete, or digest-mismatched snapshots
    are not converted into an empty set, because that could re-admit a setup
    already proven bad.
    """

    root = Path(private_root)
    path = root / "state" / "derived" / "setup_outcome_memory.json"
    if not path.is_file():
        raise KnownBadAuthorityUnavailable("known-bad snapshot is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnownBadAuthorityUnavailable("known-bad snapshot is unreadable") from exc
    if not isinstance(payload, dict):
        raise KnownBadAuthorityUnavailable("known-bad snapshot is invalid")
    records = payload.get("records")
    if not isinstance(records, list):
        raise KnownBadAuthorityUnavailable("known-bad snapshot records are invalid")

    schema = str(payload.get("schema") or "")
    setups = _known_bad_from_records(records)
    if schema == "setup_outcome_memory.v2":
        if payload.get("complete") is not True:
            raise KnownBadAuthorityUnavailable("known-bad snapshot is incomplete")
        declared = str(payload.get("known_bad_set_sha256") or "")
        if declared != _known_bad_digest(setups):
            raise KnownBadAuthorityUnavailable("known-bad snapshot digest mismatch")
        accelerator = root / "state" / "derived" / "setup_outcome_memory_reject_cache.json"
        if accelerator.is_file():
            return KnownBadAuthority(
                frozenset(setups),
                "complete_trusted_snapshot",
                "complete_trusted_snapshot",
            )
        return KnownBadAuthority(
            frozenset(setups),
            "valid_snapshot_accelerator_missing",
            "complete_trusted_snapshot",
        )

    # v1 was atomically written only after a complete `build_memory_index`.
    # It remains a bounded legacy authority for its exact records; a future
    # v2 write upgrades it to an explicit completeness/digest contract.
    if schema in {"", "setup_outcome_memory.v1"}:
        return KnownBadAuthority(
            frozenset(setups),
            "legacy_complete_snapshot",
            "legacy_complete_snapshot",
        )
    raise KnownBadAuthorityUnavailable("known-bad snapshot schema is unsupported")


def load_known_bad(private_root: Path) -> set[KnownBadSetup]:
    """Compatibility view over the explicit fail-closed known-bad authority."""

    return set(load_known_bad_authority(private_root).setups)
