"""
BB Fade backtester v3 — MTF: 15m setup + 5m wick-rejection entry.

Flow:
  1. 15m candle touches BB (high>=upper15 OR low<=lower15) → setup armed
  2. Look at next up to 3 five-minute candles for wick rejection
     SHORT: 5m HIGH >= upper15 AND 5m close < upper15
     LONG:  5m LOW  <= lower15 AND 5m close > lower15
  3. Enter on first confirming 5m candle
  Skip: TRENDING on 1H (ADX>=22 and DI_spread>=10)

TP = 15m BB midline, SL = entry +/- band_width15 * SL_MULT

Pattern analysis: RSI, vol_ratio, band_width, hour_utc.
"""

import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.strategy.indicators import calc_adx  # noqa: E402

CACHE = ROOT / "scripts" / "backtest" / "cache" / "screener"
FEE_RT = 0.10 / 100
BB_PERIOD = 20
BB_STD = 2.0
SL_MULT = 0.5
MAX_HOLD = 16  # 5m bars = 80 min max hold
CONFIRM_BARS = 3  # max 5m bars to wait for wick rejection after 15m touch
COOLDOWN = 2  # 15m bars between setups same pair
MIN_WIDTH = 2.0  # skip if 15m band_width/price < 2.0%


def load(sym: str, tf: str) -> list:
    p = CACHE / f"{sym}_{tf}_60d.pkl"
    return pickle.load(open(p, "rb")) if p.exists() else []


def _calc_rsi_array(closes, period=14):
    c = np.array(closes, dtype=float)
    n = len(c)
    rsi = np.full(n, 50.0)
    if n < period + 1:
        return rsi
    deltas = np.diff(c)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = float(np.mean(gains[:period]))
    avg_l = float(np.mean(losses[:period]))
    for i in range(period, n - 1):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rs = avg_g / avg_l if avg_l > 0 else 100.0
        rsi[i + 1] = 100 - 100 / (1 + rs)
    return rsi


def calc_bb(closes):
    c = np.array(closes, dtype=float)
    mid, upper, lower = [], [], []
    for i in range(len(c)):
        if i < BB_PERIOD - 1:
            mid.append(np.nan)
            upper.append(np.nan)
            lower.append(np.nan)
            continue
        w = c[i - BB_PERIOD + 1 : i + 1]
        m = float(np.mean(w))
        s = float(np.std(w, ddof=0))
        mid.append(m)
        upper.append(m + BB_STD * s)
        lower.append(m - BB_STD * s)
    return mid, upper, lower


def build_adx_map(c1h):
    """Returns dict: hour_ts_ms -> trending (bool)."""
    if len(c1h) < 25:
        return {}
    H = [float(c[2]) for c in c1h]
    L = [float(c[3]) for c in c1h]
    C = [float(c[4]) for c in c1h]
    result = {}
    for i in range(len(c1h)):
        if i < 20:
            result[c1h[i][0]] = False
            continue
        adx, pdi, ndi = calc_adx(H[: i + 1], L[: i + 1], C[: i + 1], period=9)
        result[c1h[i][0]] = adx >= 22 and abs(pdi - ndi) >= 10
    return result


def build_5m_index(c5):
    """Map each 5m timestamp -> index for fast lookup."""
    return {c[0]: idx for idx, c in enumerate(c5)}


def backtest_pair(sym: str) -> list:
    c15 = load(sym, "15m")
    c5 = load(sym, "5m")
    c1h = load(sym, "1H")
    if len(c15) < BB_PERIOD + 20 or len(c5) < 50 or not c1h:
        return []

    # 15m arrays
    closes15 = [float(c[4]) for c in c15]
    highs15 = [float(c[2]) for c in c15]
    lows15 = [float(c[3]) for c in c15]
    vols15 = [float(c[5]) for c in c15]
    ts15 = [c[0] for c in c15]

    mid15, upper15, lower15 = calc_bb(closes15)
    rsi15 = _calc_rsi_array(closes15, period=14)

    # 5m arrays
    highs5 = [float(c[2]) for c in c5]
    lows5 = [float(c[3]) for c in c5]
    closes5 = [float(c[4]) for c in c5]
    _vols5 = [float(c[5]) for c in c5]
    ts5 = [c[0] for c in c5]
    ts5_idx = {t: i for i, t in enumerate(ts5)}

    adx_map = build_adx_map(c1h)

    trades = []
    last_setup_bar = -COOLDOWN - 1

    for i15 in range(BB_PERIOD + 2, len(c15) - 5):
        if np.isnan(upper15[i15]):
            continue

        bw = upper15[i15] - lower15[i15]
        if bw / closes15[i15] * 100 < MIN_WIDTH:
            continue

        if i15 - last_setup_bar < COOLDOWN:
            continue

        # 1H trending filter
        h1_ts = (ts15[i15] // 3_600_000) * 3_600_000
        if adx_map.get(h1_ts, False):
            continue

        h15, l15 = highs15[i15], lows15[i15]

        # 15m must touch the band (setup condition)
        touched_upper = h15 >= upper15[i15]
        touched_lower = l15 <= lower15[i15]
        if not touched_upper and not touched_lower:
            continue

        # Determine setup side
        if touched_upper and not touched_lower:
            setup_side = "sell"
        elif touched_lower and not touched_upper:
            setup_side = "buy"
        else:
            # touched both — skip ambiguous
            continue

        last_setup_bar = i15

        # Find corresponding 5m bars: from start of this 15m candle + next CONFIRM_BARS
        # 15m candle start = ts15[i15], end = ts15[i15] + 14*60*1000
        # Look at 5m bars starting from ts15[i15]
        start5_ts = ts15[i15]
        start5_idx = ts5_idx.get(start5_ts)
        if start5_idx is None:
            # find nearest 5m bar at or after this 15m candle
            for k, t in enumerate(ts5):
                if t >= start5_ts:
                    start5_idx = k
                    break
        if start5_idx is None:
            continue

        # Search for wick rejection in 5m candles of this 15m bar + next bar
        search_end = min(start5_idx + 3 + CONFIRM_BARS, len(c5) - MAX_HOLD - 2)
        entry_bar5 = None

        for j5 in range(start5_idx, search_end):
            h5, l5, c5_close = highs5[j5], lows5[j5], closes5[j5]
            if setup_side == "sell":
                if h5 >= upper15[i15] and c5_close < upper15[i15]:
                    entry_bar5 = j5
                    break
            else:
                if l5 <= lower15[i15] and c5_close > lower15[i15]:
                    entry_bar5 = j5
                    break

        if entry_bar5 is None:
            continue

        if entry_bar5 + MAX_HOLD >= len(c5):
            continue

        entry = closes5[entry_bar5]
        tp = mid15[i15]
        if setup_side == "sell":
            sl = upper15[i15] + bw * SL_MULT
        else:
            sl = lower15[i15] - bw * SL_MULT

        tp_dist = abs(entry - tp)
        sl_dist = abs(entry - sl)
        if tp_dist < sl_dist * 0.3:
            continue

        # Vol ratio on 15m
        baseline_vol = (
            float(np.mean(vols15[max(0, i15 - 10) : i15])) if i15 >= 5 else 1.0
        )
        vol_ratio = vols15[i15] / baseline_vol if baseline_vol > 0 else 1.0

        rsi_val = float(rsi15[i15]) if not np.isnan(rsi15[i15]) else 50.0
        hour_utc = (ts15[i15] // 3_600_000) % 24
        bw_pct = bw / closes15[i15] * 100

        # Research-based filters
        if setup_side == "sell" and rsi_val > 60:  # high RSI = breakout not fade
            continue
        if setup_side == "buy" and rsi_val < 40:  # low RSI = downtrend impulse
            continue
        if vol_ratio > 1.5:  # high vol = momentum, not reversion
            continue
        if bw_pct < 1.0:  # squeeze = expect breakout
            continue
        if hour_utc < 8:  # Asia session underperforms
            continue

        # Simulate on 5m bars
        outcome = "TIME"
        exit_price = closes5[entry_bar5 + MAX_HOLD]
        hold_bars = MAX_HOLD

        for j in range(entry_bar5 + 1, entry_bar5 + MAX_HOLD + 1):
            hj, lj = highs5[j], lows5[j]
            hold_bars = j - entry_bar5
            if setup_side == "sell":
                if hj >= sl:
                    outcome, exit_price = "SL", sl
                    break
                if lj <= tp:
                    outcome, exit_price = "TP", tp
                    break
            else:
                if lj <= sl:
                    outcome, exit_price = "SL", sl
                    break
                if hj >= tp:
                    outcome, exit_price = "TP", tp
                    break

        gross = ((exit_price - entry) / entry * 100) * (
            1 if setup_side == "buy" else -1
        )
        net = gross - FEE_RT * 100

        trades.append(
            {
                "sym": sym,
                "side": setup_side,
                "outcome": outcome,
                "net": net,
                "hold_bars": hold_bars,
                "vol_ratio": vol_ratio,
                "rsi": rsi_val,
                "bw_pct": bw_pct,
                "hour_utc": hour_utc,
                "entry_ts": ts5[entry_bar5],
                "entry_price": entry,
            }
        )

    return trades


def print_breakdown(label: str, groups: dict):
    print(f"\n=== {label} ===")
    rows = []
    for key, trades in groups.items():
        w = sum(1 for t in trades if t["outcome"] == "TP")
        avg = sum(t["net"] for t in trades) / len(trades)
        rows.append((key, len(trades), w / len(trades) * 100, avg))
    rows.sort(key=lambda x: x[0] if isinstance(x[0], (int, float)) else str(x[0]))
    for key, n, wr, avg in rows:
        bar = "#" * int(wr / 5)
        print(f"  {str(key):<12}  n={n:>4}  WR={wr:>5.1f}%  {bar}  avg={avg:+.3f}%")


def main():
    pairs = sorted({p.stem.split("_15m_")[0] for p in CACHE.glob("*_15m_60d.pkl")})

    all_trades = []
    for sym in pairs:
        t = backtest_pair(sym)
        all_trades.extend(t)
        if t:
            excl = [x for x in t if x["outcome"] != "TIME"]
            if excl:
                wr = sum(1 for x in excl if x["outcome"] == "TP") / len(excl) * 100
                print(f"  {sym:<25}  n={len(excl):>4}  WR={wr:.0f}%")

    excl = [t for t in all_trades if t["outcome"] != "TIME"]
    wins = [t for t in excl if t["outcome"] == "TP"]
    sls = [t for t in excl if t["outcome"] == "SL"]

    if not excl:
        print("No trades.")
        return

    wr = len(wins) / len(excl) * 100
    avg_net = sum(t["net"] for t in excl) / len(excl)
    pf_num = sum(t["net"] for t in wins) if wins else 0
    pf_den = abs(sum(t["net"] for t in sls)) if sls else 1
    pf = pf_num / pf_den if pf_den > 0 else 0
    avg_h = sum(t["hold_bars"] for t in excl) / len(excl) * 5

    print("\n" + "=" * 58)
    print("BB FADE v3 -- MTF 15m setup + 5m entry RESULTS")
    print("=" * 58)
    print(
        f"Total:         {len(all_trades)}  (TIME={sum(1 for t in all_trades if t['outcome'] == 'TIME')})"
    )
    print(f"Excl TIME:     {len(excl)}  TP={len(wins)}  SL={len(sls)}")
    print(f"WR:            {wr:.1f}%")
    print(f"Avg net:       {avg_net:+.3f}%")
    print(f"Profit Factor: {pf:.2f}")
    print(f"Avg hold:      {avg_h:.0f} min")

    vol_g = defaultdict(list)
    for t in excl:
        k = (
            "<1.5"
            if t["vol_ratio"] < 1.5
            else ("1.5-3" if t["vol_ratio"] < 3 else ">3")
        )
        vol_g[k].append(t)
    print_breakdown("Vol ratio at touch (15m)", vol_g)

    rsi_g = defaultdict(list)
    for t in excl:
        r = t["rsi"]
        if t["side"] == "sell":
            k = (
                ">80"
                if r > 80
                else (">70" if r > 70 else (">60" if r > 60 else "<=60"))
            )
        else:
            k = (
                "<20"
                if r < 20
                else ("<30" if r < 30 else ("<40" if r < 40 else ">=40"))
            )
        rsi_g[f"{t['side']}_{k}"].append(t)
    print_breakdown("RSI at 15m touch (by side)", rsi_g)

    bw_g = defaultdict(list)
    for t in excl:
        k = (
            "<0.5"
            if t["bw_pct"] < 0.5
            else (
                "<1.0"
                if t["bw_pct"] < 1.0
                else ("<2.0" if t["bw_pct"] < 2.0 else ">=2.0")
            )
        )
        bw_g[k].append(t)
    print_breakdown("BB width % at 15m touch", bw_g)

    hour_g = defaultdict(list)
    for t in excl:
        h = t["hour_utc"]
        k = "Asia 00-08" if h < 8 else ("EU 08-16" if h < 16 else "US 16-24")
        hour_g[k].append(t)
    print_breakdown("Session (UTC)", hour_g)

    sym_g = defaultdict(list)
    for t in excl:
        sym_g[t["sym"]].append(t)
    rows = [
        (
            s,
            len(v),
            sum(1 for x in v if x["outcome"] == "TP") / len(v) * 100,
            sum(x["net"] for x in v) / len(v),
        )
        for s, v in sym_g.items()
    ]
    rows.sort(key=lambda x: -x[2])
    print("\n=== Top pairs by WR ===")
    print(f"  {'Pair':<25}  n     WR    avg_net")
    for sym, n, wr_s, an in rows[:12]:
        print(f"  {sym:<25}  {n:<5} {wr_s:.0f}%   {an:+.3f}%")


if __name__ == "__main__":
    main()
