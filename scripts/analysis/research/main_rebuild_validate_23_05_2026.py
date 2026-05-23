"""GO validation battery for TRENDING_IMPULSE mid — 23.05.2026.

The multi-AI round converged: the GO (TRENDING_IMPULSE, mid entry, scaled_tp_100, +0.48%)
is CONDITIONAL until it survives robustness tests. This runs the fast battery that needs
NO extra data (kills fat-tail / overfit on the existing 10-day replay sample):
  - pair-split: is the edge concentrated in one pair (BSB fat-tail precedent)?
  - cost-stress: does net survive higher fee+slippage?
  - bootstrap CI: is mean net significantly > 0?
  - sign-permutation: could the result come from random signs?
  - side split: both sides positive?

Reuses main_rebuild_replay pipeline read-only. Walk-forward OOS (long window) is a
separate next step if this battery passes.

Run: python scripts/analysis/research/main_rebuild_validate_23_05_2026.py
"""

from __future__ import annotations

import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main_rebuild_replay_23_05_2026 as mrr

GO_CELL = "TRENDING_IMPULSE"
GO_ENTRY = "mid"
GO_EXIT = "scaled_tp_100"
SEED = 17
random.seed(SEED)


def collect_go_trades():
    _, candle_sets, _, events, s, e = mrr.base.load_replay()
    rows = mrr.build_rows(events, candle_sets, s, e)
    trades = []
    for r in rows:
        if r.get("skip_reason"):
            continue
        if r.get("cell") == GO_CELL and r.get("entry_mode") == GO_ENTRY and r.get("base_exit") == GO_EXIT:
            sim = r.get("new_exit") or {}
            trades.append({
                "symbol": r["symbol"],
                "side": r.get("model_side"),
                "net": mrr.base.safe_float(sim.get("net_pct")),
                "gross": mrr.base.safe_float(sim.get("gross_pct")),
                "outcome": sim.get("outcome"),
            })
    return [t for t in trades if math.isfinite(t["net"])]


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def bootstrap_ci(vals, iters=5000, lo=2.5, hi=97.5):
    n = len(vals)
    means = []
    for _ in range(iters):
        sample = [vals[random.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    return means[int(lo / 100 * iters)], means[int(hi / 100 * iters)]


def sign_perm_pvalue(vals, iters=5000):
    """Null: trade magnitudes fixed, signs random. P(perm mean >= observed mean)."""
    obs = mean(vals)
    mags = [abs(v) for v in vals]
    ge = 0
    for _ in range(iters):
        pm = sum(m if random.random() < 0.5 else -m for m in mags) / len(mags)
        if pm >= obs:
            ge += 1
    return (ge + 1) / (iters + 1)


def main() -> int:
    trades = collect_go_trades()
    nets = [t["net"] for t in trades]
    n = len(nets)
    print(f"\n=== GO VALIDATION — {GO_CELL} {GO_ENTRY} {GO_EXIT} ===")
    print(f"trades n={n} | mean net={mean(nets):.3f}% | median={statistics.median(nets):.3f}% "
          f"| WR(net>0)={sum(1 for x in nets if x > 0)/n*100:.1f}%")

    # --- bootstrap CI + permutation ---
    lo, hi = bootstrap_ci(nets)
    p = sign_perm_pvalue(nets)
    print("\n--- significance ---")
    print(f"bootstrap 95% CI of mean net: [{lo:.3f}%, {hi:.3f}%]  -> {'EXCLUDES 0 (sig)' if lo > 0 else 'INCLUDES 0 (NOT sig)'}")
    print(f"sign-permutation p-value: {p:.3f}  -> {'significant' if p < 0.05 else 'NOT significant'}")

    # --- side split ---
    print("\n--- side split ---")
    for side in ("long", "short"):
        s = [t["net"] for t in trades if t["side"] == side]
        if s:
            print(f"{side:5} n={len(s):2} mean={mean(s):.3f}% WR={sum(1 for x in s if x>0)/len(s)*100:.0f}%")

    # --- pair-split (fat-tail check) ---
    print("\n--- pair split (sorted by total net) ---")
    by_pair = defaultdict(list)
    for t in trades:
        by_pair[t["symbol"]].append(t["net"])
    pair_tot = sorted(((sum(v), len(v), mean(v), k) for k, v in by_pair.items()), reverse=True)
    for tot, cnt, mn, sym in pair_tot:
        print(f"{sym:18} n={cnt:2} sum={tot:+.2f}% mean={mn:+.3f}%")
    top_sym = pair_tot[0][3]
    nets_ex_top = [t["net"] for t in trades if t["symbol"] != top_sym]
    print(f"\ntotal mean net           = {mean(nets):+.3f}% (n={n})")
    print(f"mean net EXCLUDING {top_sym:12} = {mean(nets_ex_top):+.3f}% (n={len(nets_ex_top)})  "
          f"-> {'survives' if mean(nets_ex_top) > 0 else 'COLLAPSES (fat-tail)'}")

    # --- cost stress (extra round-trip cost on top of baseline fee 0.20% + slip already in gross) ---
    print("\n--- cost stress (extra cost beyond baseline) ---")
    print(f"{'extra':>6} {'mean net':>9} {'long':>8} {'short':>8} {'WR':>5}")
    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]
    for extra in (0.0, 0.05, 0.10, 0.15, 0.25):
        adj = [x - extra for x in nets]
        al = [t["net"] - extra for t in longs]
        ash = [t["net"] - extra for t in shorts]
        wr = sum(1 for x in adj if x > 0) / n * 100
        print(f"{extra:>5.2f}% {mean(adj):>+8.3f}% {mean(al):>+7.3f}% {mean(ash):>+7.3f}% {wr:>4.0f}%")

    print("\n--- verdict guide ---")
    print("GO survives only if: CI excludes 0 AND both sides + AND survives top-pair drop AND net>0 at +0.10% extra cost.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
