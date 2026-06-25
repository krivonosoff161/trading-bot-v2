# -*- coding: utf-8 -*-
"""Selection -> deterministic generation -> lifecycle -> review for the paper-watch lane (research-only).

Everything here is deterministic and keyless. An LLM may add colour to reason/risk text, but the signal
geometry and every gate decision are computed from candles + universe stats only — there is no path to an
order, .env, AUTO_TRADE, or a private endpoint.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.paper_signals.contract import PaperActionSignal, validate_signal

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
    """Round to a sane precision relative to price magnitude (keeps cards readable)."""
    if ref <= 0:
        return round(x, 6)
    digits = max(2, 6 - len(str(int(ref))))
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
def gate_candidate(mover: dict[str, Any], candles: list[dict[str, Any]], *, now_ms: int,
                   known_bad: set[tuple[str, str]], family: str = "momentum_continuation") -> str:
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
    if (sym, family) in known_bad or (sym, "*") in known_bad:   # exact family OR symbol-wide confirmed-bad
        return "known_bad_in_memory"
    return "ok"


# ── lifecycle: observe a signal on FRESH candles (bars strictly after boundary_ts) ──
def observe(sig: PaperActionSignal, candles: list[dict[str, Any]]) -> PaperActionSignal:
    """Advance armed -> opened_paper -> closed_paper/expired/invalidated using only bars after the
    creation boundary. No look-ahead: each bar is evaluated in order."""
    if sig.status not in ("armed", "opened_paper"):
        return sig
    new = [c for c in candles if int(c.get("ts") or 0) > int(sig.boundary_ts)]
    if not new:
        sig.outcome = {"result": "pending", "new_bars": 0}
        return sig
    long_ = sig.side == "long"
    lo, hi = sig.entry_zone
    tp_final = sig.take_profit_plan[-1]["price"]
    tp1 = sig.take_profit_plan[0]["price"]
    do_partial = sig.exit_mode == "partial_be" and len(sig.take_profit_plan) > 1
    opened = sig.status == "opened_paper"
    o = sig.outcome or {}
    entry_px = float(o.get("entry") or 0.0)
    open_i = int(o.get("open_index") or 0)
    eff_stop = float(o.get("eff_stop") or sig.stop_loss)     # stop moves to breakeven after the partial
    partial_done = bool(o.get("partial_done"))
    be_done = bool(o.get("be_done"))                          # simple BE at 0.5R already activated
    banked = float(o.get("banked_pct") or 0.0)               # realized leg-1 net% (half size)
    mfe = float(o.get("mfe_pct") or 0.0)
    mae = float(o.get("mae_pct") or 0.0)
    fav_wait = 0.0

    def _leg(px):
        return (px - entry_px if long_ else entry_px - px) / entry_px * 100

    for i, c in enumerate(new):
        h, lov, cl = float(c["high"]), float(c["low"]), float(c["close"])
        if not opened:
            fav_wait = max(fav_wait, ((h - hi) / hi if long_ else (lo - lov) / lo) * 100)
            if i >= ARM_WINDOW_BARS:
                sig.status = "expired"
                sig.outcome = {"result": "expired_no_entry", "bars_waited": i,
                               "ran_away": fav_wait >= (sig.risk_pct or 99)}
                return sig
            filled = lov <= lo if long_ else h >= hi   # limit-pullback fill
            if filled:
                opened, entry_px, open_i = True, (lo if long_ else hi), i
                eff_stop = sig.stop_loss
                sig.status = "opened_paper"
            continue
        mfe = max(mfe, (h - entry_px if long_ else entry_px - lov) / entry_px * 100)
        mae = max(mae, (entry_px - lov if long_ else h - entry_px) / entry_px * 100)
        bars_held = i - open_i
        hit_sl = lov <= eff_stop if long_ else h >= eff_stop
        if hit_sl:
            # same-bar ambiguity is handled conservatively: if this bar also triggered the
            # original stop, we honour the stop; be_done only applies from the NEXT bar onward
            # (the simple-BE block below runs AFTER this check)
            kind = ("simple_be" if (be_done and not partial_done)
                    else "partial_be" if partial_done
                    else "stop")
            return _close(sig, kind, eff_stop, entry_px, banked, partial_done, mfe, mae, bars_held, long_)
        # simple BE at 0.5R — only in partial_be exit mode (consistent with do_partial gating).
        # Placed AFTER the stop check so a same-bar low/high that also hits the original stop
        # is never retroactively saved; BE applies from NEXT bar only.
        if not be_done and sig.exit_mode == "partial_be":
            risk_dist = abs(entry_px - sig.stop_loss)
            if risk_dist > 0:
                be_px = (entry_px + 0.5 * risk_dist) if long_ else (entry_px - 0.5 * risk_dist)
                if (h >= be_px) if long_ else (lov <= be_px):
                    eff_stop = entry_px
                    be_done = True
        # bank half at tp1 and trail the stop to breakeven (the bad_exit_gave_back remedy)
        if do_partial and not partial_done and ((h >= tp1) if long_ else (lov <= tp1)):
            banked, partial_done, eff_stop = 0.5 * _leg(tp1), True, entry_px
        if (h >= tp_final) if long_ else (lov <= tp_final):
            return _close(sig, "take", tp_final, entry_px, banked, partial_done, mfe, mae, bars_held, long_)
        if bars_held >= sig.max_hold_bars:
            return _close(sig, "timeout", cl, entry_px, banked, partial_done, mfe, mae, bars_held, long_)
    sig.outcome = {"result": "pending_open" if opened else "pending_arm", "entry": entry_px,
                   "open_index": open_i, "eff_stop": eff_stop, "partial_done": partial_done,
                   "be_done": be_done,
                   "banked_pct": round(banked, 4), "mfe_pct": round(mfe, 3), "mae_pct": round(mae, 3),
                   "new_bars": len(new)}
    return sig


def _close(sig, kind, exit_px, entry_px, banked, partial_done, mfe, mae, bars_held, long_):
    leg2 = (exit_px - entry_px if long_ else entry_px - exit_px) / entry_px * 100
    # remaining size is 0.5 after a partial, else full
    net = banked + 0.5 * leg2 if partial_done else leg2
    sig.status = "closed_paper"
    sig.outcome = {"result": kind, "entry": round(entry_px, 8), "exit": round(exit_px, 8),
                   "net_pct": round(net, 3), "banked_pct": round(banked, 4), "partial_done": partial_done,
                   "mfe_pct": round(mfe, 3), "mae_pct": round(mae, 3), "bars_held": bars_held,
                   "reached_tp1": bool(partial_done or kind == "take")}
    return sig


def age_out(sig: PaperActionSignal, now: float, *, max_no_data: int = 4) -> bool:
    """Wall-clock / no-data age-out so an armed card cannot strand forever (bottleneck #5). An armed or
    opened signal past expires_at, or one whose symbol has returned no candles too many times, becomes
    terminal (expired_no_entry with a reason). Returns True if it just aged out."""
    if sig.status not in ("armed", "opened_paper"):
        return False
    o = sig.outcome or {}
    if sig.expires_at and now > sig.expires_at:
        sig.status = "expired"
        sig.outcome = {**o, "result": "expired_no_entry", "reason": "stale_past_expiry"}
        return True
    if int(o.get("no_data_count") or 0) >= max_no_data:
        sig.status = "expired"
        sig.outcome = {**o, "result": "expired_no_entry", "reason": "no_data_repeated"}
        return True
    return False


# ── review: deterministic metrics + diagnosis (the visual-review backbone) ──
def review(sig: PaperActionSignal) -> PaperActionSignal:
    o = sig.outcome or {}
    res = o.get("result")
    net = float(o.get("net_pct") or 0.0)
    mfe = float(o.get("mfe_pct") or 0.0)
    mae = float(o.get("mae_pct") or 0.0)
    capture = round(net / mfe, 3) if mfe > 0 else 0.0
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
                  "capture_of_mfe": capture, "bars_held": o.get("bars_held"),
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
    tail = candles[-(sig.max_hold_bars + 30):]
    closes = [float(c["close"]) for c in tail]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(closes, color="black", lw=1)
    ax.axhspan(sig.entry_zone[0], sig.entry_zone[1], color="tab:blue", alpha=0.2, label="entry zone")
    ax.axhline(sig.stop_loss, color="tab:red", ls="--", lw=1, label="stop")
    for t in sig.take_profit_plan:
        ax.axhline(t["price"], color="tab:green", ls=":", lw=1)
    ax.set_title(f"{sig.okx_inst_id} {sig.timeframe} {sig.side.upper()} [{sig.review.get('diagnosis', 'paper')}]")
    ax.legend(loc="best", fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)


def load_known_bad(private_root: Path) -> set[tuple[str, str]]:
    """(symbol, family) pairs the outcome memory flags as confirmed-bad. Empty if unavailable."""
    path = Path(private_root) / "state" / "derived" / "setup_outcome_memory.json"
    try:
        recs = json.loads(path.read_text(encoding="utf-8")).get("records") or []
    except Exception:  # noqa: BLE001
        return set()
    bad = set()
    for r in recs:
        if r.get("outcome_class") == "REJECTED_CONFIRMED_BAD" or r.get("tactical_class") == "REJECTED_CONFIRMED_BAD":
            sym = str(r.get("symbol"))
            fam = str(r.get("family") or "*")
            bad.add((sym, fam))      # block this exact (symbol, family) ...
            bad.add((sym, "*"))      # ... and the symbol wholesale (cross-family confirmed-bad)
    return bad
