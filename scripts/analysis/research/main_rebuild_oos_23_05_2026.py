"""Walk-forward OOS for the TRENDING_IMPULSE GO — 23.05.2026.

The in-sample battery (main_rebuild_validate) passed: significant, two-sided,
pair-diversified, cost-robust on the 04-14.05 window. The one thing it could NOT test
is TIME out-of-sample. This extends the replay window to ~45 days (full candle cache,
auto-backfill from OKX for gaps) and slices the GO trades into sequential walk-forward
windows. GO survives OOS only if net>0 in the majority of windows with both sides holding.

Run: python scripts/analysis/research/main_rebuild_oos_23_05_2026.py
"""

from __future__ import annotations

import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main_rebuild_replay_23_05_2026 as mrr

GO_CELL = "TRENDING_IMPULSE"
GO_ENTRY = "mid"
GO_EXIT = "scaled_tp_100"
OOS_DAYS = 45
N_WINDOWS = 5

# extend the replay window before loading
mrr.phase_a.CONFIG["analysis_days"] = OOS_DAYS


def mean(xs):
    xs = [x for x in xs if math.isfinite(x)]
    return sum(xs) / len(xs) if xs else float("nan")


def iso_ms(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)


def main() -> int:
    _, candle_sets, _, events, s, e = mrr.base.load_replay()
    rows = mrr.build_rows(events, candle_sets, s, e)
    trades = []
    for r in rows:
        if r.get("skip_reason"):
            continue
        if r.get("cell") == GO_CELL and r.get("entry_mode") == GO_ENTRY and r.get("base_exit") == GO_EXIT:
            sim = r.get("new_exit") or {}
            net = mrr.base.safe_float(sim.get("net_pct"))
            if math.isfinite(net):
                trades.append({"ts": r["ts"], "ts_ms": iso_ms(r["ts"]), "symbol": r["symbol"],
                               "side": r.get("model_side"), "net": net})
    trades.sort(key=lambda t: t["ts_ms"])
    n = len(trades)
    print(f"\n=== WALK-FORWARD OOS — {GO_CELL} {GO_ENTRY} {GO_EXIT} ===")
    print(f"window {mrr.base.iso_from_ms(s)} -> {mrr.base.iso_from_ms(e)} ({OOS_DAYS}d) | trades n={n}")
    if n == 0:
        print("no trades — abort")
        return 1
    print(f"FULL period: mean net={mean([t['net'] for t in trades]):+.3f}% "
          f"WR={sum(1 for t in trades if t['net']>0)/n*100:.0f}% "
          f"long={mean([t['net'] for t in trades if t['side']=='long']):+.3f}% "
          f"short={mean([t['net'] for t in trades if t['side']=='short']):+.3f}%")

    # sequential walk-forward windows by time
    span = e - s
    step = span // N_WINDOWS
    print(f"\n--- {N_WINDOWS} sequential windows (~{step//(24*60*60*1000)}d each) ---")
    print(f"{'window':>22} {'n':>3} {'net':>8} {'long':>8} {'short':>8} {'WR':>5}")
    pos_windows = 0
    both_sides_ok = 0
    for i in range(N_WINDOWS):
        w0 = s + i * step
        w1 = s + (i + 1) * step if i < N_WINDOWS - 1 else e + 1
        wt = [t for t in trades if w0 <= t["ts_ms"] < w1]
        if not wt:
            print(f"{mrr.base.iso_from_ms(w0)[5:10]+'..'+mrr.base.iso_from_ms(w1)[5:10]:>22}   0      —        —        —     —")
            continue
        nets = [t["net"] for t in wt]
        longs = [t["net"] for t in wt if t["side"] == "long"]
        shorts = [t["net"] for t in wt if t["side"] == "short"]
        mn = mean(nets)
        if mn > 0:
            pos_windows += 1
        if longs and shorts and mean(longs) > 0 and mean(shorts) > 0:
            both_sides_ok += 1
        label = mrr.base.iso_from_ms(w0)[5:10] + ".." + mrr.base.iso_from_ms(w1)[5:10]
        print(f"{label:>22} {len(wt):>3} {mn:>+7.3f}% {mean(longs):>+7.3f}% {mean(shorts):>+7.3f}% "
              f"{sum(1 for x in nets if x>0)/len(nets)*100:>4.0f}%")

    print(f"\npositive windows: {pos_windows}/{N_WINDOWS} | both-sides-positive windows: {both_sides_ok}/{N_WINDOWS}")

    # pair-split over the whole OOS period
    by_pair = defaultdict(list)
    for t in trades:
        by_pair[t["symbol"]].append(t["net"])
    pair_tot = sorted(((sum(v), len(v), k) for k, v in by_pair.items()), reverse=True)
    top = pair_tot[0][2]
    ex_top = mean([t["net"] for t in trades if t["symbol"] != top])
    print(f"\npairs traded: {len(by_pair)} | top contributor: {top} (sum {pair_tot[0][0]:+.2f}%, n={pair_tot[0][1]})")
    print(f"mean net excluding {top}: {ex_top:+.3f}%")

    print("\n--- verdict guide ---")
    print(f"OOS GO if: positive in >=4/{N_WINDOWS} windows, both sides hold overall, survives top-pair drop.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
