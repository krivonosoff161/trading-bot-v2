"""
BB+Volume 5m backtest — FADE only engine (v2).

Strategy: FADE only — close pokes BB band with low volume in ranging market.
  Entry:  close > BB_upper (short) or close < BB_lower (long)
          + vol < vol_ma × FADE_VOL_MULT
          + ADX_1H < per-pair threshold
          + BB_mid at least FADE_MIN_DIST_ATR away (R:R quality gate)
  SL:     ATR-based
  TP:     fixed ATR multiple (not BB_mid) → stable R:R = 2.0
  Max hold: 12 bars (60 min)

BREAKOUT removed — PF 1.02 over 1163 trades = no edge in this form.

Data source: scripts/backtest_candle_cache_35d.pkl
Run: python -u scripts/bt_bb_volume_5m.py
"""

import sys
import pickle
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

CACHE_FILE = Path(__file__).parent / "backtest_candle_cache_35d.pkl"
SYMBOLS    = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT"]

# BB + volume
BB_PERIOD     = 20
BB_STD        = 2.0
VOL_MA_PERIOD = 20
ATR_PERIOD    = 14

# Higher timeframe context
ADX_1H_PERIOD = 14

# FADE: per-pair config
# adx_max  — ADX_1H ceiling (market must be ranging)
# vol_mult — volume must be below vol_ma × this (low vol = no breakout)
# sl_atr   — SL distance in ATR units
# tp_atr   — TP distance in ATR units (fixed R:R, not BB_mid)
# min_dist — BB_mid must be >= this × ATR from entry (R:R quality gate)
# tp_atr=0 means TP = BB_mid (natural mean reversion target)
# min_dist = minimum distance from entry to BB_mid in ATR units (R:R gate)
# adx_max=0 disables the pair entirely
# tp_atr=0 means TP = BB_mid (natural mean reversion target)
# min_dist = minimum distance from entry to BB_mid in ATR units (R:R gate, ≥1.0 → R:R≥1.0)
# adx_max=0 disables the pair entirely
FADE_PARAMS = {
    "BTC-USDT":  {"adx_max": 20, "vol_mult": 0.70, "sl_atr": 1.0, "tp_atr": 0, "min_dist": 1.0},
    "ETH-USDT":  {"adx_max": 20, "vol_mult": 0.70, "sl_atr": 1.0, "tp_atr": 0, "min_dist": 1.0},
    "SOL-USDT":  {"adx_max": 0,  "vol_mult": 0.70, "sl_atr": 1.0, "tp_atr": 0, "min_dist": 1.0},  # disabled: PF<1
    "XRP-USDT":  {"adx_max": 0,  "vol_mult": 0.70, "sl_atr": 1.0, "tp_atr": 0, "min_dist": 1.0},  # disabled: PF<1
    "DOGE-USDT": {"adx_max": 22, "vol_mult": 0.70, "sl_atr": 1.0, "tp_atr": 0, "min_dist": 1.0},
}
FADE_MAX_HOLD = 12   # bars × 5m = 60 min

# Period split (days from most recent)
DAYS_BACK   = 12
NUM_PERIODS = 3

BARS_5M_PER_DAY = 288    # 24×60/5


# ---------------------------------------------------------------------------
# Indicator helpers (standalone, no external deps)
# ---------------------------------------------------------------------------

def _slope_at(closes: np.ndarray, i: int, period: int = 5) -> float:
    """Linear regression angle (degrees) at bar i using last `period` closes.
    Drozdov min-max normalization. Returns 0.0 if flat or not enough data.
    For BB FADE: we check slope is DECELERATING (current < previous for SHORT).
    """
    if i < period - 1:
        return 0.0
    y = closes[i - period + 1: i + 1].astype(float)
    y_range = float(y.max() - y.min())
    if y_range == 0.0:
        return 0.0
    x_sc = np.linspace(0.0, 1.0, period)
    y_sc = (y - y.min()) / y_range
    slope = float(np.polyfit(x_sc, y_sc, 1)[0])
    return float(np.degrees(np.arctan(slope)))


def _ema_series(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.zeros(len(arr))
    k = 2.0 / (period + 1)
    out[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _bias_1h_series(closes_1h: np.ndarray, ema_fast: int = 20, ema_slow: int = 50) -> np.ndarray:
    """Returns bias per 1H bar: 1=UP, -1=DOWN, 0=NEUTRAL (EMA20 vs EMA50)."""
    n   = len(closes_1h)
    k_f = 2.0 / (ema_fast + 1)
    k_s = 2.0 / (ema_slow  + 1)
    ema_f = np.zeros(n)
    ema_s = np.zeros(n)
    if n > ema_slow:
        ema_f[ema_fast - 1] = float(np.mean(closes_1h[:ema_fast]))
        ema_s[ema_slow - 1] = float(np.mean(closes_1h[:ema_slow]))
        for i in range(ema_fast, n):
            ema_f[i] = closes_1h[i] * k_f + ema_f[i - 1] * (1 - k_f)
        for i in range(ema_slow, n):
            ema_s[i] = closes_1h[i] * k_s + ema_s[i - 1] * (1 - k_s)
    bias = np.zeros(n, dtype=int)
    for i in range(ema_slow, n):
        if ema_f[i] > ema_s[i]:
            bias[i] = 1    # UP
        elif ema_f[i] < ema_s[i]:
            bias[i] = -1   # DOWN
    return bias


def _atr_series(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int = 14) -> np.ndarray:
    n = len(closes)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
    atr = np.zeros(n)
    if period < n:
        atr[period] = float(np.mean(tr[1:period + 1]))
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _adx_series(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                period: int = 14) -> np.ndarray:
    """Returns ADX series (same length as input)."""
    n = len(closes)
    plus_dm  = np.zeros(n)
    minus_dm = np.zeros(n)
    tr       = np.zeros(n)
    for i in range(1, n):
        up   = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i]  = up   if up > down and up > 0   else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))

    # Wilder smoothing
    def _smooth(arr):
        s = np.zeros(n)
        if n > period:
            s[period] = np.sum(arr[1:period + 1])
            for i in range(period + 1, n):
                s[i] = s[i - 1] - s[i - 1] / period + arr[i]
        return s

    str14    = _smooth(tr)
    plus14   = _smooth(plus_dm)
    minus14  = _smooth(minus_dm)

    with np.errstate(divide='ignore', invalid='ignore'):
        pdi  = np.where(str14 > 0, 100 * plus14 / str14,  0.0)
        mdi  = np.where(str14 > 0, 100 * minus14 / str14, 0.0)
        dx   = np.where((pdi + mdi) > 0, 100 * np.abs(pdi - mdi) / (pdi + mdi), 0.0)

    adx = np.zeros(n)
    start = period * 2
    if start < n:
        adx[start] = np.mean(dx[period:start + 1]) if start + 1 > period else 0.0
        for i in range(start + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx


# ---------------------------------------------------------------------------
# Parse cache candles → chronological arrays
# ---------------------------------------------------------------------------

def _parse(raw: list):
    """raw = list oldest-first [ts, o, h, l, c, vol_contracts, ...]
    Cache stores candles in chronological order (oldest first).
    """
    ts     = np.array([int(c[0])   for c in raw])
    opens  = np.array([float(c[1]) for c in raw])
    highs  = np.array([float(c[2]) for c in raw])
    lows   = np.array([float(c[3]) for c in raw])
    closes = np.array([float(c[4]) for c in raw])
    vols   = np.array([float(c[5]) for c in raw])
    return ts, opens, highs, lows, closes, vols


# ---------------------------------------------------------------------------
# Trade result tracking
# ---------------------------------------------------------------------------

class TradeResult:
    __slots__ = ("side", "entry", "sl", "tp", "exit_p", "outcome",
                 "bars_held", "ts_entry", "signal_type")

    def __init__(self, side, entry, sl, tp, ts_entry, signal_type):
        self.side        = side
        self.entry       = entry
        self.sl          = sl
        self.tp          = tp
        self.exit_p      = None
        self.outcome     = None    # "TP" | "SL" | "TIME"
        self.bars_held   = 0
        self.ts_entry    = ts_entry
        self.signal_type = signal_type   # "BREAKOUT" | "FADE"

    @property
    def pnl_r(self):
        if self.exit_p is None:
            return 0.0
        risk = abs(self.entry - self.sl)
        if risk == 0:
            return 0.0
        if self.side == "LONG":
            return (self.exit_p - self.entry) / risk
        return (self.entry - self.exit_p) / risk


# ---------------------------------------------------------------------------
# Simulate one symbol
# ---------------------------------------------------------------------------

def simulate_symbol(sym: str, data: dict) -> list:
    raw_5m = data["5m"]
    raw_1h = data["1h"]

    ts5, o5, h5, l5, c5, v5 = _parse(raw_5m)
    ts1, o1, h1, l1, c1, _  = _parse(raw_1h)

    n5 = len(ts5)
    n1 = len(ts1)

    fp = FADE_PARAMS.get(sym, FADE_PARAMS["BTC-USDT"])

    # Precompute 1H ADX series
    adx_1h = _adx_series(h1, l1, c1, ADX_1H_PERIOD)

    # Precompute 5m series
    atr5 = _atr_series(h5, l5, c5, ATR_PERIOD)

    # BB series: middle, upper, lower
    bb_mid = np.zeros(n5)
    bb_up  = np.zeros(n5)
    bb_lo  = np.zeros(n5)
    for i in range(BB_PERIOD - 1, n5):
        sl = c5[i - BB_PERIOD + 1: i + 1]
        m  = float(np.mean(sl))
        s  = float(np.std(sl, ddof=0))
        bb_mid[i] = m
        bb_up[i]  = m + BB_STD * s
        bb_lo[i]  = m - BB_STD * s

    # Volume MA series
    vol_ma = np.zeros(n5)
    for i in range(VOL_MA_PERIOD - 1, n5):
        vol_ma[i] = float(np.mean(v5[i - VOL_MA_PERIOD + 1: i + 1]))

    trades   = []
    in_trade = False
    active   = None

    warmup = max(BB_PERIOD, VOL_MA_PERIOD, ATR_PERIOD, ADX_1H_PERIOD * 2) + 5

    for i in range(warmup, n5 - 1):
        # --- manage open trade ---
        if in_trade:
            active.bars_held += 1
            hi = h5[i]
            lo = l5[i]

            sl_hit = (active.side == "LONG"  and lo <= active.sl) or \
                     (active.side == "SHORT" and hi >= active.sl)
            tp_hit = (active.side == "LONG"  and hi >= active.tp) or \
                     (active.side == "SHORT" and lo <= active.tp)

            if sl_hit:
                active.exit_p, active.outcome = active.sl, "SL"
                trades.append(active); in_trade = False
            elif tp_hit:
                active.exit_p, active.outcome = active.tp, "TP"
                trades.append(active); in_trade = False
            elif active.bars_held >= FADE_MAX_HOLD:
                active.exit_p, active.outcome = c5[i], "TIME"
                trades.append(active); in_trade = False
            continue

        # --- look for FADE signal ---
        cur_close  = c5[i]
        cur_vol    = v5[i]
        cur_atr    = atr5[i]
        cur_vol_ma = vol_ma[i]
        cur_bb_up  = bb_up[i]
        cur_bb_lo  = bb_lo[i]
        cur_bb_mid = bb_mid[i]

        if cur_atr == 0 or cur_vol_ma == 0 or cur_bb_up == 0:
            continue

        # 1H ADX: last closed bar before this 5m bar
        idx1 = np.searchsorted(ts1, ts5[i], side='left') - 1
        if idx1 < ADX_1H_PERIOD * 2:
            continue
        cur_adx_1h = adx_1h[idx1]

        # Base FADE conditions
        adx_ok = cur_adx_1h < fp["adx_max"]
        vol_ok = cur_vol < cur_vol_ma * fp["vol_mult"]
        # Volume must be declining (not growing) — rising vol at BB = trend continuation
        vol_declining = v5[i] < v5[i - 1]
        if not (adx_ok and vol_ok and vol_declining):
            continue

        # Slope deceleration filter (5m):
        # FADE SHORT → price pushed up through BB but 5m slope is now weaker than prev bar
        # FADE LONG  → price pushed down through BB but 5m slope is now less negative than prev bar
        slope_now  = _slope_at(c5, i,     period=5)
        slope_prev = _slope_at(c5, i - 1, period=5)

        # "Walking the band" check: signal bar is NOT a big thrust candle
        # Thrust = high-low > 1.5× ATR (price blasting through BB → trend, not fade)
        # Walk = small bars consolidating at BB edge → exhaustion, fade valid
        cur_bar_range = h5[i] - l5[i]
        not_thrust = cur_bar_range < 1.5 * cur_atr

        # FADE SHORT: close above upper band + not a thrust + slope decelerating
        if cur_close > cur_bb_up and not_thrust:
            slope_fading = slope_now < slope_prev
            dist = cur_close - cur_bb_mid
            if dist >= fp["min_dist"] * cur_atr and slope_fading:
                # SL behind the BB band, not from entry price
                sl = cur_bb_up + fp["sl_atr"] * cur_atr
                tp = cur_bb_mid if fp["tp_atr"] == 0 else cur_close - fp["tp_atr"] * cur_atr
                active   = TradeResult("SHORT", cur_close, sl, tp, ts5[i], "FADE")
                in_trade = True
                continue

        # FADE LONG: close below lower band + not a thrust + slope decelerating
        if cur_close < cur_bb_lo and not_thrust:
            slope_fading = slope_now > slope_prev
            dist = cur_bb_mid - cur_close
            if dist >= fp["min_dist"] * cur_atr and slope_fading:
                # SL behind the BB band, not from entry price
                sl = cur_bb_lo - fp["sl_atr"] * cur_atr
                tp = cur_bb_mid if fp["tp_atr"] == 0 else cur_close + fp["tp_atr"] * cur_atr
                active   = TradeResult("LONG", cur_close, sl, tp, ts5[i], "FADE")
                in_trade = True
                continue

    return trades


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _stats(trades: list) -> dict:
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "avg_r": 0.0}
    wins   = [t for t in trades if t.pnl_r > 0]
    losses = [t for t in trades if t.pnl_r <= 0]
    gross_w = sum(t.pnl_r for t in wins)
    gross_l = abs(sum(t.pnl_r for t in losses))
    wr  = len(wins) / len(trades) * 100
    pf  = gross_w / gross_l if gross_l > 0 else float("inf")
    avg = sum(t.pnl_r for t in trades) / len(trades)
    to  = sum(1 for t in trades if t.outcome == "TIME") / len(trades) * 100
    return {"n": len(trades), "wr": round(wr, 1), "pf": round(pf, 2),
            "avg_r": round(avg, 3), "time_exit_pct": round(to, 1)}


def _stats_line(label: str, trades: list) -> str:
    s = _stats(trades)
    if s["n"] == 0:
        return f"  {label:20s}  0 trades"
    return (f"  {label:20s}  n={s['n']:4d}  WR={s['wr']:5.1f}%  "
            f"PF={s['pf']:5.2f}  avg_R={s['avg_r']:+.3f}  TIME={s['time_exit_pct']:.0f}%")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(cache: dict = None):
    if cache is None:
        print("Loading cache …")
        with open(CACHE_FILE, "rb") as f:
            cache = pickle.load(f)

    cache_start_ms = cache.get("_cache_start_ms", 0)
    print(f"Cache start: {cache_start_ms}")

    all_trades: dict[str, list] = {}

    for sym in SYMBOLS:
        data = cache.get(sym, {})
        if not data.get("5m") or not data.get("1h"):
            print(f"[SKIP] {sym}: missing 5m or 1h data")
            continue
        print(f"Simulating {sym} …", flush=True)
        trades = simulate_symbol(sym, data)
        all_trades[sym] = trades

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("BB+Volume 5m — Backtest Results (35d)")
    print("=" * 70)

    combined = [t for trades in all_trades.values() for t in trades]

    # Overall
    print("\n[OVERALL — FADE only]")
    print(_stats_line("ALL", combined))

    # Per symbol
    print("\n[BY SYMBOL]")
    for sym in SYMBOLS:
        trades = all_trades.get(sym, [])
        print(_stats_line(sym, trades))

    # Period split — use timestamps from first symbol that has trades
    ref_sym = next((s for s in SYMBOLS if all_trades.get(s)), None)
    if ref_sym:
        raw_5m = cache[ref_sym]["5m"]
        ts5, *_ = _parse(raw_5m)
        latest_ts = int(ts5[-1])
        ms_per_day = 86_400_000

        print("\n[BY PERIOD]")
        for p in range(NUM_PERIODS):
            ts_end   = latest_ts - p * DAYS_BACK * ms_per_day
            ts_start = ts_end - DAYS_BACK * ms_per_day
            label    = f"P{p + 1} (last {(p + 1) * DAYS_BACK}d — {p * DAYS_BACK}d ago)"
            subset   = [t for t in combined
                        if ts_start <= t.ts_entry <= ts_end]
            print(_stats_line(label, subset))

    print("\n[PARAMS]")
    print(f"  BB({BB_PERIOD}, {BB_STD})  vol_ma({VOL_MA_PERIOD})  ATR({ATR_PERIOD})")
    print(f"  FADE per-pair (ADX_max / vol_mult / SL_atr / TP_atr / min_dist):")
    for s, fp in FADE_PARAMS.items():
        print(f"    {s}: ADX<{fp['adx_max']}  vol<{fp['vol_mult']}x  "
              f"SL={fp['sl_atr']}R  TP={fp['tp_atr']}R  min_dist={fp['min_dist']}ATR")
    print(f"  Max hold: {FADE_MAX_HOLD} bars (60 min)")
    print("=" * 70)


def optimize_adx(cache: dict, symbols: list, adx_range: list) -> None:
    """Grid search over ADX thresholds per symbol."""
    print("\n" + "=" * 70)
    print("ADX THRESHOLD OPTIMIZATION (per pair)")
    print("=" * 70)
    for sym in symbols:
        data = cache.get(sym, {})
        if not data.get("5m") or not data.get("1h"):
            continue
        print(f"\n  {sym}:")
        print(f"  {'ADX<':>6}  {'n':>4}  {'WR':>6}  {'PF':>6}  {'avg_R':>7}")
        print(f"  {'-'*42}")
        orig = FADE_PARAMS[sym]["adx_max"]
        for adx_val in adx_range:
            FADE_PARAMS[sym]["adx_max"] = adx_val
            trades = simulate_symbol(sym, data)
            s = _stats(trades)
            pf_str = f"{s['pf']:6.2f}" if s['n'] > 0 else "   —  "
            wr_str = f"{s['wr']:5.1f}%" if s['n'] > 0 else "   —  "
            ar_str = f"{s['avg_r']:+.3f}" if s['n'] > 0 else "    — "
            marker = " ◄" if s['n'] >= 5 and s['pf'] >= 2.0 else ""
            print(f"  {adx_val:>6}  {s['n']:>4}  {wr_str:>6}  {pf_str:>6}  {ar_str:>7}{marker}")
        FADE_PARAMS[sym]["adx_max"] = orig  # restore
    print("=" * 70)


if __name__ == "__main__":
    print("Loading cache …")
    with open(CACHE_FILE, "rb") as _f:
        _cache = pickle.load(_f)
    main(_cache)
    optimize_adx(_cache, ["XRP-USDT", "SOL-USDT"], adx_range=[10, 13, 15, 18, 20, 22, 25, 28])
