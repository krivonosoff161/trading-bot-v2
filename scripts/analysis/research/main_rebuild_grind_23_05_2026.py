"""Grind-ride test (engine #3 candidate) — 23.05.2026.

Phase-1 audit found 1453 events classified as trend_grind_watch and NEVER traded — the
'long smooth moves' the trader points at. This tests whether riding them makes money:
enter WITH the grind direction, structural stop, long hold (4h/8h), structural trail.
If net>0 both sides -> engine #3 is real (huge: ~78% of events). If not -> it's chop.

Reuses replay pipeline + base helpers read-only.
Run: python scripts/analysis/research/main_rebuild_grind_23_05_2026.py
"""

from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main_rebuild_replay_23_05_2026 as mrr
phase_a = mrr.phase_a
phase_b = mrr.phase_b
base = mrr.base

GRIND_MIN_STOP_PCT = 1.0   # grind = wider stop than impulse
HOLD_BARS = {"4h": 16, "8h": 32}   # 15m bars
STRUCT_K = [2, 3]


def mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float)) and math.isfinite(x)]
    return sum(xs) / len(xs) if xs else float("nan")


def grind_stop(rows, idx, side):
    entry_raw = base.safe_float(rows[idx][4])
    low = base.safe_float(rows[idx][3]); high = base.safe_float(rows[idx][2])
    buf = entry_raw * base.CONFIG["structural_stop_buffer_pct"] / 100
    min_dist = entry_raw * GRIND_MIN_STOP_PCT / 100
    if side == "long":
        return min(low - buf, entry_raw - min_dist)
    return max(high + buf, entry_raw + min_dist)


def grind_sim(rows, idx, side, hold_bars, exit_mode, k):
    o = base.safe_float(rows[idx][1]); c = base.safe_float(rows[idx][4])
    entry = base.slipped_entry((o + c) / 2.0, side)   # mid entry
    stop = grind_stop(rows, idx, side)
    end = min(len(rows) - 1, idx + hold_bars)
    best = 0.0
    outcome = "TIME"
    exit_price = base.safe_float(rows[end][4])
    for j in range(idx + 1, end + 1):
        row = rows[j]
        fav = base.favorable_price(row, side)
        best = max(best, base.dir_return(entry, fav, side))
        if base.stop_hit(row, stop, side):
            outcome = "SL"; exit_price = stop; break
        if exit_mode == "structure" and base.structure_break(rows, j, side, k):
            outcome = f"STRUCT_K{k}"; exit_price = base.safe_float(row[4]); break
    gross = base.dir_return(entry, exit_price, side)
    return {"net": gross - base.CONFIG["fee_pct"], "mfe": best,
            "capture": (max(gross, 0) / best * 100) if best > 0 else float("nan"),
            "outcome": outcome}


def _trend_side(rows, idx, w=6):
    """Honest no-look-ahead trend direction: recent SMA vs older SMA, PAST bars only."""
    if idx - 2 * w < 0:
        return None
    recent = sum(base.safe_float(r[4]) for r in rows[idx - w:idx]) / w
    older = sum(base.safe_float(r[4]) for r in rows[idx - 2 * w:idx - w]) / w
    if recent > older:
        return "long"
    if recent < older:
        return "short"
    return None


def _event_side(ev):
    return "long" if ev["direction"] == "long" else "short" if ev["direction"] == "short" else None


def main() -> int:
    _, candle_sets, _, events, s, e = base.load_replay()
    grind = []
    for ev in events:
        if phase_b.model_for_event(ev) != "trend_grind_watch":
            continue
        cs = candle_sets.get(ev["symbol"])
        if not cs:
            continue
        idx = phase_b.candle_idx(cs, "15m", int(ev["start_open_ms"]))
        if idx is None:
            continue
        grind.append({
            "rows": cs.rows["15m"], "idx": idx,
            "event_side": _event_side(ev),                  # LOOK-AHEAD (future move dir) — reference only
            "bias_side": _trend_side(cs.rows["15m"], idx),  # honest: past-only SMA trend direction
        })

    print(f"\n=== GRIND-RIDE TEST (window {base.iso_from_ms(s)[:10]}..{base.iso_from_ms(e)[:10]}) ===")
    print(f"grind events: {len(grind)}")

    for src_name, key in [("event_dir LOOK-AHEAD (ref)", "event_side"), ("past-trend HONEST", "bias_side")]:
        usable = [g for g in grind if g[key] in ("long", "short")]
        print(f"\n--- direction source: {src_name}  (n={len(usable)}) ---")
        print(f"{'config':>20} {'net':>8} {'long':>8} {'short':>8} {'WR':>5} {'cap':>5}")
        for hold_name, hb in HOLD_BARS.items():
            for cfg_name, mode, k in [("time", "time", 0)] + [("struct_k%d" % k, "structure", k) for k in STRUCT_K]:
                res = [{**grind_sim(g["rows"], g["idx"], g[key], hb, mode, k), "side": g[key]} for g in usable]
                nets = [r["net"] for r in res]
                longs = [r["net"] for r in res if r["side"] == "long"]
                shorts = [r["net"] for r in res if r["side"] == "short"]
                wr = sum(1 for x in nets if x > 0) / len(nets) * 100 if nets else 0
                print(f"{hold_name+'/'+cfg_name:>20} {mean(nets):>+7.3f}% {mean(longs):>+7.3f}% "
                      f"{mean(shorts):>+7.3f}% {wr:>4.0f}% {mean([r['capture'] for r in res]):>4.0f}%")
    print("\n--- read ---")
    print("LOOK-AHEAD uses future move direction (cheating) — reference for the inflation gap.")
    print("HONEST uses 1h trend bias from past data. If HONEST net>0 both sides -> grind edge is real.")
    print("If HONEST collapses to ~0/negative -> the +1% was pure look-ahead; grind not (yet) tradeable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
