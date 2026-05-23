"""Production-direction regime audit — 23.05.2026.

Trader's catch: the per-regime audit used a REIMPLEMENTED direction (side_from_structure),
which our own memory says breaks direction. So its low direction-% (DRIFT 24%) judged the
WRONG engine. This re-measures regimes on the REAL production signals (main_signals.jsonl —
actual compute_signal side/regime/entry/SL/TP + real labeled outcomes). No reimpl, no replay.

Per regime: production direction quality (decisive WR), current net (real geometry),
and net with a RIDE exit (structural trail instead of the tiny fixed TP), same direction+entry.
Answers: where is production right/wrong, and does fixing geometry rescue each regime.

Run: python scripts/analysis/research/main_rebuild_prod_audit_23_05_2026.py
"""

from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main_entry_timing_21_05_2026 as met

FEE = 0.20
HOLD_BARS = 24          # 24 x 5m = 2h ride cap (FAST); SWING handled by larger cap below
SWING_HOLD_BARS = 60    # 5h
STRUCT_K = 3


def mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float)) and math.isfinite(x)]
    return sum(xs) / len(xs) if xs else float("nan")


def structure_break(candles, j, side, k):
    if j - k < 0:
        return False
    c = candles[j].close
    prev = candles[j - k:j]
    if side == "buy":
        return c < min(x.low for x in prev)
    return c > max(x.high for x in prev)


def ride_net(candles, entry_idx, entry, side, sl, hold_bars, k):
    """Production direction + entry; ride exit (structural trail) with real SL, hold cap."""
    end = min(len(candles) - 1, entry_idx + hold_bars)
    exit_price = candles[end].close
    for j in range(entry_idx + 1, end + 1):
        c = candles[j]
        # SL first (conservative)
        if side == "buy" and c.low <= sl:
            exit_price = sl; break
        if side == "sell" and c.high >= sl:
            exit_price = sl; break
        if structure_break(candles, j, side, k):
            exit_price = c.close; break
    return met.directional_return(entry, exit_price, side) - FEE


def main() -> int:
    joined = met.load_joined()
    candle_cache = met.prepare_candles(joined)
    by_regime = defaultdict(list)
    for sig in joined:
        candles = candle_cache.get(sig["symbol"], [])
        if not candles:
            continue
        ts_ms = met.iso_to_ms(sig["ts"])
        eidx = met.completed_candle_index(candles, ts_ms)
        if eidx is None or eidx >= len(candles):
            continue
        entry = met.safe_float(sig["entry"]); sl = met.safe_float(sig["sl"])
        side = sig["side"]
        regime = sig.get("regime") or "?"
        style = sig.get("trade_style") or "?"
        outcome = sig["label"].get("outcome")
        cur = met.simulate_levels(candles, ts_ms, entry, sig, "immediate")
        hb = SWING_HOLD_BARS if style == "SWING" else HOLD_BARS
        rnet = ride_net(candles, eidx, entry, side, sl, hb, STRUCT_K)
        mfe = met.mfe_mae_after_entry(candles, eidx, sig)["mfe_after_entry_pct"]
        by_regime[regime].append({
            "outcome": outcome, "cur_net": met.safe_float(cur.get("net_pct")),
            "ride_net": rnet, "mfe": mfe, "side": side,
        })

    print("\n=== PRODUCTION-DIRECTION REGIME AUDIT (real signals, n joined) ===")
    print(f"{'regime':>10} {'n':>3} {'decWR':>6} {'TIME%':>6} {'cur net':>8} {'ride net':>9} {'MFE':>6}")
    for regime in sorted(by_regime):
        rs = by_regime[regime]
        oc = Counter(r["outcome"] for r in rs)
        dec = oc["TP1"] + oc["TP2"] + oc["SL"]
        wr = (oc["TP1"] + oc["TP2"]) / dec * 100 if dec else float("nan")
        time_pct = oc["TIME"] / len(rs) * 100
        print(f"{regime:>10} {len(rs):>3} {wr:>5.0f}% {time_pct:>5.0f}% "
              f"{mean([r['cur_net'] for r in rs]):>+7.3f}% {mean([r['ride_net'] for r in rs]):>+8.3f}% "
              f"{mean([r['mfe'] for r in rs]):>5.2f}%")
        # side split for ride net
        for sd in ("buy", "sell"):
            ss = [r["ride_net"] for r in rs if r["side"] == sd]
            if ss:
                print(f"           {sd:>4}: ride net {mean(ss):>+.3f}% (n={len(ss)})")

    print("\n--- read ---")
    print("decWR = production direction quality (TP vs SL). cur net = real tiny-TP geometry.")
    print("ride net = SAME production direction+entry but structural ride exit instead of fixed TP.")
    print("If ride net > cur net and >0 -> production direction is fine, geometry was the bug.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
