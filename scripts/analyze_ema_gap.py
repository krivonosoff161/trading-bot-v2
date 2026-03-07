"""
Mini-backtest: EMA gap filter analysis.

Tests whether filtering entries by abs(ema_gap) / atr ratio
would improve P&L on the 07.03.2026 session.

Data: extracted from logs_archive/logs_2026-03-07_23-32 + OKX trade history.
Run: python scripts/analyze_ema_gap.py
"""

# Trade data from the session: (time, symbol, side, ema_gap, atr, pnl, outcome)
# outcome: TP = take profit, SL = stop loss, M = manual close
TRADES = [
    ("13:49", "BTC", "sell",   0.16,  51.51,  +0.57, "TP"),
    ("13:54", "BTC", "sell",   2.33,  57.46,  +0.67, "TP"),
    ("14:00", "BTC", "sell",   5.03,  65.61,  +0.74, "TP"),
    ("14:07", "BTC", "sell",  34.51,  68.52,  -2.67, "SL"),
    ("14:48", "BTC", "sell",   3.26,  70.45,  +1.09, "TP"),
    ("15:17", "BTC", "sell",  33.29,  66.73,  -2.16, "SL"),
    ("16:05", "BTC", "sell",  42.58,  93.67,  +1.56, "TP"),
    ("16:19", "SOL", "buy",    0.08,   0.49,  +1.76, "TP"),
    ("16:37", "BTC", "sell", 167.23, 134.21,  -2.38, "SL"),
    ("16:45", "BTC", "sell",  24.27, 106.10,  -2.12, "SL"),
    ("16:58", "SOL", "buy",    0.20,   0.62,  -1.55, "SL"),
    ("17:12", "BTC", "sell",   7.09,  77.68,  +1.29, "TP"),
    ("17:17", "SOL", "sell",   0.45,   0.49,  -1.33, "SL"),
    ("17:29", "BTC", "sell", 137.85, 162.05,  -2.69, "SL"),
    ("18:17", "BTC", "sell",  65.51,  94.91,  +2.02, "TP"),
    ("18:27", "SOL", "buy",    0.14,   0.55,  +1.81, "TP"),
    ("18:34", "BTC", "sell",  67.71,  87.05,  -2.38, "SL"),
    ("18:47", "SOL", "buy",    0.37,   0.62,  -1.40, "SL"),
    ("19:15", "SOL", "sell",   0.35,   0.60,  +1.18,  "M"),
    ("23:10", "BTC", "sell", 484.24, 162.34,  -2.99, "SL"),
    ("23:11", "BTC", "sell", 326.08, 229.27,  +5.04, "TP"),
    ("23:23", "BTC", "sell", 178.82, 207.39,  -4.19, "SL"),
    ("23:24", "BTC", "sell",  86.72, 222.32,  +0.62,  "M"),
]

COMMISSION_PER_TRADE = 0.66  # USDT round-trip (0.05% × 2 × ~$660 for BTC)
SOL_COMMISSION = 0.08        # USDT round-trip for SOL


def simulate(max_gap_atr: float) -> dict:
    kept, filtered = [], []
    for t in TRADES:
        time, sym, side, gap, atr, pnl, outcome = t
        ratio = gap / atr if atr > 0 else 0
        comm = SOL_COMMISSION if sym == "SOL" else COMMISSION_PER_TRADE
        if ratio <= max_gap_atr:
            kept.append((time, sym, pnl, outcome, ratio, comm))
        else:
            filtered.append((time, sym, pnl, outcome, ratio, comm))

    gross = sum(t[2] for t in kept)
    comm_total = sum(t[5] for t in kept)
    net = gross - comm_total
    wins = [t for t in kept if t[2] > 0]
    losses = [t for t in kept if t[2] < 0]
    wr = len(wins) / len(kept) * 100 if kept else 0

    filtered_pnl = sum(t[2] for t in filtered)

    return {
        "threshold": max_gap_atr,
        "kept": len(kept),
        "filtered": len(filtered),
        "wins": len(wins),
        "losses": len(losses),
        "wr": wr,
        "gross": gross,
        "comm": comm_total,
        "net": net,
        "filtered_pnl": filtered_pnl,
        "filtered_trades": filtered,
    }


def main():
    print("=" * 72)
    print("EMA GAP FILTER BACKTEST — 07.03.2026 session (23 trades)")
    print("=" * 72)

    # Baseline (no filter)
    base = simulate(9999)
    print(f"\nBASELINE (no filter): {base['kept']} trades | "
          f"WR={base['wr']:.0f}% | gross={base['gross']:+.2f} | "
          f"comm={base['comm']:.2f} | NET={base['net']:+.2f} USDT\n")

    print(f"{'Threshold':>10} {'Kept':>5} {'Filtered':>9} {'WR':>6} "
          f"{'Gross':>8} {'Comm':>6} {'NET':>8} {'Filtered PnL':>13}")
    print("-" * 72)

    thresholds = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0, 1.5, 2.0, 3.0]
    best_net = base["net"]
    best_thresh = None

    for t in thresholds:
        r = simulate(t)
        marker = " ◄ BEST" if r["net"] > best_net else ""
        if r["net"] > best_net:
            best_net = r["net"]
            best_thresh = t
        print(f"{t:>10.1f}x {r['kept']:>5} {r['filtered']:>9} "
              f"{r['wr']:>5.0f}% {r['gross']:>+8.2f} {r['comm']:>6.2f} "
              f"{r['net']:>+8.2f}{marker}")

    print("-" * 72)
    if best_thresh:
        print(f"\nBEST THRESHOLD: {best_thresh}x ATR → NET={best_net:+.2f} USDT")
        r = simulate(best_thresh)
        print(f"\nFiltered out trades at threshold {best_thresh}x:")
        for t in r["filtered_trades"]:
            time, sym, pnl, outcome, ratio, _ = t
            print(f"  {time} {sym:3} {outcome}  ratio={ratio:.2f}x  pnl={pnl:+.2f}")
    else:
        print("\nNo threshold improves over baseline.")

    # Show all trades sorted by ratio for visual analysis
    print("\n" + "=" * 72)
    print("ALL TRADES sorted by ema_gap/atr ratio:")
    print(f"{'Time':>6} {'Sym':>4} {'Outcome':>7} {'Ratio':>7} {'PnL':>8}")
    print("-" * 40)
    sorted_trades = sorted(TRADES, key=lambda x: x[3] / x[4])
    for t in sorted_trades:
        time, sym, side, gap, atr, pnl, outcome = t
        ratio = gap / atr
        marker = " ✓" if pnl > 0 else " ✗"
        print(f"{time:>6} {sym:>4} {outcome:>7}  {ratio:>6.2f}x  {pnl:>+8.2f}{marker}")


if __name__ == "__main__":
    main()
