"""
Distribution analysis for basis_pct and perp_div_4bar features.

Reads backtest_results_latest.json (produced by backtest_simulate.py after a run
with mark/index data enabled) and prints winner vs loser distribution stats.

Run after a full backtest:
    python scripts/analyze_basis_distribution.py
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

RESULTS_FILE = Path(__file__).parent / "backtest_runs" / "backtest_results_latest.json"


def _percentile(vals: list, p: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _stats(vals: list, label: str) -> None:
    vals = [v for v in vals if v is not None]
    if not vals:
        print(f"  {label}: нет данных")
        return
    print(
        f"  {label:30}  n={len(vals):4}  "
        f"mean={sum(vals)/len(vals):+.4f}  "
        f"p10={_percentile(vals,10):+.4f}  "
        f"p25={_percentile(vals,25):+.4f}  "
        f"p50={_percentile(vals,50):+.4f}  "
        f"p75={_percentile(vals,75):+.4f}  "
        f"p90={_percentile(vals,90):+.4f}"
    )


def _bucket_report(feature: str, results: list, buckets: list) -> None:
    """Show TP/SL/TIME breakdown per bucket of feature values."""
    print(f"\n  Breakdown по {feature}:")
    print(f"  {'Bucket':>16}  {'n':>5}  {'TP%':>6}  {'SL%':>6}  {'TIME%':>6}")
    for lo, hi in buckets:
        subset = [
            r for r in results
            if r.get(feature) is not None and lo <= r[feature] < hi
        ]
        tp = sum(1 for r in subset if r["outcome"] == "TP")
        sl = sum(1 for r in subset if r["outcome"] == "STOP")
        tx = sum(1 for r in subset if r["outcome"] == "TIME_EXIT")
        n  = len(subset)
        if n == 0:
            continue
        print(
            f"  [{lo:+.3f}, {hi:+.3f})  {n:5}  "
            f"{tp*100//n:5}%  {sl*100//n:5}%  {tx*100//n:5}%"
        )


def main():
    if not RESULTS_FILE.exists():
        print(f"ERROR: {RESULTS_FILE} not found — run backtest_simulate.py first")
        sys.exit(1)

    with open(RESULTS_FILE, encoding="utf-8") as f:
        results = json.load(f)

    print(f"Загружено {len(results)} сигналов из {RESULTS_FILE.name}")

    # Filter to records that have basis data
    with_basis = [r for r in results if r.get("basis_pct") is not None]
    with_pdiv  = [r for r in results if r.get("perp_div_4bar") is not None]
    print(f"  С basis_pct:     {len(with_basis)} / {len(results)}")
    print(f"  С perp_div_4bar: {len(with_pdiv)} / {len(results)}")

    wins   = [r for r in results if r["outcome"] == "TP"]
    losses = [r for r in results if r["outcome"] == "STOP"]
    time_x = [r for r in results if r["outcome"] == "TIME_EXIT"]
    print(f"  TP={len(wins)}  SL={len(losses)}  TIME={len(time_x)}")

    # ── basis_pct distribution ───────────────────────────────────────────────
    print("\n=== basis_pct (last - mark) / mark * 100 ===")
    print("  Positive = perp premium (perp expensive vs fair value)")
    _stats([r.get("basis_pct") for r in wins],   "Winners (TP)")
    _stats([r.get("basis_pct") for r in losses], "Losers  (SL)")
    _stats([r.get("basis_pct") for r in time_x], "TIME_EXIT")

    # Alignment check: does basis agree with trade direction?
    correct_basis_wins = [
        r for r in wins
        if r.get("basis_pct") is not None
        and (
            (r["side"] == "sell" and r["basis_pct"] > 0) or  # short + premium
            (r["side"] == "buy"  and r["basis_pct"] < 0)     # long  + discount
        )
    ]
    correct_basis_losses = [
        r for r in losses
        if r.get("basis_pct") is not None
        and (
            (r["side"] == "sell" and r["basis_pct"] > 0) or
            (r["side"] == "buy"  and r["basis_pct"] < 0)
        )
    ]
    wb = [r for r in wins   if r.get("basis_pct") is not None]
    lb = [r for r in losses if r.get("basis_pct") is not None]
    print("\n  Basis aligned with trade direction:")
    print(f"    Winners: {len(correct_basis_wins)}/{len(wb)} = "
          f"{len(correct_basis_wins)*100//len(wb) if wb else 0}%")
    print(f"    Losers:  {len(correct_basis_losses)}/{len(lb)} = "
          f"{len(correct_basis_losses)*100//len(lb) if lb else 0}%")

    _bucket_report("basis_pct", results, [
        (-1.0, -0.05), (-0.05, -0.02), (-0.02, 0.0),
        (0.0, 0.02), (0.02, 0.05), (0.05, 1.0),
    ])

    # ── perp_div_4bar distribution ────────────────────────────────────────────
    print("\n=== perp_div_4bar (perp 1h change - index 1h change, pct) ===")
    print("  Positive = perp moving faster than spot (momentum divergence)")
    _stats([r.get("perp_div_4bar") for r in wins],   "Winners (TP)")
    _stats([r.get("perp_div_4bar") for r in losses], "Losers  (SL)")
    _stats([r.get("perp_div_4bar") for r in time_x], "TIME_EXIT")

    _bucket_report("perp_div_4bar", results, [
        (-2.0, -0.5), (-0.5, -0.2), (-0.2, -0.05),
        (-0.05, 0.0), (0.0, 0.05), (0.05, 0.2), (0.2, 0.5), (0.5, 2.0),
    ])

    # ── perp_div directional analysis ─────────────────────────────────────────
    print("\n=== perp_div_4bar: направление сделки vs дивергенция ===")
    print("  'aligned' = перп идёт в сторону сделки (long + perp_div>0 или short + perp_div<0)")
    print("  'against' = перп идёт против сделки")
    print(f"\n  {'Combo':35}  {'n':>5}  {'TP%':>6}  {'SL%':>6}  {'WR(TP+SL)':>10}")
    combos = [
        ("LONG  + perp_div > 0 (perp ahead of spot)",  "buy",  True),
        ("LONG  + perp_div < 0 (perp behind spot)",    "buy",  False),
        ("SHORT + perp_div > 0 (perp ahead of spot)",  "sell", True),
        ("SHORT + perp_div < 0 (perp behind spot)",    "sell", False),
    ]
    for label, side, div_positive in combos:
        sub = [
            r for r in results
            if r["side"] == side
            and r.get("perp_div_4bar") is not None
            and (r["perp_div_4bar"] > 0) == div_positive
        ]
        tp = sum(1 for r in sub if r["outcome"] == "TP")
        sl = sum(1 for r in sub if r["outcome"] == "STOP")
        wr = tp * 100 // (tp + sl) if (tp + sl) > 0 else 0
        print(f"  {label:35}  {len(sub):5}  {tp*100//len(sub) if sub else 0:5}%  "
              f"{sl*100//len(sub) if sub else 0:5}%  {wr:8}%")

    # ── perp_div magnitude: extreme cases ─────────────────────────────────────
    print("\n=== perp_div_4bar: экстремальные значения (|div| > 0.1%) ===")
    extreme = [r for r in results if r.get("perp_div_4bar") is not None and abs(r["perp_div_4bar"]) > 0.1]
    normal  = [r for r in results if r.get("perp_div_4bar") is not None and abs(r["perp_div_4bar"]) <= 0.1]
    for label, sub in [("Extreme |div| > 0.1%", extreme), ("Normal  |div| <= 0.1%", normal)]:
        if not sub:
            continue
        tp = sum(1 for r in sub if r["outcome"] == "TP")
        sl = sum(1 for r in sub if r["outcome"] == "STOP")
        wr = tp * 100 // (tp + sl) if (tp + sl) > 0 else 0
        print(f"  {label}: n={len(sub)}, TP={tp*100//len(sub) if sub else 0}%, "
              f"SL={sl*100//len(sub) if sub else 0}%, WR={wr}%")

    # ── Per-regime breakdown ──────────────────────────────────────────────────
    print("\n=== По режимам (perp_div) ===")
    for regime in ["TRENDING", "DRIFT", "RANGING"]:
        sub = [r for r in results if r.get("regime") == regime and r.get("perp_div_4bar") is not None]
        if not sub:
            continue
        neg = [r for r in sub if r["perp_div_4bar"] < 0]
        pos = [r for r in sub if r["perp_div_4bar"] >= 0]
        tp_neg = sum(1 for r in neg if r["outcome"] == "TP")
        sl_neg = sum(1 for r in neg if r["outcome"] == "STOP")
        tp_pos = sum(1 for r in pos if r["outcome"] == "TP")
        sl_pos = sum(1 for r in pos if r["outcome"] == "STOP")
        wr_neg = tp_neg * 100 // (tp_neg + sl_neg) if (tp_neg + sl_neg) > 0 else 0
        wr_pos = tp_pos * 100 // (tp_pos + sl_pos) if (tp_pos + sl_pos) > 0 else 0
        print(f"\n  {regime} — div<0: n={len(neg)}, WR={wr_neg}%  |  div>0: n={len(pos)}, WR={wr_pos}%")


if __name__ == "__main__":
    main()
