"""
Tape analysis for BB Fade winning vs losing trades.

For each trade from bt_bb_fade backtest:
  - Load tick data from E:/trading-data/ticks/{sym}/{date}.csv
  - Compute taker_buy_ratio and CVD in window [-5min, 0] before entry
  - Compare TP vs SL distributions
"""

import gzip
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TAPE_ROOT  = Path("E:/trading-data/ticks")
WINDOW_MS  = 5 * 60 * 1000   # 5 minutes before entry


def load_tape_window(sym: str, ts_ms: int) -> list:
    """Load ticks in [ts_ms - WINDOW_MS, ts_ms + 60000] from tape files."""
    date_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    sym_dir  = TAPE_ROOT / sym

    ticks = []
    for fname in [f"{date_str}.csv", f"{date_str}.csv.gz"]:
        fpath = sym_dir / fname
        if not fpath.exists():
            continue
        opener = gzip.open if fname.endswith(".gz") else open
        with opener(fpath, "rt") as f:
            next(f)  # skip header
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 6 or not parts[5].strip():
                    continue
                t = int(parts[0])
                if t < ts_ms - WINDOW_MS:
                    continue
                if t > ts_ms + 60_000:
                    break
                ticks.append((t, parts[3], float(parts[5])))  # ts, side, size
        break
    return ticks


def calc_tape_metrics(ticks: list, entry_ts: int) -> dict:
    """Compute CVD and taker_buy_ratio in window before entry."""
    pre  = [(t, s, sz) for t, s, sz in ticks if t <= entry_ts]
    post = [(t, s, sz) for t, s, sz in ticks if entry_ts < t <= entry_ts + 60_000]

    def metrics(rows):
        buy_vol  = sum(sz for _, s, sz in rows if s == "buy")
        sell_vol = sum(sz for _, s, sz in rows if s == "sell")
        total    = buy_vol + sell_vol
        ratio    = buy_vol / total if total > 0 else 0.5
        cvd      = buy_vol - sell_vol
        return ratio, cvd, total

    pre_ratio, pre_cvd, pre_total   = metrics(pre)
    post_ratio, post_cvd, post_total = metrics(post)

    return {
        "pre_buy_ratio": pre_ratio,
        "pre_cvd":       pre_cvd,
        "pre_total":     pre_total,
        "post_buy_ratio": post_ratio,
        "post_cvd":       post_cvd,
    }


def run():
    # Import backtest
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bt_bb_fade",
        ROOT / "scripts" / "backtest" / "bt_bb_fade.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    pairs = sorted({
        p.stem.split("_15m_")[0]
        for p in mod.CACHE.glob("*_15m_60d.pkl")
    })

    all_trades = []
    for sym in pairs:
        all_trades.extend(mod.backtest_pair(sym))

    excl = [t for t in all_trades if t["outcome"] != "TIME"]
    print(f"Trades to analyze: {len(excl)}")

    results = []
    missing = 0
    for t in excl:
        sym = t["sym"]
        ts  = t["entry_ts"]

        if not (TAPE_ROOT / sym).exists():
            missing += 1
            continue

        ticks = load_tape_window(sym, ts)
        if len(ticks) < 10:
            missing += 1
            continue

        m = calc_tape_metrics(ticks, ts)
        results.append({**t, **m})

    print(f"Analyzed: {len(results)}  Missing tape: {missing}\n")

    if not results:
        print("No tape data found.")
        return

    tp_trades = [r for r in results if r["outcome"] == "TP"]
    sl_trades = [r for r in results if r["outcome"] == "SL"]

    def avg(lst, key):
        return sum(r[key] for r in lst) / len(lst) if lst else 0

    print("=" * 55)
    print("TAPE METRICS: TP vs SL")
    print("=" * 55)
    print(f"{'Metric':<25}  {'TP':>8}  {'SL':>8}  {'Delta':>8}")
    print("-" * 55)

    metrics_list = [
        ("pre_buy_ratio (5m)", "pre_buy_ratio"),
        ("pre_cvd (norm)", "pre_cvd"),
        ("post_buy_ratio (1m)", "post_buy_ratio"),
    ]

    for label, key in metrics_list:
        tp_val = avg(tp_trades, key)
        sl_val = avg(sl_trades, key)
        delta  = tp_val - sl_val
        print(f"  {label:<23}  {tp_val:>8.3f}  {sl_val:>8.3f}  {delta:>+8.3f}")

    # Buy ratio buckets
    print("\n=== Buy ratio przed wejsciem (pre_buy_ratio) ===")
    buckets = {"<0.3": [], "0.3-0.5": [], "0.5-0.7": [], ">0.7": []}
    for r in results:
        br = r["pre_buy_ratio"]
        if br < 0.3:
            buckets["<0.3"].append(r)
        elif br < 0.5:
            buckets["0.3-0.5"].append(r)
        elif br < 0.7:
            buckets["0.5-0.7"].append(r)
        else:
            buckets[">0.7"].append(r)

    print(f"  {'Bucket':<12}  n     WR     avg_net")
    for k, trades in buckets.items():
        if not trades:
            continue
        wr  = sum(1 for x in trades if x["outcome"] == "TP") / len(trades) * 100
        an  = avg(trades, "net")
        print(f"  {k:<12}  {len(trades):<5} {wr:.0f}%   {an:+.3f}%")

    # Side-specific analysis
    print("\n=== Buy ratio по side ===")
    for side in ["sell", "buy"]:
        side_tp = [r for r in tp_trades if r["side"] == side]
        side_sl = [r for r in sl_trades if r["side"] == side]
        if not side_tp and not side_sl:
            continue
        tp_ratio = avg(side_tp, "pre_buy_ratio") if side_tp else 0
        sl_ratio = avg(side_sl, "pre_buy_ratio") if side_sl else 0
        print(f"  {side:<6}  TP pre_buy_ratio={tp_ratio:.3f}  SL pre_buy_ratio={sl_ratio:.3f}  delta={tp_ratio-sl_ratio:+.3f}")


if __name__ == "__main__":
    run()
