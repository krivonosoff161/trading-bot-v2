"""Stage 1 — diagnostics of SHORT cohort.

Question: why SELL WR = 34.8% (live) / what edge exists?
Should we ban shorts completely or add a veto filter?

Data sources:
- signal_log.jsonl + signal_labels.jsonl (82 live signals, real exit_r)
- pattern_db.csv (456 backtest ENTRY rows with fwd_ret + fwd_outcome)
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
LOG_PATH    = HERE / "signal_log.jsonl"
LABELS_PATH = HERE / "signal_labels.jsonl"
PDB_PATH    = HERE / "pattern_db.csv"

# ── Load live data ─────────────────────────────────────────────────────────────

def load_live() -> list[dict]:
    logs   = {}
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        logs[e["signal_id"]] = e

    merged = []
    for line in LABELS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        lbl = json.loads(line)
        log = logs.get(lbl["signal_id"], {})
        merged.append({**log, **lbl})
    return merged


def load_pdb_entries() -> list[dict]:
    rows = []
    with PDB_PATH.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get("entry_signal") != "ENTRY":
                continue
            for k in ("close", "adx_1h", "adx_4h", "di_spread_1h", "vol_ratio",
                     "day_position", "funding", "perp_div", "slope_1h", "slope_15m",
                     "fwd_ret_60m", "fwd_ret_150m", "fwd_ret_300m", "signal_hour"):
                try:
                    r[k] = float(r[k]) if r.get(k) not in (None, "") else None
                except (ValueError, TypeError):
                    r[k] = None
            rows.append(r)
    return rows


# ── Metrics ────────────────────────────────────────────────────────────────────

def pf_wr(exit_rs: list[float]) -> tuple[float, float, int]:
    if not exit_rs:
        return 0.0, 0.0, 0
    wins = [x for x in exit_rs if x > 0]
    losses = [abs(x) for x in exit_rs if x < 0]
    wr = len(wins) / len(exit_rs) * 100
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else float("inf")
    return pf, wr, len(exit_rs)


def avg_r(exit_rs: list[float]) -> float:
    return mean(exit_rs) if exit_rs else 0.0


# ── Reporters ──────────────────────────────────────────────────────────────────

def report_cut(title: str, sell_by_key: dict[str, list[float]], min_n: int = 5) -> None:
    print(f"\n── {title} ──")
    print(f"{'key':<20} {'n':>4} {'WR%':>6} {'PF':>6} {'avg_R':>7}")
    total = []
    rows = []
    for key in sorted(sell_by_key.keys(), key=str):
        rs = sell_by_key[key]
        if len(rs) < min_n:
            continue
        pf, wr, n = pf_wr(rs)
        rows.append((key, n, wr, pf, avg_r(rs)))
        total.extend(rs)
    rows.sort(key=lambda x: x[3], reverse=True)
    for key, n, wr, pf, av in rows:
        pf_s = f"{pf:.2f}" if pf != float("inf") else "∞"
        print(f"{str(key):<20} {n:>4} {wr:>5.1f}% {pf_s:>6} {av:>+6.3f}")
    if total:
        pf, wr, n = pf_wr(total)
        pf_s = f"{pf:.2f}" if pf != float("inf") else "∞"
        print(f"{'TOTAL':<20} {n:>4} {wr:>5.1f}% {pf_s:>6} {avg_r(total):>+6.3f}")


def bucket(val, bins: list[tuple[float, float, str]]) -> str:
    if val is None:
        return "?"
    for lo, hi, label in bins:
        if lo <= val < hi:
            return label
    return ">"


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print("═" * 66)
    print("STAGE 1 — SHORT DIAGNOSTICS")
    print("═" * 66)

    live = load_live()
    live_sells = [x for x in live if x.get("side") == "sell"]
    live_buys  = [x for x in live if x.get("side") == "buy"]

    print(f"\nLive: total={len(live)}  BUY={len(live_buys)}  SELL={len(live_sells)}")
    if live_buys:
        pf, wr, n = pf_wr([x["exit_r"] for x in live_buys])
        print(f"  BUY : n={n} WR={wr:.1f}% PF={pf:.2f} avg_R={avg_r([x['exit_r'] for x in live_buys]):+.3f}")
    if live_sells:
        pf, wr, n = pf_wr([x["exit_r"] for x in live_sells])
        print(f"  SELL: n={n} WR={wr:.1f}% PF={pf:.2f} avg_R={avg_r([x['exit_r'] for x in live_sells]):+.3f}")

    # ── Live SELL cuts ────────────────────────────────────────────────────────
    print("\n" + "═" * 66)
    print("LIVE SELL — CUTS (min n=4)")
    print("═" * 66)

    # regime
    by_regime = defaultdict(list)
    for s in live_sells:
        by_regime[s.get("regime", "?")].append(s["exit_r"])
    report_cut("by regime", by_regime, min_n=4)

    # symbol
    by_sym = defaultdict(list)
    for s in live_sells:
        by_sym[s.get("symbol", "?")].append(s["exit_r"])
    report_cut("by symbol", by_sym, min_n=4)

    # style
    by_style = defaultdict(list)
    for s in live_sells:
        by_style[s.get("style", "?")].append(s["exit_r"])
    report_cut("by style", by_style, min_n=4)

    # funding sign
    by_fund = defaultdict(list)
    for s in live_sells:
        f = s.get("funding")
        if f is None:
            k = "?"
        elif f > 0.0003:
            k = "pos>0.03%"
        elif f > 0.0001:
            k = "pos_small"
        elif f < -0.0003:
            k = "neg<-0.03%"
        elif f < -0.0001:
            k = "neg_small"
        else:
            k = "near_zero"
        by_fund[k].append(s["exit_r"])
    report_cut("by funding", by_fund, min_n=4)

    # hour
    by_hour = defaultdict(list)
    for s in live_sells:
        ts = s.get("ts_ms")
        if ts:
            hr = (ts // 1000 // 3600) % 24
            if 1 <= hr < 7:
                k = "night(1-7)"
            elif 7 <= hr < 13:
                k = "morn(7-13)"
            elif 13 <= hr < 19:
                k = "day(13-19)"
            else:
                k = "evng(19-1)"
            by_hour[k].append(s["exit_r"])
    report_cut("by session", by_hour, min_n=4)

    # day_position (sell: should fade tops → expect <0.5 bad, >0.7 good)
    by_dp = defaultdict(list)
    for s in live_sells:
        dp = s.get("day_position")
        if dp is None:
            k = "?"
        elif dp < 0.3:
            k = "<0.3_lows"
        elif dp < 0.5:
            k = "0.3-0.5"
        elif dp < 0.7:
            k = "0.5-0.7"
        else:
            k = ">0.7_highs"
        by_dp[k].append(s["exit_r"])
    report_cut("by day_position", by_dp, min_n=4)

    # adx_1h
    by_adx = defaultdict(list)
    for s in live_sells:
        a = s.get("adx_1h")
        if a is None:
            k = "?"
        elif a < 15:
            k = "<15"
        elif a < 20:
            k = "15-20"
        elif a < 26:
            k = "20-26"
        else:
            k = "26+"
        by_adx[k].append(s["exit_r"])
    report_cut("by adx_1h", by_adx, min_n=4)

    # vol_ratio
    by_vol = defaultdict(list)
    for s in live_sells:
        v = s.get("vol_ratio")
        if v is None:
            k = "?"
        elif v < 1.5:
            k = "<1.5"
        elif v < 3.0:
            k = "1.5-3"
        elif v < 5.0:
            k = "3-5"
        else:
            k = "5+"
        by_vol[k].append(s["exit_r"])
    report_cut("by vol_ratio", by_vol, min_n=4)

    # obi5
    by_obi = defaultdict(list)
    for s in live_sells:
        o = s.get("obi5")
        if o is None:
            k = "?"
        elif o > 0.3:
            k = "OBI>+0.3"      # book leans long — bad for short
        elif o > 0:
            k = "OBI_0_+0.3"
        elif o > -0.3:
            k = "OBI_-0.3_0"
        else:
            k = "OBI<-0.3"
        by_obi[k].append(s["exit_r"])
    report_cut("by OBI top5", by_obi, min_n=4)

    # ── Backtest SELL cross-validation ────────────────────────────────────────
    print("\n" + "═" * 66)
    print("BACKTEST (pattern_db.csv) — SELL fwd_ret_150m")
    print("═" * 66)

    try:
        pdb = load_pdb_entries()
    except FileNotFoundError:
        print("pattern_db.csv not found — skipping backtest cross-check")
        return

    pdb_sells = [r for r in pdb if r.get("side") == "sell" and r.get("fwd_ret_150m") is not None]
    pdb_buys  = [r for r in pdb if r.get("side") == "buy"  and r.get("fwd_ret_150m") is not None]

    def side_adj(r: dict, col: str = "fwd_ret_150m") -> float:
        v = r.get(col)
        return -v if r["side"] == "sell" else v

    print(f"\nBacktest ENTRY: BUY={len(pdb_buys)}  SELL={len(pdb_sells)}")
    if pdb_buys:
        print(f"  BUY  fwd_ret_150m: avg={mean([r['fwd_ret_150m'] for r in pdb_buys]):+.4f}  "
              f"side_adj_avg={mean([side_adj(r) for r in pdb_buys]):+.4f}")
    if pdb_sells:
        print(f"  SELL fwd_ret_150m: avg={mean([r['fwd_ret_150m'] for r in pdb_sells]):+.4f}  "
              f"side_adj_avg={mean([side_adj(r) for r in pdb_sells]):+.4f}")

    # backtest SELL by regime
    def bt_cut(title: str, buckets: dict[str, list[dict]], min_n: int = 10) -> None:
        print(f"\n── {title} ──")
        print(f"{'key':<20} {'n':>4} {'WR_adj%':>8} {'avg_adj':>9}")
        rows = []
        for key, rs in buckets.items():
            if len(rs) < min_n:
                continue
            adj = [side_adj(r) for r in rs]
            wr = sum(1 for x in adj if x > 0) / len(adj) * 100
            rows.append((key, len(rs), wr, mean(adj)))
        rows.sort(key=lambda x: x[3], reverse=True)
        for key, n, wr, av in rows:
            print(f"{str(key):<20} {n:>4} {wr:>7.1f}% {av:>+8.4f}")

    by_regime_bt = defaultdict(list)
    for r in pdb_sells:
        by_regime_bt[r.get("regime", "?")].append(r)
    bt_cut("SELL by regime (backtest)", by_regime_bt, min_n=10)

    by_sym_bt = defaultdict(list)
    for r in pdb_sells:
        by_sym_bt[r.get("symbol", "?")].append(r)
    bt_cut("SELL by symbol (backtest)", by_sym_bt, min_n=10)

    by_style_bt = defaultdict(list)
    for r in pdb_sells:
        by_style_bt[r.get("trade_style", "?")].append(r)
    bt_cut("SELL by style (backtest)", by_style_bt, min_n=10)

    by_fund_bt = defaultdict(list)
    for r in pdb_sells:
        f = r.get("funding")
        if f is None:
            k = "?"
        elif f > 0.0003:
            k = "pos>0.03%"
        elif f > 0.0001:
            k = "pos_small"
        elif f < -0.0003:
            k = "neg<-0.03%"
        elif f < -0.0001:
            k = "neg_small"
        else:
            k = "near_zero"
        by_fund_bt[k].append(r)
    bt_cut("SELL by funding (backtest)", by_fund_bt, min_n=10)

    by_hour_bt = defaultdict(list)
    for r in pdb_sells:
        h = r.get("signal_hour")
        if h is None:
            k = "?"
        else:
            h = int(h)
            if 1 <= h < 7:
                k = "night(1-7)"
            elif 7 <= h < 13:
                k = "morn(7-13)"
            elif 13 <= h < 19:
                k = "day(13-19)"
            else:
                k = "evng(19-1)"
        by_hour_bt[k].append(r)
    bt_cut("SELL by session (backtest)", by_hour_bt, min_n=10)

    by_dp_bt = defaultdict(list)
    for r in pdb_sells:
        dp = r.get("day_position")
        if dp is None:
            k = "?"
        elif dp < 0.3:
            k = "<0.3_lows"
        elif dp < 0.5:
            k = "0.3-0.5"
        elif dp < 0.7:
            k = "0.5-0.7"
        else:
            k = ">0.7_highs"
        by_dp_bt[k].append(r)
    bt_cut("SELL by day_position (backtest)", by_dp_bt, min_n=10)

    by_pd_bt = defaultdict(list)
    for r in pdb_sells:
        pd = r.get("perp_div")
        if pd is None:
            k = "?"
        elif pd > 0.3:
            k = "div>+0.3"
        elif pd > 0:
            k = "div_0_+0.3"
        elif pd > -0.3:
            k = "div_-0.3_0"
        else:
            k = "div<-0.3"
        by_pd_bt[k].append(r)
    bt_cut("SELL by perp_div (backtest)", by_pd_bt, min_n=10)


if __name__ == "__main__":
    main()
