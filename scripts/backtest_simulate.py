"""
Full backtest simulation: two intraday engines — FAST (1-2h) and SWING (2-4h).
Runs on historical candles every 10 minutes.

New architecture (Qwen + Codex audit):
- ADX_1H rising (not static threshold)
- BB Width expansion on 15m as regime gate
- Structural SL from swing levels
- Side-aware funding filter
- Late-move veto (day_range + day_position)
- DOGE excluded (too noisy)
- No day_high/low as TP — fixed R multiples only
- ADX_4H as late veto only (not main gate)

Usage:
    python scripts/backtest_simulate.py
"""
import asyncio
import bisect
import os
import pickle
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from src.exchange.okx_client import OKXClient
from src.strategy.indicators import (
    calc_atr, calc_adx, calc_ema, parse_candles, parse_volumes,
    find_swing_levels, calc_bollinger_bands, calc_rsi, calc_slope,
    calc_supertrend,
)
# Single source of truth for pair params — same as prod analyze_chart.py
from scripts.analyze_chart import _PAIR_PARAMS as PAIR_PARAMS, _PAIR_PARAMS_DEFAULT, _mode_cfg

# ── Historical data helpers ─────────────────────────────────────────────────────

def _get_hist_funding(funding_history: list, ts_ms: int) -> float:
    """Return the funding rate active at ts_ms.
    Funding settles every 8h; rate is set at settlement and holds until next.
    Searches for the most recent settlement <= ts_ms.
    """
    best_rate = 0.0
    best_ts   = 0
    for entry in funding_history:
        ft = int(entry.get("fundingTime", 0))
        if ft <= ts_ms and ft > best_ts:
            best_ts   = ft
            best_rate = float(entry.get("fundingRate", 0))
    return best_rate


def _get_oi_delta(oi_history: list, ts_ms: int) -> float:
    """Return OI change fraction vs previous bar at ts_ms: (oi_now - oi_prev) / oi_prev.
    OKX rubik returns entries as lists [ts, oi, oiCcy] or dicts.
    Returns 0.0 if data unavailable.
    """
    if not oi_history:
        return 0.0

    def _parse(entry):
        if isinstance(entry, dict):
            return int(entry.get("ts", 0)), float(entry.get("oi", 0) or entry.get("oiCcy", 0))
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            return int(entry[0]), float(entry[1])
        return 0, 0.0

    sorted_oi = sorted((_parse(e) for e in oi_history), key=lambda x: x[0])
    prev_oi = cur_oi = None
    for t, oi in sorted_oi:
        if t <= ts_ms:
            prev_oi = cur_oi
            cur_oi  = oi
    if cur_oi and prev_oi and prev_oi > 0:
        return (cur_oi - prev_oi) / prev_oi
    return 0.0


def _candle_vol_delta(raw_5m: list) -> float:
    """Signed volume bias from last 3 closed 5m bars.
    Returns value in [-1, +1]: +1 = all volume bullish, -1 = all bearish.
    Proxy for taker buy/sell delta; avoids downloading millions of raw trades.
    """
    if not raw_5m or len(raw_5m) < 3:
        return 0.0
    total_vol  = 0.0
    signed_vol = 0.0
    for candle in raw_5m[:3]:   # newest 3 bars (newest-first order)
        o = float(candle[1])
        c = float(candle[4])
        v = float(candle[5])
        direction  = 1.0 if c >= o else -1.0
        signed_vol += direction * v
        total_vol  += v
    if total_vol == 0:
        return 0.0
    return signed_vol / total_vol


def _calc_basis_and_perp_div(mark_15m: list, index_15m: list,
                              last_price: float, raw_15m: list) -> tuple:
    """Compute basis_pct and perp_div_4bar at signal time.

    basis_pct     = (perp_last - mark_close) / mark_close * 100
                    Positive → perp trading at premium to fair value.
    perp_div_4bar = (perp 4-bar change) - (index 4-bar change)  [pct]
                    perp = raw_15m (actual futures last price)
                    index = index_15m (spot composite)
                    Positive → futures running ahead of spot (divergence risk).

    All lists newest-first. Returns (basis_pct, perp_div_4bar) or (None, None).
    """
    if not mark_15m or not index_15m or len(index_15m) < 5:
        return None, None
    if not raw_15m or len(raw_15m) < 5:
        return None, None

    mark_close = float(mark_15m[0][4])   # most recent mark price bar close
    basis_pct  = (last_price - mark_close) / mark_close * 100 if mark_close else None

    # perp_div: futures (raw_15m) change vs index change over last 4 bars (=1h)
    try:
        perp_now   = float(raw_15m[0][4])   # perp futures close now
        perp_4back = float(raw_15m[4][4])   # perp futures close 1h ago
        idx_now    = float(index_15m[0][4])
        idx_4back  = float(index_15m[4][4])
        if perp_4back > 0 and idx_4back > 0:
            perp_chg      = (perp_now - perp_4back) / perp_4back * 100
            idx_chg       = (idx_now  - idx_4back)  / idx_4back  * 100
            perp_div_4bar = perp_chg - idx_chg
        else:
            perp_div_4bar = None
    except (IndexError, ValueError, ZeroDivisionError):
        perp_div_4bar = None

    return basis_pct, perp_div_4bar


# ── Config ─────────────────────────────────────────────────────────────────────
SYMBOLS       = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT", "ADA-USDT"]
EXTRA_SYMBOLS = []
ALL_SYMBOLS   = SYMBOLS + EXTRA_SYMBOLS

DAYS_BACK    = 21
INTERVAL_M   = 15  # matches live scanner cadence (every :00/:15/:30/:45)
OUTCOME_H    = 24

# Research-only toggles. Defaults preserve baseline behavior.
BT_REGIME_SCORE = os.getenv("BT_REGIME_SCORE", "0") == "1"   # multi-factor regime upgrade
BT_REGIME_SCORE_MIN = int(os.getenv("BT_REGIME_SCORE_MIN", "3"))  # min score to upgrade RANGING→DRIFT
BT_ADX_PERIOD = int(os.getenv("BT_ADX_PERIOD", "9"))             # 9=fast reaction (default), 14=standard

ADX_PERIOD   = BT_ADX_PERIOD

# ── Hold time — configurable via env for param sweep ────────────────────────────
HOLD_FAST_M  = int(os.getenv("BT_HOLD_FAST_M",  "150"))
HOLD_SWING_M = int(os.getenv("BT_HOLD_SWING_M", "300"))

# ── Slope filter — configurable via env for param sweep ─────────────────────────
BT_SLOPE_MIN = float(os.getenv("BT_SLOPE_MIN", "35"))

# ── Run tag for named JSON output (param sweep) ──────────────────────────────────
BT_RUN_TAG = os.getenv("BT_RUN_TAG", "latest")
BT_WEAK_TREND = os.getenv("BT_WEAK_TREND", "0") == "1"
BT_WEAK_TREND_ADX_MIN = float(os.getenv("BT_WEAK_TREND_ADX_MIN", "20"))
BT_WEAK_TREND_ADX_MAX = float(os.getenv("BT_WEAK_TREND_ADX_MAX", "26"))
BT_WEAK_TREND_SLOPE_MIN = float(os.getenv("BT_WEAK_TREND_SLOPE_MIN", "40"))
BT_WEAK_TREND_SLOPE_ACCEL = float(os.getenv("BT_WEAK_TREND_SLOPE_ACCEL", "5"))

# ── Fair-value filters ───────────────────────────────────────────────────────
# Block SHORT signals when perp is running ahead of spot (overheated longs).
# None = disabled. Hypothesis: SHORT + perp_div > 0 → WR 66% vs 79%.
PERP_DIV_SHORT_VETO = 0.0   # pct; 0.0 = block any positive divergence for shorts

# ── Candle cache ────────────────────────────────────────────────────────────────
CACHE_FILE       = Path(__file__).parent / "backtest_candle_cache_65d.pkl"
CACHE_MI_FILE    = Path(__file__).parent / "backtest_mark_index_cache_65d.pkl"
CACHE_MAX_AGE    = 23 * 3600
BACKTEST_RUNS_DIR = Path(__file__).parent / "backtest_runs"

# PAIR_PARAMS and _mode_cfg imported from scripts.analyze_chart — single source of truth.

# ── Param sets ──────────────────────────────────────────────────────────────────
PARAM_SETS = [
    {
        "label":         "COMBINED | period1 (last 21d)",
        "mode":          "COMBINED",
        "night_filter":  False,
        "time_block_h":  [],
        "offset_days":   0,
    },
    {
        "label":         "COMBINED | period2 (21d-42d ago)",
        "mode":          "COMBINED",
        "night_filter":  False,
        "time_block_h":  [],
        "offset_days":   21,
    },
    {
        "label":         "COMBINED | period3 (42d-63d ago)",
        "mode":          "COMBINED",
        "night_filter":  False,
        "time_block_h":  [],
        "offset_days":   42,
    },
]

# ── Helpers ─────────────────────────────────────────────────────────────────────

class _TeeStream:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for stream in self._streams:
            stream.write(data)
        return len(data)

    def flush(self):
        for stream in self._streams:
            stream.flush()

    def isatty(self):
        primary = self._streams[0]
        return getattr(primary, "isatty", lambda: False)()


def _enable_run_log():
    BACKTEST_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = BACKTEST_RUNS_DIR / f"backtest_auto_{stamp}.txt"

    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    log_handle = open(log_path, "w", encoding="utf-8", newline="")

    sys.stdout = _TeeStream(orig_stdout, log_handle)
    sys.stderr = _TeeStream(orig_stderr, log_handle)
    print(f"[backtest] saving output -> {log_path}")
    return log_handle, orig_stdout, orig_stderr


def _bias(bull: bool, bear: bool) -> str:
    if bull: return "UP"
    if bear: return "DOWN"
    return "NEUTRAL"


def _calc_vwap_and_day(raw_15m: list, day_start_ms: int):
    day_candles = [c for c in raw_15m if int(c[0]) >= day_start_ms]
    if len(day_candles) < 4:
        return None, None, None  # too few bars — day_position unreliable
    closes = [float(c[4]) for c in day_candles]
    vols   = [float(c[5]) for c in day_candles]
    highs  = [float(c[2]) for c in day_candles]
    lows   = [float(c[3]) for c in day_candles]
    vol_sum = sum(vols)
    vwap = sum(c * v for c, v in zip(closes, vols)) / vol_sum if vol_sum > 0 else None
    return vwap, max(highs), min(lows)


# ── Market regime detector ──────────────────────────────────────────────────────
def detect_regime(adx_1h: float, adx_4h: float, adx_4h_rising: bool,
                  di_spread_4h: float, di_spread_1h: float, bb_width: float,
                  adx_1h_rising: bool = False, st_1h_dir: str = "",
                  di_spread_15m: float = 0.0) -> str:
    """
    Classify market into one of four regimes:
      TRENDING — strong directional movement, SWING + FAST with trend
      DRIFT    — persistent directional move without acceleration (new)
      WEAK_TREND — research-only transition state, traded as stricter DRIFT
      RANGING    — sideways bounded, FAST both directions, SWING off
      CHOPPY     — volatile but no direction, no trade
    """
    # TRENDING: both timeframes show direction conviction
    trend_4h = adx_4h >= 22 and di_spread_4h >= 10
    trend_1h = adx_1h >= 18 and di_spread_1h >= 8
    if trend_4h and trend_1h:
        return "TRENDING"

    weak_trend = (
        BT_WEAK_TREND
        and BT_WEAK_TREND_ADX_MIN <= adx_1h <= BT_WEAK_TREND_ADX_MAX
        and di_spread_1h >= 8
        and (adx_1h_rising or st_1h_dir in ("up", "down"))
    )
    if weak_trend:
        return "WEAK_TREND"

    # DRIFT: 1H has persistent direction without full trend confirmation
    # Catches: ranging_adx_high (ADX 20-26 not in full trend),
    #          trending_adx_not_rising, trending_vol_low dead zones
    drift = 12 <= adx_1h <= 30 and di_spread_1h >= 5
    if drift:
        return "DRIFT"

    # CHOPPY: volatile with no direction — wide BB, DI lines converged, weak 4H trend
    if bb_width >= 3.0 and di_spread_1h < 6 and adx_4h < 22:
        return "CHOPPY"

    # RANGING: default — sideways bounded movement
    return "RANGING"


# ── Signal engine ───────────────────────────────────────────────────────────────

def compute_signal(raw_4h, raw_1h, raw_15m, funding, symbol="",
                   mode="SWING", night_filter=False, oi_delta=0.0,
                   time_block_h=None, raw_5m=None, trade_delta_15m=0.0,
                   _debug=False):
    """
    mode="FAST"     — fast intraday only (1-2h hold)
    mode="SWING"    — intraday swing only (2-4h hold)
    mode="COMBINED" — FAST checked first, SWING as fallback
    """
    pp = PAIR_PARAMS.get(symbol)
    if pp is None:
        return None   # pair is OFF

    if not raw_4h or len(raw_4h) < 50: return None  # need 50 bars for calc_ema(c4h, 50)
    if not raw_1h or len(raw_1h) < 50: return None  # need 50 bars for calc_ema(c1h, 50)
    if not raw_15m or len(raw_15m) < 30: return None

    h4h, l4h, c4h = parse_candles(raw_4h)
    h1h, l1h, c1h = parse_candles(raw_1h)
    h15, l15, c15 = parse_candles(raw_15m)

    # ADX on 1H — slope + DI spread
    adx_1h,      pdi_1h, mdi_1h = calc_adx(h1h, l1h, c1h, period=ADX_PERIOD, bar_index=-1)
    adx_1h_prev, _,      _      = calc_adx(h1h, l1h, c1h, period=ADX_PERIOD, bar_index=-2)
    adx_1h_rising = float(adx_1h) > float(adx_1h_prev)
    di_spread_1h  = abs(float(pdi_1h) - float(mdi_1h))

    # ADX on 15m — faster DI spread for V-bottom recovery detection
    _, pdi_15m, mdi_15m = calc_adx(h15, l15, c15, period=ADX_PERIOD, bar_index=-1)
    di_spread_15m = abs(float(pdi_15m) - float(mdi_15m))

    # ADX on 4H — slope + DI spread for SWING quality
    adx_4h,      pdi_4h, mdi_4h = calc_adx(h4h, l4h, c4h, period=ADX_PERIOD, bar_index=-1)
    adx_4h_prev, pdi_4h_c, mdi_4h_c = calc_adx(h4h, l4h, c4h, period=ADX_PERIOD, bar_index=-2)
    adx_4h_rising    = float(adx_4h) > float(adx_4h_prev)
    di_spread_4h     = abs(float(pdi_4h) - float(mdi_4h))
    # Fix 1: use closed (confirmed) 4H bar for veto — forming bar DI is unreliable mid-candle
    di_spread_4h_closed = abs(float(pdi_4h_c) - float(mdi_4h_c))

    # EMA bias on 1H (direction source)
    ema20_1h = calc_ema(c1h, 20)
    ema50_1h = calc_ema(c1h, 50)
    bias_1h  = _bias(ema20_1h[-1] > ema50_1h[-1], ema20_1h[-1] < ema50_1h[-1])

    _pdi_1h_f = float(pdi_1h)
    _mdi_1h_f = float(mdi_1h)

    # Slope: linear regression angle (Drozdov method)
    # 1H slope → SWING confirmation; 15m slope → FAST confirmation
    slope_1h      = calc_slope(c1h,      period=5)
    slope_1h_prev = calc_slope(c1h[:-1], period=5)
    slope_15m      = calc_slope(c15,      period=5)
    slope_15m_prev = calc_slope(c15[:-1], period=5)

    # Supertrend on 1H — direction without volume dependency
    # Used as fallback in DRIFT when EMA slope is FLAT (slow/lazy moves)
    st_1h = calc_supertrend(h1h, l1h, c1h, period=14, multiplier=3.0)

    # EMA bias on 4H (veto reference)
    ema20_4h = calc_ema(c4h, 20)
    ema50_4h = calc_ema(c4h, 50)
    bias_4h  = _bias(ema20_4h[-1] > ema50_4h[-1], ema20_4h[-1] < ema50_4h[-1])

    # 4H alignment context (SWING)
    # Conflict only when 4H is actively trending against 1H (DI spread >= 12).
    # Below threshold: 4H is transitioning (EMA lagged) — allow entry.
    four_h_conflict = (bias_4h != "NEUTRAL" and bias_4h != bias_1h
                       and di_spread_4h_closed >= 12)
    adx_4h_ok       = float(adx_4h) >= 20

    # 5m trigger (FAST): direction + quality filters
    five_m_long   = True   # defaults: don't block if no 5m data
    five_m_short  = True
    cross_fresh   = True   # cross happened ≤ 2 bars ago
    body_quality  = True   # trigger candle body/range > 0.55
    vol_ratio_5m  = 1.0    # 5m volume ratio
    if raw_5m and len(raw_5m) >= 22:
        h5, l5, c5  = parse_candles(raw_5m)
        o5          = [float(c[1]) for c in reversed(list(raw_5m))]  # open prices newest-first
        ema20_5m    = calc_ema(c5, 20)
        ema_val     = float(ema20_5m[-1])
        trigger_close = float(c5[-1])
        five_m_long  = trigger_close > ema_val
        five_m_short = trigger_close < ema_val

        # Cross freshness: EMA cross must have happened ≤ 5 bars ago (25 min)
        # Scan every 10m, so cross can happen between checks — 5 bars gives enough window
        n_fresh = 5
        closes_hist = [float(c5[-(i+1)]) for i in range(n_fresh + 1)]
        emas_hist   = [float(ema20_5m[-(i+1)]) for i in range(n_fresh + 1)]
        above_hist  = [c > e for c, e in zip(closes_hist, emas_hist)]
        if five_m_long:
            # Current is above EMA — look back for a bar that was below
            cross_fresh = any(not above_hist[i] for i in range(1, n_fresh + 1))
        else:
            # Current is below EMA — look back for a bar that was above
            cross_fresh = any(above_hist[i] for i in range(1, n_fresh + 1))

        # Trigger candle body quality: body > 55% of full range
        hi  = float(h5[-1])
        lo  = float(l5[-1])
        op  = o5[-1]  # newest open
        rng = hi - lo
        body_quality = (abs(trigger_close - op) / rng > 0.55) if rng > 0 else False

        # vol_ratio_5m: last 3 bars vs prev 15 bars
        vols_5m      = [float(c[5]) for c in reversed(list(raw_5m))]
        rec5         = np.mean(vols_5m[:3])   if len(vols_5m) >= 3  else 0
        pri5         = np.mean(vols_5m[5:20]) if len(vols_5m) >= 20 else rec5
        vol_ratio_5m = rec5 / pri5 if pri5 > 0 else 1.0

    # ATR on 15m and 1H
    atr_15m = float(calc_atr(h15, l15, c15, period=ADX_PERIOD))
    atr_1h  = float(calc_atr(h1h, l1h, c1h, period=ADX_PERIOD))
    close   = float(c15[-1])

    # RSI on 1H — used for BOUNCE detection
    rsi_1h = float(calc_rsi(c1h, period=14))

    # Vol ratio on 15m: last 3 closed bars vs prior 15 — raw_15m is newest-first
    vols_15m   = [float(c[5]) for c in raw_15m]          # newest-first, no reversal
    recent_vol = np.mean(vols_15m[1:4])  if len(vols_15m) >= 4  else 0   # skip forming [0]
    prior_vol  = np.mean(vols_15m[5:20]) if len(vols_15m) >= 20 else recent_vol
    vol_ratio  = recent_vol / max(prior_vol, 1e-9)

    # BB Width on 15m — expansion regime filter
    bb          = calc_bollinger_bands(c15, period=20, std_mult=2.0)
    bb_width    = bb["width_pct"]
    bb_pct_b    = bb["pct_b"]        # Fix 3: 0=at lower band, 50=middle, 100=at upper band
    bb_expanding = bb_width > 1.5   # < 1.5% of price = sideways, skip

    # VWAP and day levels
    ts_last      = int(raw_15m[0][0])
    dt_last      = datetime.fromtimestamp(ts_last / 1000, tz=timezone.utc)
    day_start_ms = int(datetime(dt_last.year, dt_last.month, dt_last.day,
                                tzinfo=timezone.utc).timestamp() * 1000)
    vwap, day_high, day_low = _calc_vwap_and_day(raw_15m, day_start_ms)

    day_position = None
    if day_high and day_low and day_high != day_low:
        day_position = (close - day_low) / (day_high - day_low)

    daily_range_pct = 0.0
    if day_high and day_low and day_low > 0:
        daily_range_pct = (day_high - day_low) / day_low * 100

    signal_hour = dt_last.hour
    is_night    = 1 <= signal_hour < 7

    # Late-move veto: symmetric — block long at top AND short at bottom
    late_move = False  # evaluated per-side after signal is known

    # debug-only fields (populated later in respective blocks)
    ema_drift_dir    = ""
    drift_adx1h_veto = False

    # ── Regime detection ──────────────────────────────────────────────────────
    regime = detect_regime(float(adx_1h), float(adx_4h), adx_4h_rising,
                           di_spread_4h, di_spread_1h, bb_width,
                           adx_1h_rising=adx_1h_rising,
                           st_1h_dir=st_1h["direction"],
                           di_spread_15m=di_spread_15m)
    if regime == "TRENDING" and four_h_conflict:
        regime = "RANGING"

    # ── Regime score upgrade (research toggle BT_REGIME_SCORE) ───────────────
    # Catches early trends where ADX_4H lags but faster indicators already confirm.
    # Upgrades RANGING → DRIFT when ≥ BT_REGIME_SCORE_MIN factors agree.
    if BT_REGIME_SCORE and regime == "RANGING":
        rs = 0
        if di_spread_1h >= 8:              rs += 1  # DI diverging (reacts before ADX)
        if abs(slope_1h) >= 25:            rs += 1  # price slope strong
        if bias_1h in ("UP", "DOWN"):      rs += 1  # EMA 20/50 aligned
        if vol_ratio >= 0.8:               rs += 1  # volume present
        if adx_1h >= 20:                   rs += 1  # ADX not flat (even if lagging)
        if rs >= BT_REGIME_SCORE_MIN:
            regime = "DRIFT"

    # ── Mode + Direction by regime ────────────────────────────────────────────
    trade_style = "NO_TRADE"
    side        = None
    entry_cfg   = {}     # resolved regime+style params, used for sl_k
    _drop       = None   # first filter that killed this tick

    if regime == "CHOPPY":
        _drop = "choppy"

    elif regime == "TRENDING":
        def _bb_ok(cfg: dict) -> bool:
            if bb_width < cfg.get("bb_width_min", 0.0):
                return False
            if cfg.get("require_bb_expanding", False) and not bb_expanding:
                return False
            return True

        cfg_sw = _mode_cfg(pp, "trending", "swing")
        swing_base = (
            float(adx_1h) >= cfg_sw.get("adx", 18) and adx_1h_rising
            and vol_ratio >= cfg_sw["vol"] and _bb_ok(cfg_sw)
            and not four_h_conflict and adx_4h_ok
            and di_spread_4h >= 8 and di_spread_1h >= 8
        )
        if mode in ("SWING", "COMBINED"):
            if swing_base and bias_1h == "UP" and five_m_long:
                trade_style, side, entry_cfg = "SWING", "buy", cfg_sw
            elif swing_base and bias_1h == "DOWN" and five_m_short:
                trade_style, side, entry_cfg = "SWING", "sell", cfg_sw

        cfg_f = _mode_cfg(pp, "trending", "fast")
        fast_base = (
            float(adx_1h) >= cfg_f.get("adx", 18) and adx_1h_rising
            and vol_ratio >= cfg_f["vol"] and _bb_ok(cfg_f)
        )
        if mode in ("FAST", "COMBINED") and trade_style == "NO_TRADE":
            if fast_base and five_m_long and bias_1h == "UP":
                trade_style, side, entry_cfg = "FAST", "buy", cfg_f
            elif fast_base and five_m_short and bias_1h == "DOWN":
                trade_style, side, entry_cfg = "FAST", "sell", cfg_f

        if trade_style == "NO_TRADE":
            if not (float(adx_1h) >= cfg_f.get("adx", 18)):
                _drop = "trending_adx_low"
            elif not adx_1h_rising:
                _drop = "trending_adx_not_rising"
            elif vol_ratio < cfg_f["vol"] and vol_ratio < cfg_sw["vol"]:
                _drop = "trending_vol_low"
            elif bb_width < cfg_f.get("bb_width_min", 0.0):
                _drop = "trending_bb_narrow"
            elif four_h_conflict or not adx_4h_ok:
                _drop = "trending_4h_block"
            elif di_spread_4h < 8 or di_spread_1h < 8:
                _drop = "trending_di_spread"
            elif bias_1h == "NEUTRAL":
                _drop = "trending_neutral_bias"
            else:
                _drop = "trending_no_5m"

    elif regime == "RANGING":
        cfg_r = _mode_cfg(pp, "ranging", "fast")
        _adx_ceil    = cfg_r.get("adx_max", 22)
        _bb_corridor = cfg_r.get("bb_width_min", 0.8) <= bb_width <= cfg_r.get("bb_width_max", 2.5)
        ranging_base = (
            float(adx_1h) <= _adx_ceil and not adx_1h_rising
            and vol_ratio >= cfg_r["vol"]
            and _bb_corridor and day_position is not None
        )
        # V6C: V-bottom recovery — ADX slightly above ceiling, 15m DI already confirms direction
        ranging_recovery = (
            _adx_ceil < float(adx_1h) <= 28 and not adx_1h_rising
            and di_spread_15m >= 12
            and vol_ratio >= cfg_r["vol"]
            and day_position is not None
        )
        _buy_pos_ok  = day_position is not None and day_position <= cfg_r.get("buy_pos_max",  0.35)
        _sell_pos_ok = day_position is not None and day_position >= cfg_r.get("sell_pos_min", 0.65)
        if mode in ("FAST", "COMBINED"):
            if (ranging_base or ranging_recovery) and five_m_long and _buy_pos_ok:
                trade_style, side, entry_cfg = "FAST", "buy", cfg_r
            elif (ranging_base or ranging_recovery) and five_m_short and _sell_pos_ok:
                trade_style, side, entry_cfg = "FAST", "sell", cfg_r

        if trade_style == "NO_TRADE":
            if float(adx_1h) > _adx_ceil:
                _drop = "ranging_adx_high"
            elif adx_1h_rising:
                _drop = "ranging_adx_rising"
            elif not _bb_corridor:
                _drop = "ranging_bb_outside"
            elif vol_ratio < cfg_r["vol"]:
                _drop = "ranging_vol_low"
            elif day_position is None:
                _drop = "ranging_day_pos_none"
            else:
                _drop = "ranging_day_pos_wrong"

    elif regime in ("DRIFT", "WEAK_TREND"):
        cfg_d = _mode_cfg(pp, "drift", "fast")
        ema_slope_up   = (len(ema20_1h) >= 4
                          and ema20_1h[-1] > ema20_1h[-2]
                          and ema20_1h[-2] > ema20_1h[-3]
                          and ema20_1h[-3] > ema20_1h[-4])
        ema_slope_down = (len(ema20_1h) >= 4
                          and ema20_1h[-1] < ema20_1h[-2]
                          and ema20_1h[-2] < ema20_1h[-3]
                          and ema20_1h[-3] < ema20_1h[-4])
        # Supertrend fallback: when EMA slope is FLAT (4-bar requirement too strict
        # for slow/lazy directional moves), use Supertrend direction instead.
        _st_dir = st_1h["direction"]   # "up" or "down"
        if ema_slope_up:
            ema_drift_dir = "UP"
        elif ema_slope_down:
            ema_drift_dir = "DOWN"
        elif _st_dir == "up":
            ema_drift_dir = "UP"
        elif _st_dir == "down":
            ema_drift_dir = "DOWN"
        else:
            ema_drift_dir = "FLAT"

        vwap_side_ok = False
        if vwap:
            if ema_drift_dir == "UP"   and close > vwap: vwap_side_ok = True
            if ema_drift_dir == "DOWN" and close < vwap: vwap_side_ok = True

        drift_vol_ok  = vol_ratio >= cfg_d["vol"]
        delta_opposes = (
            (ema_drift_dir == "UP"   and trade_delta_15m < -0.3)
            or (ema_drift_dir == "DOWN" and trade_delta_15m >  0.3)
        )
        # Per-pair: require DRIFT direction to align with structural bias_1h
        drift_bias_ok = True
        if pp.get("drift_bias_check", False):
            drift_bias_ok = not (ema_drift_dir == "UP"   and bias_1h == "DOWN") \
                        and not (ema_drift_dir == "DOWN" and bias_1h == "UP")

        drift_base = (ema_drift_dir != "FLAT" and vwap_side_ok and drift_vol_ok
                      and not delta_opposes and drift_bias_ok)

        if mode in ("FAST", "COMBINED"):
            if drift_base and five_m_long  and ema_drift_dir == "UP":
                trade_style, side, entry_cfg = "FAST", "buy", cfg_d
            elif drift_base and five_m_short and ema_drift_dir == "DOWN":
                trade_style, side, entry_cfg = "FAST", "sell", cfg_d

        if trade_style == "NO_TRADE":
            if ema_drift_dir == "FLAT":
                _drop = "drift_ema_flat"
            elif not vwap_side_ok:
                _drop = "drift_vwap_wrong"
            elif not drift_vol_ok:
                _drop = "drift_vol_low"
            elif delta_opposes:
                _drop = "drift_delta_wrong"
            else:
                _drop = "drift_no_5m"

    strong_4h_veto = (
        trade_style == "FAST"
        and side is not None
        and bias_4h != "NEUTRAL"
        and di_spread_4h_closed >= 8   # Fix 1: use confirmed closed bar, not forming
        and ((side == "buy" and bias_4h == "DOWN")
             or (side == "sell" and bias_4h == "UP"))
    )
    if strong_4h_veto:
        trade_style, side = "NO_TRADE", None
        _drop = _drop or "strong_4h_veto"

    # Per-pair mode restriction
    allowed = pp.get("allowed_modes", ["FAST", "SWING"])
    if trade_style not in allowed:
        trade_style, side = "NO_TRADE", None
        _drop = _drop or "allowed_modes"

    # Night filter — block all signals 01-07 UTC
    if night_filter and is_night:
        trade_style, side = "NO_TRADE", None
        _drop = _drop or "night"

    # Time block (e.g. 10:00 UTC = 0% WR)
    if time_block_h and signal_hour in time_block_h:
        trade_style, side = "NO_TRADE", None
        _drop = _drop or "time_block"

    # Late-move veto — symmetric
    if daily_range_pct > pp["late_range"] and day_position is not None:
        if side == "buy"  and day_position > 0.90:
            trade_style, side = "NO_TRADE", None
            _drop = _drop or "late_move"
        if side == "sell" and day_position < 0.10:
            trade_style, side = "NO_TRADE", None
            _drop = _drop or "late_move"

    # 4H veto — informational only; SWING handles 4H in condition, FAST is 4H-agnostic
    fourch_veto = four_h_conflict or strong_4h_veto

    # ── VWAP filter — regime-aware ────────────────────────────────────────────
    # Matches analyze_chart.py: TRENDING skips VWAP (ADX+DI already confirm direction).
    # DRIFT: trend-follow. RANGING: fade.
    vwap_ok = True
    if vwap and close and side:
        if regime == "RANGING":
            if side == "buy"  and close > vwap: vwap_ok = False
            if side == "sell" and close < vwap: vwap_ok = False
        elif regime in ("DRIFT", "WEAK_TREND"):
            if side == "buy"  and close < vwap: vwap_ok = False
            if side == "sell" and close > vwap: vwap_ok = False

    # ── DRIFT 1H ADX lower-bound filter ──────────────────────────────────────
    # Hypothesis A: ADX_1H 12-14 = barely drifting → mostly TIME_EXIT.
    # Post-classification veto — regime label stays DRIFT, ENTRY blocked.
    _DRIFT_ADX1H_MIN = 15.0
    drift_adx1h_veto = regime in ("DRIFT", "WEAK_TREND") and adx_1h < _DRIFT_ADX1H_MIN
    if drift_adx1h_veto:
        trade_style, side = "NO_TRADE", None
        _drop = _drop or "drift_adx1h_low"

    # ── DRIFT SHORT veto (Stage 1) ───────────────────────────────────────────
    # Live 11d: DRIFT SELL n=17 WR 29% PF 0.18 avg -0.554R → biggest bleeder.
    # DRIFT = no clear trend → shorts eat mean-reversion bounces.
    # TRENDING SHORT and FADE SHORT keep their edge.
    drift_short_veto = regime in ("DRIFT", "WEAK_TREND") and side == "sell"
    if drift_short_veto:
        trade_style, side = "NO_TRADE", None
        _drop = _drop or "drift_short_veto"

    # ── Slope veto (Drozdov): price angle must confirm direction ─────────────
    # FAST uses 15m slope (matches trade duration), SWING uses 1H slope.
    # Condition: slope accelerating (current > prev) in trade direction.
    # Applied only to TRENDING/DRIFT — RANGING is mean-reversion, slope n/a.
    _SLOPE_MIN = BT_SLOPE_MIN
    if trade_style != "NO_TRADE" and side and regime in ("TRENDING", "DRIFT", "WEAK_TREND"):
        sl_cur  = slope_15m      if trade_style == "FAST" else slope_1h
        sl_prev = slope_15m_prev if trade_style == "FAST" else slope_1h_prev
        slope_min = BT_WEAK_TREND_SLOPE_MIN if regime == "WEAK_TREND" else _SLOPE_MIN
        slope_accel = BT_WEAK_TREND_SLOPE_ACCEL if regime == "WEAK_TREND" else 0.0
        slope_ok = False
        if side == "buy":
            slope_ok = sl_cur >= slope_min and sl_cur > sl_prev + slope_accel
        elif side == "sell":
            slope_ok = sl_cur <= -slope_min and sl_cur < sl_prev - slope_accel
        if not slope_ok:
            trade_style, side = "NO_TRADE", None
            _drop = _drop or "slope_weak"

    # ── Side-aware funding filter ─────────────────────────────────────────────
    funding_val   = funding if funding is not None else 0.0
    FUND_THRESH   = 0.0005   # 0.05%
    funding_block = False
    if side == "buy"  and funding_val >  FUND_THRESH: funding_block = True
    if side == "sell" and funding_val < -FUND_THRESH: funding_block = True

    # ── OI delta filter ───────────────────────────────────────────────────────
    # OI dropping >3% = positions being closed, not new money = weaker signal
    oi_weak = oi_delta < -0.03

    # ── SL / TP (matches prod analyze_chart.py formulas) ─────────────────────
    sl_p = tp_p = tp2_p = None
    sl_dist = 0.0

    if trade_style == "FAST" and side and close:
        atr_sl_dist = max(entry_cfg.get("sl_k", 1.4) * 1.2 * atr_15m, close * 0.004)
        swings = find_swing_levels(h15, l15, lookback=3, count=4)
        if side == "buy":
            atr_sl    = close - atr_sl_dist
            struct_sl = (swings["recent_lows"][-1] - 0.2 * atr_15m
                         if swings["recent_lows"] else None)
            sl_p    = round(min(struct_sl, atr_sl) if struct_sl and struct_sl < close else atr_sl, 6)
            sl_dist = close - sl_p
            _tp1_k  = 0.4 if regime in ("DRIFT", "WEAK_TREND") else 0.8
            tp_p    = round(close + sl_dist * _tp1_k, 6)   # TP1
            tp2_p   = round(close + sl_dist * 1.5, 6)      # TP2
        else:
            atr_sl    = close + atr_sl_dist
            struct_sl = (swings["recent_highs"][-1] + 0.2 * atr_15m
                         if swings["recent_highs"] else None)
            sl_p    = round(max(struct_sl, atr_sl) if struct_sl and struct_sl > close else atr_sl, 6)
            sl_dist = sl_p - close
            _tp1_k  = 0.4 if regime in ("DRIFT", "WEAK_TREND") else 0.8
            tp_p    = round(close - sl_dist * _tp1_k, 6)   # TP1
            tp2_p   = round(close - sl_dist * 1.5, 6)      # TP2

    elif trade_style == "SWING" and side and close:
        swings = find_swing_levels(h15, l15, lookback=3, count=4)
        if side == "buy":
            atr_sl    = close - entry_cfg.get("sl_k", 1.8) * 1.2 * atr_15m
            struct_sl = (swings["recent_lows"][-1] - 0.3 * atr_15m
                         if swings["recent_lows"] else None)
            sl_p    = round(min(struct_sl, atr_sl) if struct_sl else atr_sl, 6)
            sl_dist = close - sl_p
            tp_p    = round(close + min(sl_dist * 1.0, atr_1h * 0.5), 6)    # TP1
            tp2_p   = round(close + min(sl_dist * 2.5, atr_1h * 1.2), 6)    # TP2
        else:
            atr_sl    = close + entry_cfg.get("sl_k", 1.8) * 1.2 * atr_15m
            struct_sl = (swings["recent_highs"][-1] + 0.3 * atr_15m
                         if swings["recent_highs"] else None)
            sl_p    = round(max(struct_sl, atr_sl) if struct_sl else atr_sl, 6)
            sl_dist = sl_p - close
            tp_p    = round(close - min(sl_dist * 1.0, atr_1h * 0.5), 6)    # TP1
            tp2_p   = round(close - min(sl_dist * 2.5, atr_1h * 1.2), 6)    # TP2

    # R/R validation — block if TP2 < 0.8R (matches prod analyze_chart.py)
    rr_ok = True
    if sl_p and tp2_p and sl_dist > 0:
        if abs(tp2_p - close) / sl_dist < 0.8:
            rr_ok = False

    # ── Drop reason for post-regime filters ──────────────────────────────────
    if not vwap_ok:        _drop = _drop or "vwap"
    if funding_block:      _drop = _drop or "funding"
    if oi_weak:            _drop = _drop or "oi_weak"
    if not rr_ok:          _drop = _drop or "rr_low"
    if not sl_p or not tp_p:
        _drop = _drop or "no_sl_tp"

    # ── Final signal ──────────────────────────────────────────────────────────
    blocked = (trade_style == "NO_TRADE" or not vwap_ok
               or funding_block or oi_weak or not rr_ok
               or not sl_p or not tp_p)

    entry_signal = "NO_TRADE" if blocked else "ENTRY"

    out = {
        "entry_signal":    entry_signal,
        "drop_reason":     _drop if entry_signal == "NO_TRADE" else None,
        "trade_style":     trade_style,
        "regime":          regime,
        "side":            side,
        "close":           close,
        "sl":              sl_p,
        "tp":              tp_p,
        "adx_1h":          round(float(adx_1h), 1),
        "adx_1h_rising":   adx_1h_rising,
        "adx_4h":          round(float(adx_4h), 1),
        "bias_1h":         bias_1h,
        "vol_ratio":       round(vol_ratio, 2),
        "bb_width":        round(bb_width, 2),
        "funding":         funding_val,
        "oi_delta":        round(oi_delta, 4),
        "day_position":    round(day_position, 3) if day_position else None,
        "fourch_veto":     fourch_veto,
        "strong_4h_veto":  strong_4h_veto,
        "late_move":       late_move,
        "oi_weak":         oi_weak,
        "rsi_1h":          round(rsi_1h, 1),
        "slope_1h":        round(slope_1h, 1),
        "slope_1h_prev":   round(slope_1h_prev, 1),
        "slope_15m":       round(slope_15m, 1),
        "slope_15m_prev":  round(slope_15m_prev, 1),
        "is_night":        is_night,
    }
    if _debug:
        out.update({
            "di_spread_1h":     round(di_spread_1h, 1),
            "di_spread_4h":     round(di_spread_4h, 1),
            "bias_4h":          bias_4h,
            "vwap_ok":          vwap_ok,
            "vwap":             round(vwap, 4) if vwap else None,
            "ema_drift_dir":    ema_drift_dir,
            "signal_hour":      signal_hour,
            "bb_pct_b":         round(bb_pct_b, 1),
            "drift_adx1h_veto": drift_adx1h_veto,
        })
    return out


NOTIONAL_RATIO = 1.5   # notional = balance × 1.5 (e.g. $1500 at $1000 balance, 10x leverage)
LEVERAGE       = 10    # display only — changes margin math context
FAST_ATR_CAP  = 0.7    # FAST TP capped at 0.7 × ATR_1H  (≈ realistic 2h move)
SWING_ATR_CAP = 1.5    # SWING TP capped at 1.5 × ATR_1H (≈ realistic 4h move)


def calc_pnl(outcome, side, entry, sl, tp, exit_price, balance):
    """Calculate P&L in dollars for one trade outcome.
    Notional-based sizing: position = balance × NOTIONAL_RATIO.
    P&L = price_move_pct × notional (clipped at SL/TP bounds).
    """
    if entry <= 0:
        return 0.0
    notional  = balance * NOTIONAL_RATIO
    direction = 1 if side == "buy" else -1
    sl_dist   = abs(entry - sl)
    sl_pct    = sl_dist / entry if entry > 0 else 0

    if outcome == "STOP":
        return -sl_pct * notional
    if outcome == "TP":
        tp_dist = abs(tp - entry)
        return (tp_dist / entry) * notional
    if outcome == "TIME_EXIT":
        if exit_price is None:
            return 0.0
        price_move = direction * (exit_price - entry)
        tp_dist    = abs(tp - entry)
        price_move = max(-sl_dist, min(tp_dist, price_move))
        return (price_move / entry) * notional
    return 0.0


def check_outcome(raw_forward, side, sl, tp, signal_ts_ms, max_hold_ms, entry_price=None):
    """Returns (outcome, elapsed_mins, exit_price, mfe_r).
    mfe_r = max favorable excursion in R units within hold window.
    """
    if not raw_forward:
        return "NO_DATA", None, None, 0.0
    sl_dist  = abs((entry_price or 0) - sl) if entry_price else 0
    last_close = None
    mfe = 0.0   # max favorable excursion in price units
    for c in reversed(raw_forward):
        ts_c = int(c[0])
        if ts_c <= signal_ts_ms:
            continue
        elapsed_ms = ts_c - signal_ts_ms
        high = float(c[2])
        low  = float(c[3])
        # track MFE
        if entry_price and sl_dist > 0:
            favorable = (high - entry_price) if side == "buy" else (entry_price - low)
            mfe = max(mfe, favorable)
        if elapsed_ms > max_hold_ms:
            mfe_r = round(mfe / sl_dist, 2) if sl_dist > 0 else 0.0
            return "TIME_EXIT", elapsed_ms // 60000, last_close, mfe_r
        last_close = float(c[4])
        mins = elapsed_ms // 60000
        if side == "buy":
            if low  <= sl:  return "STOP", mins, sl,   round(mfe / sl_dist, 2) if sl_dist > 0 else 0.0
            if high >= tp:  return "TP",   mins, tp,   round(mfe / sl_dist, 2) if sl_dist > 0 else 0.0
        else:
            if high >= sl:  return "STOP", mins, sl,   round(mfe / sl_dist, 2) if sl_dist > 0 else 0.0
            if low  <= tp:  return "TP",   mins, tp,   round(mfe / sl_dist, 2) if sl_dist > 0 else 0.0
    return "OPEN", None, None, 0.0


# ── Main ────────────────────────────────────────────────────────────────────────

async def run():
    client = OKXClient(
        api_key=os.getenv("OKX_API_KEY", ""),
        secret_key=os.getenv("OKX_SECRET_KEY", ""),
        passphrase=os.getenv("OKX_PASSPHRASE", ""),
        is_demo=False,
    )

    now_ms  = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    step_ms = INTERVAL_M * 60 * 1000
    hold_ms = {"FAST": HOLD_FAST_M * 60 * 1000, "SWING": HOLD_SWING_M * 60 * 1000}

    # ── Report metadata header ─────────────────────────────────────────────────
    try:
        import subprocess
        _git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
        _git_dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], stderr=subprocess.DEVNULL
        ).decode().strip()
        _git_label = f"{_git_hash}{' (dirty)' if _git_dirty else ''}"
    except Exception:
        _git_label = "unknown"
    _run_ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print("=" * 62)
    print("  BACKTEST CONFIG")
    print("=" * 62)
    print(f"  Run:       {_run_ts}")
    print(f"  Git HEAD:  {_git_label}")
    print(f"  Symbols:   {', '.join(ALL_SYMBOLS)}")
    print(f"  Cadence:   {INTERVAL_M}m  (live: 15m)")
    print(f"  Hold:      FAST={HOLD_FAST_M}m  SWING={HOLD_SWING_M}m")
    print(f"  TimeBlock: off")
    print(f"  Position:  one per pair (any side)")
    print(f"  Fees:      NOT modeled  |  Slippage: NOT modeled")
    print(f"  Entry:     market at next scan (no fill delay)")
    print("=" * 62)
    print()

    # Cache must cover all periods: DAYS_BACK + max offset + warmup for EMA-50 on 4H
    INDICATOR_WARMUP_DAYS = 10   # EMA-50 on 4H needs 50 bars = 8.3 days
    max_offset_days  = max(p.get("offset_days", 0) for p in PARAM_SETS)
    total_days       = DAYS_BACK + max_offset_days + INDICATOR_WARMUP_DAYS
    cache_start_ms   = now_ms - total_days * 24 * 3600 * 1000
    all_timestamps   = list(range(cache_start_ms,
                                  now_ms - OUTCOME_H * 3600 * 1000, step_ms))

    _api_sem = asyncio.Semaphore(1)

    async def _fetch_full_history(symbol: str, bar: str, since_ms: int) -> list:
        """Fetch all candles since since_ms via pagination. Returns sorted oldest-first."""
        all_candles: list = []
        after_ms = None
        while True:
            async with _api_sem:
                batch = await client.get_history_candles(
                    symbol, bar, after=after_ms, limit=100)
            await asyncio.sleep(0.2)
            if not batch:
                break
            all_candles.extend(batch)
            oldest_ts = int(batch[-1][0])   # batch is newest-first → last = oldest
            if oldest_ts <= since_ms:
                break
            after_ms = oldest_ts            # next page: older than current oldest
        # keep only candles in range, sort oldest→newest
        all_candles = [c for c in all_candles if int(c[0]) >= since_ms]
        all_candles.sort(key=lambda c: int(c[0]))
        return all_candles

    async def _fetch_symbol(symbol: str) -> tuple:
        async with _api_sem:
            funding = await client.get_funding_rate(symbol)
        await asyncio.sleep(0.2)
        async with _api_sem:
            funding_hist = await client.get_funding_rate_history(symbol, limit=100)
        await asyncio.sleep(0.2)
        async with _api_sem:
            oi_hist = await client.get_oi_history(symbol, period="1H", limit=720)
        await asyncio.sleep(0.2)
        # One full-history fetch per timeframe instead of per-timestamp
        h4  = await _fetch_full_history(symbol, "4H",  cache_start_ms)
        h1  = await _fetch_full_history(symbol, "1H",  cache_start_ms)
        h15 = await _fetch_full_history(symbol, "15m", cache_start_ms)
        h5  = await _fetch_full_history(symbol, "5m",  cache_start_ms)
        print(f"  {symbol}: 4H={len(h4)}, 1H={len(h1)}, 15m={len(h15)}, 5m={len(h5)}, "
              f"funding={len(funding_hist)}, OI={len(oi_hist)})")
        return symbol, funding, funding_hist, oi_hist, h4, h1, h15, h5

    # ── Load or fetch candles ──────────────────────────────────────────────────
    candle_cache: dict = {}
    try:
        _tmp = pickle.load(open(CACHE_FILE, "rb")) if CACHE_FILE.exists() else {}
        cache_valid = (
            CACHE_FILE.exists()
            and (time.time() - CACHE_FILE.stat().st_mtime) < CACHE_MAX_AGE
            and all(s in _tmp for s in ALL_SYMBOLS)
            and "4h" in _tmp.get("BTC-USDT", {})    # new cache structure check
            and "5m" in _tmp.get("BTC-USDT", {})    # 5m trigger requires 5m data
            and _tmp.get("_cache_start_ms", cache_start_ms) <= cache_start_ms  # covers full range
        )
    except Exception:
        cache_valid = False

    if cache_valid:
        print(f"Загрузка свечей из кэша ({CACHE_FILE.name})...")
        with open(CACHE_FILE, "rb") as f:
            candle_cache = pickle.load(f)
        print(f"  Кэш загружен: {len(candle_cache)} пар")
    else:
        print(f"Загрузка свечей для {len(ALL_SYMBOLS)} пар (параллельно)...")
        results_fetch = await asyncio.gather(*[_fetch_symbol(s) for s in ALL_SYMBOLS])
        for symbol, funding, funding_hist, oi_hist, h4, h1, h15, h5 in results_fetch:
            candle_cache[symbol] = {
                "funding":         funding,
                "funding_history": funding_hist,
                "oi_history":      oi_hist,
                "4h":              h4,
                "1h":              h1,
                "15m":             h15,
                "5m":              h5,
            }
        candle_cache["_cache_start_ms"] = cache_start_ms
        with open(CACHE_FILE, "wb") as f:
            pickle.dump(candle_cache, f)
        print(f"  Кэш сохранён → {CACHE_FILE.name}")

    for sym in ALL_SYMBOLS:
        h1  = candle_cache[sym]["1h"]
        h15 = candle_cache[sym]["15m"]
        status = "OFF (per config)" if PAIR_PARAMS.get(sym) is None else "active"
        print(f"  {sym}: 1H={len(h1)} баров, 15m={len(h15)} баров  [{status}]")
    print()

    # ── Load or fetch mark/index candles ──────────────────────────────────────
    mi_cache: dict = {}

    async def _fetch_mi_history(symbol: str, bar: str, endpoint_fn, since_ms: int) -> list:
        """Fetch full history from a mark/index candle endpoint. Returns sorted oldest-first."""
        all_candles: list = []
        after_ms = None
        while True:
            async with _api_sem:
                batch = await endpoint_fn(symbol, bar=bar, after=after_ms, limit=100)
            await asyncio.sleep(0.2)
            if not batch:
                break
            all_candles.extend(batch)
            oldest_ts = int(batch[-1][0])
            if oldest_ts <= since_ms:
                break
            after_ms = oldest_ts
        all_candles = [c for c in all_candles if int(c[0]) >= since_ms]
        all_candles.sort(key=lambda c: int(c[0]))
        return all_candles

    async def _fetch_mi_symbol(symbol: str) -> tuple:
        mark15 = await _fetch_mi_history(
            symbol, "15m", client.get_history_mark_price_candles, cache_start_ms)
        idx15  = await _fetch_mi_history(
            symbol, "15m", client.get_history_index_candles, cache_start_ms)
        print(f"  {symbol}: mark_15m={len(mark15)}, index_15m={len(idx15)}")
        return symbol, mark15, idx15

    try:
        _mi_tmp = pickle.load(open(CACHE_MI_FILE, "rb")) if CACHE_MI_FILE.exists() else {}
        mi_valid = (
            CACHE_MI_FILE.exists()
            and (time.time() - CACHE_MI_FILE.stat().st_mtime) < CACHE_MAX_AGE
            and all(s in _mi_tmp for s in ALL_SYMBOLS)
            and "mark_15m" in _mi_tmp.get("BTC-USDT", {})
            and _mi_tmp.get("_cache_start_ms", cache_start_ms) <= cache_start_ms
        )
    except Exception:
        mi_valid = False

    if mi_valid:
        print(f"Загрузка mark/index из кэша ({CACHE_MI_FILE.name})...")
        with open(CACHE_MI_FILE, "rb") as f:
            mi_cache = pickle.load(f)
        print(f"  MI-кэш загружен: {sum(1 for k in mi_cache if k != '_cache_start_ms')} пар")
    else:
        print(f"Загрузка mark/index свечей для {len(ALL_SYMBOLS)} пар...")
        mi_results = await asyncio.gather(*[_fetch_mi_symbol(s) for s in ALL_SYMBOLS])
        for symbol, mark15, idx15 in mi_results:
            mi_cache[symbol] = {"mark_15m": mark15, "index_15m": idx15}
        mi_cache["_cache_start_ms"] = cache_start_ms
        with open(CACHE_MI_FILE, "wb") as f:
            pickle.dump(mi_cache, f)
        print(f"  MI-кэш сохранён → {CACHE_MI_FILE.name}")
    print()

    # ── Run param sets ─────────────────────────────────────────────────────────
    all_set_results = {}
    for pset in PARAM_SETS:
        results = []
        mode         = pset["mode"]
        night_filter = pset.get("night_filter", False)
        time_block_h = pset.get("time_block_h", [])

        # Per-pset timestamp window based on offset_days
        offset_ms       = pset.get("offset_days", 0) * 24 * 3600 * 1000
        pset_end_ms     = now_ms - offset_ms - OUTCOME_H * 3600 * 1000
        pset_start_ms   = pset_end_ms - DAYS_BACK * 24 * 3600 * 1000
        pset_timestamps = list(range(pset_start_ms, pset_end_ms, step_ms))

        def _process_symbol(symbol, ts_list):
            sym_results = []
            sym_funnel  = {}
            total_ticks = 0
            if PAIR_PARAMS.get(symbol) is None:
                return sym_results, sym_funnel, total_ticks
            funding_hist = candle_cache[symbol].get("funding_history", [])
            oi_hist      = candle_cache[symbol].get("oi_history", [])
            h4_all  = candle_cache[symbol]["4h"]   # sorted oldest→newest
            h1_all  = candle_cache[symbol]["1h"]
            h15_all = candle_cache[symbol]["15m"]
            h5_all  = candle_cache[symbol].get("5m", [])
            # Mark/index 15m (may be absent if mi_cache missing this symbol)
            mark15_all = mi_cache.get(symbol, {}).get("mark_15m", [])
            idx15_all  = mi_cache.get(symbol, {}).get("index_15m", [])
            # Pre-build timestamp index for fast bisect lookup
            h4_ts    = [int(c[0]) for c in h4_all]
            h1_ts    = [int(c[0]) for c in h1_all]
            h15_ts   = [int(c[0]) for c in h15_all]
            h5_ts    = [int(c[0]) for c in h5_all]
            mark15_ts = [int(c[0]) for c in mark15_all]
            idx15_ts  = [int(c[0]) for c in idx15_all]

            for ts_ms in ts_list:
                # Slice: candles visible at ts_ms (ts < ts_ms), newest-first
                i4  = bisect.bisect_left(h4_ts,  ts_ms)
                i1  = bisect.bisect_left(h1_ts,  ts_ms)
                i15 = bisect.bisect_left(h15_ts, ts_ms)
                i5  = bisect.bisect_left(h5_ts,  ts_ms)
                im  = bisect.bisect_left(mark15_ts, ts_ms)
                ii  = bisect.bisect_left(idx15_ts,  ts_ms)
                raw_4h   = list(reversed(h4_all [max(0, i4  - 60):i4 ]))
                raw_1h   = list(reversed(h1_all [max(0, i1  - 60):i1 ]))
                raw_15m  = list(reversed(h15_all[max(0, i15 - 96):i15]))
                raw_5m   = list(reversed(h5_all [max(0, i5  - 30):i5 ])) if h5_all else None
                raw_mark = list(reversed(mark15_all[max(0, im - 10):im])) if mark15_all else []
                raw_idx  = list(reversed(idx15_all [max(0, ii - 10):ii])) if idx15_all else []

                hist_funding    = _get_hist_funding(funding_hist, ts_ms)
                oi_delta        = _get_oi_delta(oi_hist, ts_ms)
                trade_delta_15m = _candle_vol_delta(raw_5m)

                # last price at signal time = close of most recent 15m bar
                last_price = float(raw_15m[0][4]) if raw_15m else 0.0
                basis_pct, perp_div_4bar = _calc_basis_and_perp_div(
                    raw_mark, raw_idx, last_price, raw_15m)

                sig = compute_signal(
                    raw_4h, raw_1h, raw_15m,
                    funding=hist_funding,
                    symbol=symbol, mode=mode, night_filter=night_filter,
                    oi_delta=oi_delta,
                    time_block_h=time_block_h,
                    raw_5m=raw_5m,
                    trade_delta_15m=trade_delta_15m,
                )
                if sig is None:
                    continue
                total_ticks += 1
                if sig["entry_signal"] != "ENTRY" or not sig["sl"] or not sig["tp"]:
                    reason = sig.get("drop_reason") or "unknown"
                    sym_funnel[reason] = sym_funnel.get(reason, 0) + 1
                    continue
                # perp_div SHORT veto: block shorts when futures running ahead of spot
                if (PERP_DIV_SHORT_VETO is not None
                        and sig["side"] == "sell"
                        and perp_div_4bar is not None
                        and perp_div_4bar > PERP_DIV_SHORT_VETO):
                    sym_funnel["perp_div_short_veto"] = sym_funnel.get("perp_div_short_veto", 0) + 1
                    continue
                # Attach fair-value features
                sig["basis_pct"]    = basis_pct
                sig["perp_div_4bar"] = perp_div_4bar
                sym_results.append((ts_ms, sig))
            return sym_results, sym_funnel, total_ticks

        # Collect signals
        pending      = []
        pset_funnel  = {}
        pset_ticks   = 0
        for symbol in SYMBOLS:
            sym_res, sym_funnel, sym_ticks = _process_symbol(symbol, pset_timestamps)
            for ts_ms, sig in sym_res:
                pending.append((symbol, ts_ms, sig))
            for k, v in sym_funnel.items():
                pset_funnel[k] = pset_funnel.get(k, 0) + v
            pset_ticks += sym_ticks
        for symbol in EXTRA_SYMBOLS:
            sym_res, sym_funnel, sym_ticks = _process_symbol(symbol, pset_timestamps[::6])
            for ts_ms, sig in sym_res:
                pending.append((symbol, ts_ms, sig))
            for k, v in sym_funnel.items():
                pset_funnel[k] = pset_funnel.get(k, 0) + v
            pset_ticks += sym_ticks

        # Fetch outcomes
        print(f"  Найдено сигналов: {len(pending)} — загружаю outcomes...")
        for i, (symbol, ts_ms, sig) in enumerate(pending, 1):
            fwd_end = ts_ms + OUTCOME_H * 3600 * 1000 + step_ms
            if i % 20 == 0 or i == len(pending):
                print(f"    {i}/{len(pending)} ({symbol})")
            async with _api_sem:
                raw_fwd = await client.get_history_candles(
                    symbol, "5m", after=fwd_end, limit=288)
            await asyncio.sleep(0.1)

            # FAST night hold matches prod: 240m at night (01-07 UTC), 120m otherwise
            if sig["trade_style"] == "FAST":
                max_h = (240 if sig.get("is_night") else HOLD_FAST_M) * 60 * 1000
            else:
                max_h = hold_ms.get(sig["trade_style"], hold_ms["SWING"])
            outcome, elapsed, exit_price, mfe_r = check_outcome(
                raw_fwd, sig["side"], sig["sl"], sig["tp"],
                ts_ms, max_h, entry_price=sig["close"],
            )
            sl_dist_abs = abs(sig["close"] - sig["sl"])
            exit_r = 0.0
            if exit_price and sl_dist_abs > 0:
                direction  = 1 if sig["side"] == "buy" else -1
                exit_r = round(direction * (exit_price - sig["close"]) / sl_dist_abs, 2)
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            results.append({
                "symbol":     symbol,
                "ts":         dt.strftime("%m-%d %H:%M"),
                "ts_ms":      ts_ms,
                "hour":       dt.hour,
                "style":      sig["trade_style"],
                "regime":     sig["regime"],
                "side":       sig["side"],
                "close":      sig["close"],
                "sl":         sig["sl"],
                "tp":         sig["tp"],
                "exit_price": exit_price,
                "sl_dist":    abs(sig["close"] - sig["sl"]) / sig["close"],
                "outcome":    outcome,
                "elapsed_m":  elapsed,
                "mfe_r":      mfe_r,
                "exit_r":     exit_r,
                "day_pos":       sig["day_position"],
                "bb_width":      sig["bb_width"],
                "slope_1h":      sig.get("slope_1h"),
                "slope_1h_prev": sig.get("slope_1h_prev"),
                "slope_15m":     sig.get("slope_15m"),
                "basis_pct":     sig.get("basis_pct"),
                "perp_div_4bar": sig.get("perp_div_4bar"),
            })

        all_set_results[pset["label"]] = (results, pset_funnel, pset_ticks)

    await client.close()

    # ── Report ─────────────────────────────────────────────────────────────────
    def _report(label, results, funnel=None, total_ticks=0, days_back=DAYS_BACK):
        wins   = [r for r in results if r["outcome"] == "TP"]
        losses = [r for r in results if r["outcome"] == "STOP"]
        time_x = [r for r in results if r["outcome"] == "TIME_EXIT"]
        total_r = len(wins) + len(losses)
        winrate = len(wins) * 100 // total_r if total_r > 0 else 0

        def _r(r):
            sl_d = abs(r["close"] - r["sl"])
            tp_d = abs(r["tp"] - r["close"]) if r.get("tp") else sl_d
            return tp_d / sl_d if sl_d > 0 else 1.0

        gross_w = sum(_r(r) for r in wins)
        gross_l = len(losses)   # each stop = 1R
        pf = round(gross_w / gross_l, 2) if gross_l > 0 else 99.0

        max_dd = consec = 0
        for r in results:
            if r["outcome"] == "STOP": consec += 1
            else: consec = 0
            max_dd = max(max_dd, consec)

        signals_per_day = round(len(results) / days_back, 1)

        # Honest WR: TP / all signals (including TIME_EXIT)
        wr_all = len(wins) * 100 // len(results) if results else 0

        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"{'='*60}")
        print(f"  Сигналов всего:   {len(results)}  ({signals_per_day}/день)")
        print(f"  ✅ TP:  {len(wins)}  ❌ SL: {len(losses)}  ⏱ TIME: {len(time_x)}")
        print(f"  Winrate (TP/all): {wr_all}%  ({len(wins)}/{len(results)})")
        print(f"  Winrate (TP+SL):  {winrate}%  ({len(wins)}/{total_r})  [без TIME_EXIT]")
        print(f"  Profit Factor:    {pf}")
        print(f"  Max серия стопов: {max_dd}")

        # TIME_EXIT diagnostics
        if time_x:
            tx_mfe   = [r["mfe_r"]  for r in time_x if r.get("mfe_r")  is not None]
            tx_exit  = [r["exit_r"] for r in time_x if r.get("exit_r") is not None]
            avg_mfe  = round(sum(tx_mfe)  / len(tx_mfe),  2) if tx_mfe  else 0
            avg_exit = round(sum(tx_exit) / len(tx_exit), 2) if tx_exit else 0
            pos_exit = sum(1 for x in tx_exit if x >  0)
            half_r   = sum(1 for x in tx_mfe  if x >= 0.5)
            print(f"  ⏱ TIME_EXIT диагностика:")
            print(f"    avg mfe_r:  {avg_mfe}R  (пик внутри окна)")
            print(f"    avg exit_r: {avg_exit}R  (итог при выходе)")
            print(f"    exit > 0R:  {pos_exit}/{len(time_x)}  ({pos_exit*100//len(time_x)}%)")
            print(f"    mfe ≥ 0.5R: {half_r}/{len(time_x)}  ({half_r*100//len(time_x)}%)")

        for side in ("buy", "sell"):
            sr  = [r for r in results if r["side"] == side]
            sw  = [r for r in sr if r["outcome"] == "TP"]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw) * 100 // (len(sw) + len(sl_)) if (len(sw) + len(sl_)) > 0 else 0
                print(f"  {'LONG' if side == 'buy' else 'SHORT'}:  "
                      f"{len(sr)} сигналов | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        print(f"\n  По стилю:")
        for style in ("FAST", "SWING", "BOUNCE"):
            sr  = [r for r in results if r["style"] == style]
            sw  = [r for r in sr if r["outcome"] == "TP"]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw) * 100 // (len(sw) + len(sl_)) if (len(sw) + len(sl_)) > 0 else 0
                print(f"    {style:8}: {len(sr):3} | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        print(f"\n  По режиму рынка:")
        for reg in ("TRENDING", "DRIFT", "RANGING", "CHOPPY"):
            sr  = [r for r in results if r.get("regime") == reg]
            sw  = [r for r in sr if r["outcome"] == "TP"]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw) * 100 // (len(sw) + len(sl_)) if (len(sw) + len(sl_)) > 0 else 0
                print(f"    {reg:8}: {len(sr):3} | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        print(f"\n  По паре:")
        for sym in ALL_SYMBOLS:
            sr  = [r for r in results if r["symbol"] == sym]
            sw  = [r for r in sr if r["outcome"] == "TP"]
            sl_ = [r for r in sr if r["outcome"] == "STOP"]
            if sr:
                wr = len(sw) * 100 // (len(sw) + len(sl_)) if (len(sw) + len(sl_)) > 0 else 0
                print(f"    {sym:12}: {len(sr):3} | ✅{len(sw)} ❌{len(sl_)} | {wr}%")

        hour_wins  = {}
        hour_total = {}
        for r in results:
            h = r["hour"]
            hour_total[h] = hour_total.get(h, 0) + 1
            if r["outcome"] == "TP":
                hour_wins[h] = hour_wins.get(h, 0) + 1
        if hour_total:
            hour_wr = {h: hour_wins.get(h, 0) * 100 // hour_total[h]
                       for h in hour_total if hour_total[h] >= 2}
            if hour_wr:
                best  = max(hour_wr, key=hour_wr.get)
                worst = min(hour_wr, key=hour_wr.get)
                print(f"\n  Лучший час:  {best:02d}:00 UTC — {hour_wr[best]}% ({hour_total[best]} сигналов)")
                print(f"  Худший час:  {worst:02d}:00 UTC — {hour_wr[worst]}% ({hour_total[worst]} сигналов)")

        print(f"\n  PASS/FAIL:")
        print(f"    Winrate ≥55%:      {'✅' if winrate >= 55 else '❌'}  {winrate}%")
        print(f"    Profit Factor ≥1.3:{'✅' if pf >= 1.3  else '❌'}  {pf}")
        print(f"    Сигналов/день ≥2:  {'✅' if signals_per_day >= 2 else '❌'}  {signals_per_day}")
        print(f"    Max серия SL ≤5:   {'✅' if max_dd <= 5 else '❌'}  {max_dd}")

        # ── Balance simulation: one position per symbol, 1% risk ──────────────
        # Simulates real bot behavior: new signal skipped if position already open
        START = 1000.0
        balance      = START
        peak         = START
        max_drawdown = 0.0
        executed     = 0
        skipped      = 0
        pos_close_ms = {}   # (symbol, side) → timestamp when position closes

        sorted_r = sorted(results, key=lambda r: r.get("ts_ms", 0))
        for r in sorted_r:
            sym     = r["symbol"]
            side_r  = r["side"]
            ts      = r["ts_ms"]
            elapsed = r.get("elapsed_m")
            max_hold_ms_sym = hold_ms.get(r["style"], hold_ms["SWING"])
            pos_key = sym  # one position per pair (any side)

            # Block same pair regardless of direction — matches real bot behaviour
            if pos_close_ms.get(pos_key, 0) > ts:
                r["executed"] = False
                skipped += 1
                continue

            # Register position close time
            if elapsed is not None:
                close_at = ts + elapsed * 60 * 1000
            else:
                close_at = ts + max_hold_ms_sym
            pos_close_ms[pos_key] = close_at

            pnl = calc_pnl(
                r["outcome"], r["side"], r["close"],
                r["sl"], r.get("tp"), r.get("exit_price"),
                balance,
            )
            r["pnl"]      = pnl
            r["executed"] = True
            balance += pnl
            if balance > peak:
                peak = balance
            dd = (peak - balance) / peak * 100
            if dd > max_drawdown:
                max_drawdown = dd
            executed += 1

        total_pct = (balance - START) / START * 100
        sign = "+" if total_pct >= 0 else ""
        print(f"\n  ── Симуляция баланса (1 позиция/пара, нотионал {NOTIONAL_RATIO}×баланс, плечо x{LEVERAGE}) ───")
        print(f"  Старт:            $1000")
        print(f"  Финиш:            ${balance:.0f}  ({sign}{total_pct:.1f}%)")
        print(f"  Макс. просадка:   {max_drawdown:.1f}%")
        print(f"  Исполнено:        {executed}  |  Пропущено: {skipped} (позиция занята)")

        # Per-symbol skipped breakdown
        skip_by_sym = {}
        for r in sorted_r:
            if not r.get("executed", True):
                skip_by_sym[r["symbol"]] = skip_by_sym.get(r["symbol"], 0) + 1
        if skip_by_sym:
            parts = "  ".join(f"{s.split('-')[0]}:{n}" for s, n in skip_by_sym.items())
            print(f"  Пропущено по паре: {parts}")

        # ── Signal funnel ────────────────────────────────────────────────────
        if funnel and total_ticks > 0:
            entry_cnt = len(results)
            print(f"\n  Воронка отсева ({total_ticks} тиков → {entry_cnt} ENTRY):")
            for reason, cnt in sorted(funnel.items(), key=lambda x: -x[1]):
                pct = cnt * 100 // total_ticks
                bar = "▓" * (pct // 2)
                print(f"    {reason:30} {cnt:5}  {pct:2}%  {bar}")

    # ── OVERALL: aggregate across all periods ──────────────────────────────────
    overall_results = []
    overall_funnel  = {}
    overall_ticks   = 0
    for res, funnel, ticks in all_set_results.values():
        overall_results.extend(res)
        overall_ticks += ticks
        for k, v in funnel.items():
            overall_funnel[k] = overall_funnel.get(k, 0) + v
    total_days = DAYS_BACK * len(PARAM_SETS)
    _report(
        f"OVERALL | full {total_days}d (все периоды объединены)",
        overall_results, overall_funnel, overall_ticks,
        days_back=total_days,
    )

    # ── Per-period breakdown ────────────────────────────────────────────────────
    for label, (res, funnel, ticks) in all_set_results.items():
        _report(label, res, funnel, ticks)

    # ── Save results JSON for distribution analysis ────────────────────────────
    import json as _json
    _results_path = BACKTEST_RUNS_DIR / f"backtest_results_{BT_RUN_TAG}.json"
    with open(_results_path, "w", encoding="utf-8") as _f:
        _json.dump(overall_results, _f, ensure_ascii=False, default=str)
    print(f"\n  Results JSON -> {_results_path.name}  ({len(overall_results)} записей)")


if __name__ == "__main__":
    _log_handle, _orig_stdout, _orig_stderr = _enable_run_log()
    try:
        asyncio.run(run())
    finally:
        sys.stdout = _orig_stdout
        sys.stderr = _orig_stderr
        _log_handle.close()
