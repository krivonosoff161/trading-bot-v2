"""
Chart Analyzer — pulls historical OKX data for a given moment and produces:
  - Console report
  - analysis_output/<sym>_<ts>_report.md
  - analysis_output/<sym>_<ts>_snapshot.json
  - analysis_output/<sym>_<ts>_annotated.png  (if --image is provided)

Usage (manual):
    python scripts/analyze_chart.py --symbol XRP-USDT --captured-at "2026-03-09T11:42:35Z"

Usage (with screenshot):
    python scripts/analyze_chart.py --symbol XRP-USDT --captured-at "2026-03-09T11:42:35Z" --image "обучение/xrp.jpg"

Tip: use analyze_latest.bat to find the latest screenshot automatically.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from src.exchange.okx_client import OKXClient
from src.strategy.indicators import (
    calc_adx, calc_atr, calc_ema, calc_sma, parse_candles, parse_volumes,
    find_swing_levels, atr_regime, calc_bollinger_bands, calc_supertrend,
    calc_chandelier_exit, calc_rsi,
)
from src.strategy.signal import get_signal


# ── Config ────────────────────────────────────────────────────────────────────

def load_strategy_params() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("strategy", {})


def load_symbol_min_sl_percent(symbol: str) -> float:
    """Read min_sl_percent for the given symbol from config.yaml trading.symbols."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for s in cfg.get("trading", {}).get("symbols", []):
        if s.get("id") == symbol:
            return float(s.get("min_sl_percent", 0.003))
    return 0.003  # fallback if symbol not in config


# ── Helpers ───────────────────────────────────────────────────────────────────

def ts_to_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def ok(cond: bool) -> str:
    return "✓" if cond else "✗"


def confirm_label(candles: list, idx: int = 0) -> str:
    try:
        val = int(candles[idx][8]) if len(candles[idx]) > 8 else 1
        return "closed" if val == 1 else "FORMING — not closed yet!"
    except (IndexError, ValueError):
        return "unknown"


def _stopped_stage(reason: str) -> str:
    if reason in ("not_enough_candles", "no_trend_1h", "atr_zero"):
        return "1H"
    if reason in ("no_pullback_15m", "pullback_volume_strong"):
        return "15m"
    if reason in ("no_breakout_5m", "breakout_volume_weak", "di_not_confirmed_5m"):
        return "5m"
    if reason == "trend_pullback_breakout":
        return "PASSED"
    return reason


def _json_safe(value):
    """Recursively convert numpy scalars/containers to JSON-serializable Python types."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _fmt_metric(value: float) -> str:
    """Format small metrics with enough precision for low-price instruments."""
    val = float(value)
    abs_val = abs(val)
    if abs_val >= 1:
        return f"{val:.4f}"
    if abs_val >= 0.01:
        return f"{val:.5f}"
    return f"{val:.6f}"


def _fmt_price(symbol: str, price) -> str:
    """Format price based on instrument scale: BTC→1dp, ETH/SOL→2dp, others→4dp."""
    if price is None:
        return "—"
    base = symbol.split("-")[0].upper()
    if base == "BTC":
        return f"{float(price):.1f}"
    if base in ("ETH", "SOL"):
        return f"{float(price):.2f}"
    return f"{float(price):.4f}"


def _next_candle_close(captured_at: str, tf_minutes: int) -> str:
    """Return next candle close boundary as HH:MM UTC string."""
    try:
        dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        total_min = dt.hour * 60 + dt.minute
        next_boundary = ((total_min // tf_minutes) + 1) * tf_minutes
        h, m = divmod(next_boundary % (24 * 60), 60)
        return f"{h:02d}:{m:02d} UTC"
    except Exception:
        return "—"


# ── Core analysis ─────────────────────────────────────────────────────────────

def analyze(raw_1h: list, raw_15m: list, raw_5m: list, params: dict, min_sl_percent: float = 0.003, raw_4h: list | None = None) -> dict:
    ema_fast   = int(params["ema_fast"])
    ema_slow   = int(params["ema_slow"])
    adx_period = int(params["adx_period"])
    adx_thresh    = float(params["adx_threshold_1h"])
    adx_thresh_4h = float(params.get("adx_threshold_4h", adx_thresh))
    scalp_enabled = bool(params.get("scalp_enabled", True))
    pb_touch   = float(params["pullback_touch_atr"])
    pb_bars    = int(params["pullback_volume_bars"])
    pb_factor  = float(params["pullback_volume_factor"])
    bk_lookbk  = int(params["breakout_lookback_5m"])
    vol_period = int(params["trigger_volume_ma_period"])
    vol_factor = float(params["trigger_volume_factor"])
    sl_buffer  = float(params["sl_buffer_atr"])
    tp_r       = float(params["tp_r_multiple"])

    # ── 4H (старший контекст) ────────────────────────────────────────────────
    h4_data: dict = {}
    if raw_4h:
        highs_4h, lows_4h, closes_4h = parse_candles(raw_4h)
        adx_4h, plus_di_4h, minus_di_4h = calc_adx(highs_4h, lows_4h, closes_4h, period=adx_period, bar_index=-2)
        ema20_4h = calc_ema(closes_4h, ema_fast)
        ema50_4h = calc_ema(closes_4h, ema_slow)
        bull_4h = ema20_4h[-2] > ema50_4h[-2] and plus_di_4h > minus_di_4h and adx_4h >= adx_thresh
        bear_4h = ema20_4h[-2] < ema50_4h[-2] and minus_di_4h > plus_di_4h and adx_4h >= adx_thresh
        bb_4h   = calc_bollinger_bands(closes_4h, period=20, std_mult=2.5)
        # Range mode: low BandWidth + weak trend = market consolidating
        range_mode_4h = bb_4h["width_pct"] < 12.0 and adx_4h < 25
        h4_data = {
            "ema20":      round(float(ema20_4h[-2]), 6),
            "ema50":      round(float(ema50_4h[-2]), 6),
            "adx":        round(adx_4h, 1),
            "plus_di":    round(plus_di_4h, 1),
            "minus_di":   round(minus_di_4h, 1),
            "bull":       bool(bull_4h),
            "bear":       bool(bear_4h),
            "bb_upper":   bb_4h["upper"],
            "bb_lower":   bb_4h["lower"],
            "bb_width":   bb_4h["width_pct"],
            "range_mode": bool(range_mode_4h),
        }

    # ── 1H ──────────────────────────────────────────────────────────────────
    highs_1h, lows_1h, closes_1h = parse_candles(raw_1h)
    adx, plus_di, minus_di = calc_adx(
        highs_1h, lows_1h, closes_1h, period=adx_period, bar_index=-2
    )
    ema20_1h = calc_ema(closes_1h, ema_fast)
    ema50_1h = calc_ema(closes_1h, ema_slow)
    atr_1h   = calc_atr(highs_1h, lows_1h, closes_1h, period=adx_period)
    rsi_1h   = calc_rsi(closes_1h, period=14)
    bb_1h   = calc_bollinger_bands(closes_1h, period=20, std_mult=2.0)
    bull_1h = ema20_1h[-2] > ema50_1h[-2] and plus_di > minus_di and adx >= adx_thresh
    bear_1h = ema20_1h[-2] < ema50_1h[-2] and minus_di > plus_di and adx >= adx_thresh

    # ── 15m ─────────────────────────────────────────────────────────────────
    highs_15m, lows_15m, closes_15m = parse_candles(raw_15m)
    vols_15m  = parse_volumes(raw_15m)
    atr_15m   = calc_atr(highs_15m, lows_15m, closes_15m, period=adx_period)
    ema20_15m = calc_ema(closes_15m, ema_fast)
    ema50_15m = calc_ema(closes_15m, ema_slow)
    rsi_15m   = calc_rsi(closes_15m, period=14)

    cur_close = closes_15m[-2]
    near_ema  = abs(cur_close - ema20_15m[-2]) <= pb_touch * atr_15m

    if bull_1h:
        structure_ok = cur_close > ema50_15m[-2]
    elif bear_1h:
        structure_ok = cur_close < ema50_15m[-2]
    else:
        structure_ok = False

    recent_vols = vols_15m[-pb_bars - 2:-2]
    prior_vols  = vols_15m[-pb_bars * 2 - 2:-pb_bars - 2]
    avg_recent  = float(np.mean(recent_vols)) if len(recent_vols) > 0 else 1.0
    avg_prior   = float(np.mean(prior_vols))  if len(prior_vols)  > 0 else 1.0
    pb_vol_weak = not (avg_prior > 0 and avg_recent > avg_prior * pb_factor)

    # ── 5m ──────────────────────────────────────────────────────────────────
    highs_5m, lows_5m, closes_5m = parse_candles(raw_5m)
    vols_5m = parse_volumes(raw_5m)
    _, plus_di_5m, minus_di_5m = calc_adx(
        highs_5m, lows_5m, closes_5m, period=adx_period, bar_index=-2
    )
    trigger_close = closes_5m[-2]
    trigger_vol   = vols_5m[-2]
    vol_sma       = calc_sma(vols_5m[:-2], vol_period)
    vol_ratio     = round(trigger_vol / vol_sma, 2) if vol_sma > 0 else 0.0
    vol_strong    = trigger_vol >= vol_sma * vol_factor

    lookback_highs = highs_5m[-bk_lookbk - 2:-2]
    lookback_lows  = lows_5m[-bk_lookbk - 2:-2]

    if bull_1h:
        breakout      = bool(trigger_close > float(np.max(lookback_highs))) if len(lookback_highs) else False
        di_confirm_5m = plus_di_5m > minus_di_5m
    elif bear_1h:
        breakout      = bool(trigger_close < float(np.min(lookback_lows))) if len(lookback_lows) else False
        di_confirm_5m = minus_di_5m > plus_di_5m
    else:
        breakout      = False
        di_confirm_5m = False

    signal = get_signal(raw_1h, raw_15m, raw_5m, params)

    # ── Action View ──────────────────────────────────────────────────────────
    # Entry suggestion based on current 5m close
    entry_price = float(trigger_close)
    if signal.get("side"):
        setup_sl = float(signal.get("setup_sl", 0))
        sl_dist_structure = abs(entry_price - setup_sl)
        sl_min_atr  = float(params.get("sl_min_atr", 1.2))
        sl_dist_min = max(entry_price * min_sl_percent, sl_min_atr * atr_15m)
        sl_dist = max(sl_dist_structure, sl_dist_min)
        tp_dist = sl_dist * tp_r
        # Cap TP at ATR(1H)-based realistic daily move
        tp1_cap = 1.5 * atr_1h
        tp2_cap = 2.5 * atr_1h
        if signal["side"] == "buy":
            sl_price  = round(entry_price - sl_dist, 4)
            tp_price  = round(entry_price + min(tp_dist,       tp1_cap), 4)
            tp2_price = round(entry_price + min(tp_dist * 1.5, tp2_cap), 4)
        else:
            sl_price  = round(entry_price + sl_dist, 4)
            tp_price  = round(entry_price - min(tp_dist,       tp1_cap), 4)
            tp2_price = round(entry_price - min(tp_dist * 1.5, tp2_cap), 4)
        action = {
            "valid": True,
            "entry":      entry_price,
            "sl":         sl_price,
            "tp1":        tp_price,
            "tp2":        tp2_price,
            "sl_dist":    round(sl_dist, 4),
            "r_multiple": tp_r,
            "type":       "confirmed",  # breakout closed = confirmed
        }
    else:
        # Near-setup action hints even without signal
        action = {
            "valid": False,
            "entry": None, "sl": None, "tp1": None, "tp2": None,
            "hint": _action_hint(bear_1h, bull_1h, near_ema, m_close=cur_close,
                                 ema20=float(ema20_15m[-2]), atr=atr_15m),
        }

    # ── Swing structure + volatility regime (15m) ────────────────────────────
    swings_15m        = find_swing_levels(highs_15m, lows_15m, lookback=3, count=4)
    atr_pct, atr_lbl  = atr_regime(highs_15m, lows_15m, closes_15m, period=adx_period)
    bb_15m            = calc_bollinger_bands(closes_15m, period=20, std_mult=2.0)
    supertrend_15m    = calc_supertrend(highs_15m, lows_15m, closes_15m, period=14, multiplier=3.0)
    ce_1h             = calc_chandelier_exit(highs_1h, lows_1h, closes_1h, lookback=22, multiplier=3.5)

    # ── Pending plan — levels for WATCH mode (trend+structure, no signal yet) ─
    pending_plan: dict = {"available": False}
    if (bull_1h or bear_1h) and structure_ok and not signal.get("side"):
        zone   = float(ema20_15m[-2])
        inv    = float(ema50_15m[-2])
        s_high = swings_15m["recent_highs"]
        s_low  = swings_15m["recent_lows"]
        if bull_1h:
            above_zone = [h for h in s_high if h > zone]
            trigger    = min(above_zone) if above_zone else round(zone + 0.5 * atr_15m, 6)
            below_zone = [l for l in s_low  if l < zone]
            sl_ref     = max(below_zone) if below_zone else inv
            sl         = round(sl_ref - sl_buffer * atr_15m, 6)
            sl_dist    = trigger - sl   # R:R anchored to trigger, not zone
        else:  # bear
            below_zone = [l for l in s_low  if l < zone]
            trigger    = max(below_zone) if below_zone else round(zone - 0.5 * atr_15m, 6)
            above_zone = [h for h in s_high if h > zone]
            sl_ref     = min(above_zone) if above_zone else inv
            sl         = round(sl_ref + sl_buffer * atr_15m, 6)
            sl_dist    = sl - trigger   # R:R anchored to trigger, not zone
        if sl_dist > 0:
            tp1_cap = 1.5 * atr_1h
            tp2_cap = 2.5 * atr_1h
            if bull_1h:
                tp1 = round(trigger + min(sl_dist * tp_r,       tp1_cap), 6)
                tp2 = round(trigger + min(sl_dist * tp_r * 1.5, tp2_cap), 6)
            else:
                tp1 = round(trigger - min(sl_dist * tp_r,       tp1_cap), 6)
                tp2 = round(trigger - min(sl_dist * tp_r * 1.5, tp2_cap), 6)
            pending_plan = {
                "available":    True,
                "entry_zone":   round(zone, 6),
                "trigger":      round(trigger, 6),
                "sl":           round(sl, 6),
                "tp1":          tp1,
                "tp2":          tp2,
                "invalidation": round(inv, 6),
            }

    return {
        "1h": {
            "ema20": round(float(ema20_1h[-2]), 6),
            "ema50": round(float(ema50_1h[-2]), 6),
            "adx": round(adx, 2),
            "plus_di": round(plus_di, 2),
            "minus_di": round(minus_di, 2),
            "bull": bull_1h, "bear": bear_1h,
            "rsi": round(rsi_1h, 1),
            "bb_width_pct": bb_1h["width_pct"],
        },
        "15m": {
            "close": round(float(cur_close), 6),
            "ema20": round(float(ema20_15m[-2]), 6),
            "ema50": round(float(ema50_15m[-2]), 6),
            "atr": round(float(atr_15m), 6),
            "rsi": round(rsi_15m, 1),
            "near_ema": near_ema,
            "structure_ok": structure_ok,
            "vol_recent": round(avg_recent, 2),
            "vol_prior": round(avg_prior, 2),
            "vol_ratio_pb": round(avg_recent / avg_prior, 2) if avg_prior > 0 else 0.0,
            "pb_vol_weak": pb_vol_weak,
            "pb_touch_threshold": round(pb_touch * atr_15m, 6),
            "atr_pct":   atr_pct,
            "atr_label": atr_lbl,
            "swing_highs": swings_15m["recent_highs"],
            "swing_lows":  swings_15m["recent_lows"],
            "bb_upper":    bb_15m["upper"],
            "bb_middle":   bb_15m["middle"],
            "bb_lower":    bb_15m["lower"],
            "bb_pct_b":    bb_15m["pct_b"],
            "bb_width_pct": bb_15m["width_pct"],
            "supertrend":       supertrend_15m["value"],
            "supertrend_dir":   supertrend_15m["direction"],
            "supertrend_dist":  supertrend_15m["distance_pct"],
            "ce_long":          ce_1h["ce_long"],
            "ce_short":         ce_1h["ce_short"],
        },
        "5m": {
            "trigger_close": round(float(trigger_close), 6),
            "trigger_vol": round(float(trigger_vol), 2),
            "vol_sma": round(float(vol_sma), 2),
            "vol_ratio": vol_ratio,
            "vol_strong": vol_strong,
            "breakout": breakout,
            "di_confirm": di_confirm_5m,
            "plus_di": round(plus_di_5m, 2),
            "minus_di": round(minus_di_5m, 2),
        },
        "4h":           h4_data,
        "signal":       signal,
        "action":       action,
        "pending_plan": pending_plan,
    }


def _action_hint(bear: bool, bull: bool, near_ema: bool,
                 m_close: float, ema20: float, atr: float) -> str:
    if not bear and not bull:
        return "Рынок движется в боковике без чёткого направления. Ждать пока рынок выберет сторону."
    direction = "ШОРТ" if bear else "ЛОНГ"
    if not near_ema:
        return f"Направление есть ({direction}), но цена ещё далеко от удобной точки входа ({ema20:.4f}). Ждать отката."
    return f"Цена у удобной точки входа для {direction}. Ждать подтверждения на 5-минутном графике."


# ── Client summary ────────────────────────────────────────────────────────────

def _reason_to_human(reason: str) -> str:
    """Translate internal bot reason code to a human-readable sentence."""
    mapping = {
        "not_enough_candles":     "Недостаточно данных для анализа.",
        "no_trend_1h":            "Нет выраженного тренда — рынок в боковике.",
        "atr_zero":               "Недостаточно данных для анализа.",
        "no_pullback_15m":        "Цена ещё не вернулась к зоне EMA — сетапа нет.",
        "pullback_volume_strong": "Откат слишком агрессивный — структура не подходит.",
        "no_breakout_5m":         "Сетап формируется — ждём подтверждения на 5m.",
        "breakout_volume_weak":   "Движение без объёма — неуверенный пробой.",
        "di_not_confirmed_5m":    "Направление на 5m не совпадает с трендом.",
    }
    return mapping.get(reason, "Условия входа не выполнены.")


def build_client_summary(symbol: str, captured_at: str, r: dict) -> str:
    """Three output modes based on market state:
    - ГОТОВ ВХОД  (act["valid"])
    - НАБЛЮДАЕМ   (trend + structure ok, no confirmed entry)
    - ВНЕ РЫНКА   (no trend, or trend with broken structure)
    """
    h      = r["1h"]
    m      = r["15m"]
    f      = r["5m"]
    sig    = r["signal"]
    act    = r["action"]
    pp     = r.get("pending_plan", {"available": False})
    reason = sig.get("reason", "")

    try:
        dt     = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        ts_str = dt.strftime("%d %b %Y  %H:%M UTC")
    except Exception:
        ts_str = captured_at

    has_trend  = h["bull"] or h["bear"]
    setup_zone = m["near_ema"] and m["structure_ok"]
    gap        = abs(m["close"] - m["ema20"])
    threshold  = m["pb_touch_threshold"]

    if gap <= threshold:
        zone_prox = "уже в зоне"
    elif gap <= threshold * 2:
        zone_prox = "приближается к зоне"
    else:
        zone_prox = "пока не добралась до зоны"

    adx_word   = "устойчивый" if h["adx"] >= 25 else "умеренный" if h["adx"] >= 20 else "слабый"
    em_rel     = "выше" if h["ema20"] > h["ema50"] else "ниже"
    pb_char    = "с умеренным объёмом" if m["pb_vol_weak"] else "с повышенным объёмом"
    side_above = "выше" if h["bull"] else "ниже"
    side_below = "ниже" if h["bull"] else "выше"
    bk_dir     = "выше" if h["bull"] else "ниже"
    dir_long   = h["bull"]

    # ── Mode classification ──────────────────────────────────────────────────
    mode_entry    = act["valid"]
    mode_no_trend = not has_trend
    mode_broken   = has_trend and not m["structure_ok"]
    mode_off      = mode_no_trend or mode_broken   # ВНЕ РЫНКА
    # mode_watch  = has_trend and m["structure_ok"] and not mode_entry  # НАБЛЮДАЕМ

    # ── Status + direction labels (top lines, not block headers) ────────────
    if mode_entry:
        status_label = "ГОТОВ ВХОД"
        dir_label    = "только LONG — короткая сторона не рассматривается" if dir_long \
                       else "только SHORT — длинная сторона не рассматривается"
    elif mode_no_trend:
        status_label = "ВНЕ РЫНКА"
        dir_label    = "направления нет — ни LONG, ни SHORT не рассматриваются"
    elif mode_broken:
        status_label = "ВНЕ РЫНКА"
        dir_label    = "только LONG — но после восстановления структуры на 15m" if dir_long \
                       else "только SHORT — но после восстановления структуры на 15m"
    else:
        status_label = "НАБЛЮДАЕМ"
        dir_label    = "только LONG — короткая сторона не рассматривается" if dir_long \
                       else "только SHORT — длинная сторона не рассматривается"

    SEP   = "═" * 46
    lines = [SEP, f"  {symbol}  |  {ts_str}", SEP, ""]
    lines.append(f"  Статус:      {status_label}")
    lines.append(f"  Направление: {dir_label}")
    lines.append("")

    # ── СЕЙЧАС НА РЫНКЕ ─────────────────────────────────────────────────────
    lines.append("СЕЙЧАС НА РЫНКЕ")
    if mode_entry:
        struct = "бычий" if dir_long else "медвежий"
        lines.append(f"  На 1H контекст {struct} — EMA20 {em_rel} EMA50, ADX {adx_word}. На 15m цена откатила к средней зоне {pb_char}, структура удержалась. На 5m зафиксирован пробой с объёмом.")
        lines.append("  (Проще: контекст, откат, пробой — всё сошлось.)")
    elif mode_no_trend:
        lines.append(f"  На 1H уверенного направленного движения нет — ADX {adx_word}, рынок не показывает ясного импульса.")
        if m["near_ema"]:
            local = f"  Локально на 15m цена у средней зоны (EMA20 ≈ {_fmt_price(symbol, m['ema20'])})"
            if m["structure_ok"]:
                local += f" {pb_char}"
            else:
                local += ", но структура пока не удержана"
            if f["breakout"] and f["vol_strong"]:
                local += "; на 5m есть движение, но без старшего контекста этого недостаточно."
            elif f["breakout"]:
                local += "; на 5m движение появилось, подтверждение ещё слабое."
            else:
                local += "; на 5m явного импульса пока нет."
            lines.append(local)
        elif f["vol_strong"]:
            lines.append("  На 5m есть локальная активность, но без контекста сверху это не основание для позиции.")
        lines.append("  (Проще: рынок не определился — вход без контекста это лотерея.)")
    elif mode_broken:
        struct = "бычий" if dir_long else "медвежий"
        lines.append(f"  На 1H контекст {struct} — EMA20 {em_rel} EMA50, ADX {adx_word}. Тренд есть. Но на 15m структура нарушена — цена {side_below} EMA50.")
        lines.append("  (Проще: направление понятно, но опорная зона пробита. Вход преждевременный.)")
    elif reason == "pullback_volume_strong":
        struct = "бычий" if dir_long else "медвежий"
        lines.append(f"  На 1H контекст {struct} — EMA20 {em_rel} EMA50, ADX {adx_word}. На 15m цена у зоны, но откат идёт с повышенным объёмом — давление на структуру.")
        lines.append("  (Проще: уровень правильный, но движение к нему слишком агрессивное.)")
    elif setup_zone and f["breakout"]:
        struct = "бычий" if dir_long else "медвежий"
        lines.append(f"  На 1H контекст {struct} — EMA20 {em_rel} EMA50, ADX {adx_word}. На 15m цена у зоны, на 5m появляется движение — но подтверждение ещё неполное.")
        lines.append("  (Проще: всё почти сходится — ждём последнего условия.)")
    elif setup_zone:
        struct = "бычий" if dir_long else "медвежий"
        lines.append(f"  На 1H контекст {struct} — EMA20 {em_rel} EMA50, ADX {adx_word}. На 15m цена у средней зоны {pb_char} — структура держится, откат выглядит как коррекция.")
        lines.append("  (Проще: контекст и уровень сошлись, ждём сигнала на 5m.)")
    else:
        struct   = "бычий" if dir_long else "медвежий"
        move_dir = "растёт" if dir_long else "падает"
        lines.append(f"  На 1H контекст {struct} — EMA20 {em_rel} EMA50, ADX {adx_word}. На 15m цена ещё не откатила к средней зоне — рынок {move_dir} без паузы.")
        lines.append("  (Проще: тренд есть, но цена не в том месте.)")
    # ATR regime note — only when not normal (adds soft volatility context)
    atr_lbl = m.get("atr_label", "")
    if "сжатие" in atr_lbl or "расширение" in atr_lbl:
        lines.append(f"  Волатильность: {atr_lbl}.")
    lines.append("")

    # ════════════════════════════════════════════════════════════════════════
    # MODE C — ГОТОВ ВХОД
    # ════════════════════════════════════════════════════════════════════════
    if mode_entry:
        lines.append("ПЛАН ВХОДА")
        lines.append(f"  Вход можно рассматривать около {_fmt_price(symbol, act['entry'])}.")
        lines.append(f"  Защитный выход:  {_fmt_price(symbol, act['sl'])}")
        lines.append(f"  Первая цель:     {_fmt_price(symbol, act['tp1'])}")
        lines.append(f"  Вторая цель:     {_fmt_price(symbol, act['tp2'])}")
        lines.append("")

        lines.append("ГДЕ ИДЕЯ ЛОМАЕТСЯ")
        lines.append("  Если цена пересечёт защитный выход — сценарий не работает, выходим.")
        lines.append("")

        lines.append("ГДЕ ЗАБРАТЬ ПРИБЫЛЬ")
        lines.append(f"  Первая цель: {_fmt_price(symbol, act['tp1'])} — можно зафиксировать часть.")
        lines.append(f"  Вторая цель: {_fmt_price(symbol, act['tp2'])} — если позиция всё ещё открыта.")
        lines.append("")

        lines.append("НЕ ДЕЛАТЬ")
        lines.append(f"  Не входить, если цена уже ушла далеко от {_fmt_price(symbol, act['entry'])}.")
        lines.append("  Не двигать стоп в убыточную сторону.")
        lines.append("  Не увеличивать позицию после открытия.")

    # ════════════════════════════════════════════════════════════════════════
    # MODE A — ВНЕ РЫНКА
    # ════════════════════════════════════════════════════════════════════════
    elif mode_off:
        lines.append("СЕЙЧАС ОРДЕР НЕ СТАВИМ")
        if mode_no_trend:
            lines.append("  Нет направленного контекста на 1H — оснований для позиции нет.")
        else:
            lines.append("  На 15m структура нарушена — сетап некачественный.")
        lines.append("")

        lines.append("ЧТО НУЖНО ДЛЯ ПОЯВЛЕНИЯ СЦЕНАРИЯ")
        if mode_no_trend:
            lines.append("  Уверенный импульс на 1H с расхождением EMA20/EMA50 и ADX ≥ 20.")
            lines.append("  Только после этого — возврат к оценке зоны входа.")
        else:
            lines.append(f"  Цена должна вернуться {side_above} EMA50 (15m) ≈ {_fmt_price(symbol, m['ema50'])} и закрепиться.")
            lines.append("  После этого — спокойный откат к EMA20 и подтверждение на 5m.")
        lines.append("")

        lines.append("НЕ ДЕЛАТЬ")
        if mode_no_trend:
            lines.append("  Не открывать позицию ни в LONG, ни в SHORT.")
            lines.append("  Не трактовать локальные движения на 5m как самостоятельный сигнал.")
        elif dir_long:
            lines.append("  Не открывать LONG — структура нарушена, вход преждевременный.")
            lines.append("  Не открывать SHORT против бычьего сценария.")
        else:
            lines.append("  Не открывать SHORT — структура нарушена, вход преждевременный.")
            lines.append("  Не открывать LONG против медвежьего сценария.")
        lines.append("")

        lines.append("КОГДА ВЕРНУТЬСЯ")
        if mode_no_trend:
            lines.append("  Пришлите новый скрин после закрытия текущей 1H свечи.")
            lines.append("  Если на 1H по-прежнему нет импульса — сценарий по этому активу пока не актуален.")
        else:
            lines.append(f"  Вернитесь когда цена закрепится {side_above} EMA50 (15m) ≈ {_fmt_price(symbol, m['ema50'])}.")
            lines.append("  До этого пересылать скрин нет смысла — условия не изменились.")

    # ════════════════════════════════════════════════════════════════════════
    # MODE B — НАБЛЮДАЕМ
    # ════════════════════════════════════════════════════════════════════════
    else:
        lines.append("СЕЙЧАС ОРДЕР НЕ СТАВИМ")
        if reason == "pullback_volume_strong":
            lines.append("  Откат к зоне слишком агрессивный — ждём ослабления давления.")
        elif f["breakout"]:
            lines.append("  Движение на 5m есть — но не все условия подтверждения выполнены.")
        else:
            lines.append("  Ждём подтверждающего импульса на 5m.")
        lines.append("")

        lines.append("ЗОНА НАБЛЮДЕНИЯ")
        lines.append(f"  EMA20 (средняя зона цены на 15m) ≈ {_fmt_price(symbol, m['ema20'])}.")
        if setup_zone:
            if reason == "pullback_volume_strong":
                lines.append("  Цена в зоне — но вход ещё не готов: откат к зоне слишком агрессивный.")
            elif f["breakout"]:
                lines.append("  Цена в зоне — но вход ещё не готов: на 5m движение есть, ждём подтверждения условий.")
            else:
                lines.append("  Цена в зоне — но вход ещё не готов: ждём подтверждающего импульса на 5m.")
        else:
            if zone_prox == "приближается к зоне":
                lines.append("  Цена приближается к зоне.")
            else:
                lines.append("  Цена пока не добралась до зоны.")
        lines.append("")

        # Pending plan — show pre-computed levels when available
        if pp.get("available"):
            lines.append("ПЛАН ПРИ ПОДТВЕРЖДЕНИИ")
            lines.append("  Уровни рассчитаны заранее. Точнее станут при появлении сигнала:")
            lines.append(f"  Зона входа:        ≈ {_fmt_price(symbol, pp['entry_zone'])}")
            lines.append(f"  Триггерная цена:   {_fmt_price(symbol, pp['trigger'])}")
            lines.append(f"  Ориентир стопа:    {_fmt_price(symbol, pp['sl'])}")
            lines.append(f"  Первая цель:       {_fmt_price(symbol, pp['tp1'])}")
            lines.append(f"  Вторая цель:       {_fmt_price(symbol, pp['tp2'])}")
            lines.append(f"  Сценарий ломается: цена уходит {side_below} {_fmt_price(symbol, pp['invalidation'])}")
            lines.append("")

        lines.append("ЧТО НУЖНО ДЛЯ ВХОДА")
        if reason == "pullback_volume_strong":
            lines.append(f"  Новый откат к EMA20 с умеренным объёмом.")
            lines.append(f"  Затем подтверждающий импульс на 5m {bk_dir} последней структуры.")
        elif f["breakout"]:
            lines.append(f"  Свеча на 5m должна закрыться {bk_dir} структуры с объёмом выше среднего.")
            lines.append("  Все условия должны выполниться одновременно.")
        else:
            lines.append(f"  Свеча на 5m должна закрыться {bk_dir} последней структуры.")
            lines.append("  Объём на 5m должен быть выше среднего.")
        lines.append("")

        lines.append("КОГДА ИДЕЯ ТЕРЯЕТ СМЫСЛ")
        if setup_zone:
            lines.append(f"  Если цена уйдёт {side_below} EMA50 (15m) ≈ {_fmt_price(symbol, m['ema50'])} — сценарий отменяем.")
        else:
            lines.append(f"  Если цена пробьёт EMA50 (15m) ≈ {_fmt_price(symbol, m['ema50'])} — сценарий отменяем.")
        lines.append("")

        lines.append("НЕ ДЕЛАТЬ")
        if dir_long:
            lines.append("  Не входить в SHORT. Ожидаемое движение цены к зоне — это подготовка к LONG, не short-идея.")
            lines.append("  Не открывать LONG до подтверждения на 5m.")
            lines.append("  Не гнаться за ценой, если она ушла от зоны.")
        else:
            lines.append("  Не входить в LONG. Ожидаемое движение цены к зоне — это подготовка к SHORT, не long-идея.")
            lines.append("  Не открывать SHORT до подтверждения на 5m.")
            lines.append("  Не гнаться за ценой, если она ушла от зоны.")
        lines.append("")

        lines.append("КОГДА ВЕРНУТЬСЯ")
        if not setup_zone:
            lines.append(f"  Пришлите скрин когда цена приблизится к ≈ {_fmt_price(symbol, m['ema20'])}.")
            lines.append("  Или после закрытия ближайшей 15m свечи.")
        elif reason == "pullback_volume_strong":
            lines.append("  Пришлите новый скрин через 1-2 закрытых 15m свечи.")
            lines.append("  Нужно убедиться, что давление на откате ослабло.")
        elif f["breakout"]:
            lines.append("  Пришлите скрин после закрытия текущей 5m свечи.")
            lines.append("  Нужно видеть подтверждение закрытия, а не только движение в моменте.")
        else:
            lines.append("  Пришлите скрин после закрытия ближайшей 5m свечи.")

    # Актуально до — next candle close for the relevant timeframe
    if mode_entry or setup_zone:
        until_str = _next_candle_close(captured_at, 5)
    elif mode_no_trend:
        until_str = _next_candle_close(captured_at, 60)
    else:
        until_str = _next_candle_close(captured_at, 15)
    until_label = "Повторный анализ после:" if (mode_entry or setup_zone) else "Актуально до:"
    lines += ["", f"  {until_label} {until_str}", "", SEP]
    return "\n".join(lines)


def _format_telegram(client_summary: str) -> str:
    """Wrap client summary in a monospace block for Telegram HTML."""
    return f"<pre>{client_summary}</pre>"


# ── Trader view ───────────────────────────────────────────────────────────────

def build_trader_view(r: dict, symbol: str = "") -> list:
    lines = []
    h = r["1h"]
    m = r["15m"]
    f = r["5m"]
    sig = r["signal"]
    reason = sig.get("reason", "")

    if h["bull"]:
        lines.append(f"• 1H тренд бычий (ADX {h['adx']:.1f}) — торгуем только лонг")
    elif h["bear"]:
        lines.append(f"• 1H тренд медвежий (ADX {h['adx']:.1f}) — торгуем только шорт")
    else:
        lines.append(f"• 1H тренда нет (ADX {h['adx']:.1f}) — рынок в боковике, ждать")

    if m["near_ema"]:
        lines.append(f"• На 15m цена у EMA20 ({_fmt_price(symbol, m['ema20'])}) — зона пулбэка есть")
    else:
        dist = abs(m["close"] - m["ema20"])
        lines.append(
            f"• На 15m цена далеко от EMA20 (gap={_fmt_metric(dist)}, "
            f"порог={_fmt_metric(m['pb_touch_threshold'])}) — сетапа нет"
        )

    if h["bull"] or h["bear"]:
        if m["structure_ok"]:
            side_word = "выше EMA50" if h["bull"] else "ниже EMA50"
            lines.append(f"• Структура 15m в порядке — цена {side_word}")
        else:
            lines.append("• Структура 15m сломана — цена на неправильной стороне EMA50")

    if m["pb_vol_weak"]:
        lines.append(f"• Объём пулбэка слабый (ratio={m['vol_ratio_pb']:.2f}) — хороший признак")
    else:
        lines.append(f"• Объём пулбэка сильный (ratio={m['vol_ratio_pb']:.2f}) — движение агрессивное")

    if f["breakout"]:
        lines.append("• На 5m есть пробой — триггерная свеча вышла за структуру")
    else:
        lines.append("• На 5m пробоя нет — ждать триггерной свечи")

    if f["vol_strong"]:
        lines.append(f"• Объём триггера сильный (×{f['vol_ratio']:.2f})")
    else:
        lines.append(f"• Объём триггера слабый (×{f['vol_ratio']:.2f}, нужно ×1.3+)")

    if sig.get("side"):
        side_str = "LONG" if sig["side"] == "buy" else "SHORT"
        lines.append(f"• Итог: бот видит сигнал {side_str} — все фильтры пройдены ✓")
    elif reason == "no_trend_1h":
        lines.append("• Итог: нет тренда. Ждать ADX ≥ 20 и расхождения DI на 1H")
    elif reason == "no_pullback_15m":
        lines.append(f"• Итог: нет сетапа. Ждать цены к EMA20(15m) ≈ {_fmt_price(symbol, m['ema20'])}")
    elif reason == "pullback_volume_strong":
        lines.append("• Итог: пулбэк слишком агрессивный. Ждать ослабления объёма")
    elif reason == "no_breakout_5m":
        lines.append("• Итог: сетап есть — ждать пробойной свечи на 5m")
    elif reason == "breakout_volume_weak":
        lines.append("• Итог: пробой есть, объём слабый — не входить")
    elif reason == "di_not_confirmed_5m":
        lines.append("• Итог: пробой и объём есть, DI против — не входить")

    return lines


def build_action_view(r: dict, symbol: str = "") -> list:
    lines = []
    act = r["action"]
    sig = r["signal"]

    if act["valid"]:
        side_str = "LONG" if sig["side"] == "buy" else "SHORT"
        lines.append(f"  Направление:  {side_str}  ({act['type']})")
        lines.append(f"  Entry:        {_fmt_price(symbol, act['entry'])}")
        lines.append(f"  SL:           {_fmt_price(symbol, act['sl'])}  (dist={_fmt_metric(act['sl_dist'])})")
        lines.append(f"  TP1:          {_fmt_price(symbol, act['tp1'])}  (R×{act['r_multiple']})")
        lines.append(f"  TP2:          {_fmt_price(symbol, act['tp2'])}  (R×{act['r_multiple'] * 1.5:.1f})")
        lines.append(f"  Invalidation: позиция инвалидна если цена пересекла SL")
    else:
        lines.append(f"  Сигнала нет. {act.get('hint', '')}")

    return lines


# ── Report formatting ─────────────────────────────────────────────────────────

def format_report(symbol: str, captured_at: str, r: dict) -> str:
    h = r["1h"]
    m = r["15m"]
    f = r["5m"]
    sig = r["signal"]
    reason = sig.get("reason", "")
    stage  = _stopped_stage(reason)
    side   = sig.get("side")
    trader = build_trader_view(r, symbol)
    action = build_action_view(r, symbol)

    SEP  = "=" * 62
    THIN = "─" * 62
    trend_str = "BULLISH" if h["bull"] else "BEARISH" if h["bear"] else "NONE (flat)"

    lines = [
        SEP,
        f"  CHART ANALYSIS: {symbol} @ {captured_at}",
        SEP,
        "",
        f"── 1H TREND {THIN[11:]}",
        f"  EMA20:  {_fmt_price(symbol, h['ema20']):<12} EMA50:  {_fmt_price(symbol, h['ema50'])}",
        f"  ADX:    {h['adx']:<12.1f} +DI: {h['plus_di']:.1f}   -DI: {h['minus_di']:.1f}",
        f"  Trend:  {trend_str}",
        "",
        f"── 15m SETUP {THIN[12:]}",
        f"  Close:  {_fmt_price(symbol, m['close']):<12} EMA20: {_fmt_price(symbol, m['ema20'])}   EMA50: {_fmt_price(symbol, m['ema50'])}",
        f"  ATR:    {m['atr']:.4f}",
        f"  Near EMA20:   gap={_fmt_metric(abs(m['close'] - m['ema20']))}  "
        f"threshold={_fmt_metric(m['pb_touch_threshold'])}  {ok(m['near_ema'])}",
        f"  Structure:    {ok(m['structure_ok'])}",
        f"  Pullback vol: recent={m['vol_recent']:.0f}  prior={m['vol_prior']:.0f}  "
        f"ratio={m['vol_ratio_pb']:.2f}  weak={ok(m['pb_vol_weak'])}",
        "",
        f"── 5m TRIGGER {THIN[13:]}",
        f"  Trigger close: {f['trigger_close']:.4f}",
        f"  Breakout:      {ok(f['breakout'])}",
        f"  Vol ratio:     trigger={f['trigger_vol']:.0f}  SMA={f['vol_sma']:.0f}  "
        f"×{f['vol_ratio']:.2f}  strong={ok(f['vol_strong'])}",
        f"  DI confirm:    +DI={f['plus_di']:.1f}  -DI={f['minus_di']:.1f}  {ok(f['di_confirm'])}",
        "",
        f"── BOT DECISION {THIN[15:]}",
        f"  Result:       {'TRADE ' + side.upper() if side else 'NO TRADE'}",
        f"  Reason:       {reason}",
        f"  Stopped at:   {stage}",
        "",
        f"── TRADER VIEW {THIN[14:]}",
    ]
    for t in trader:
        lines.append(f"  {t}")

    lines += [
        "",
        f"── ACTION VIEW {THIN[14:]}",
    ]
    lines += action
    lines += ["", SEP]
    return "\n".join(lines)


# ── Annotated PNG ─────────────────────────────────────────────────────────────

def generate_annotated_png(image_path: str, summary_text: str, out_path: str) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("WARNING: Pillow not installed, skipping annotated PNG")
        return

    img = Image.open(image_path).convert("RGB")
    iw, ih = img.size

    # Build panel lines (right sidebar, 380px wide)
    PANEL_W = 400
    PADDING = 14
    FONT_SIZE = 14

    try:
        font = ImageFont.truetype("arial.ttf", FONT_SIZE)
        font_bold = ImageFont.truetype("arialbd.ttf", FONT_SIZE + 1)
    except Exception:
        font = ImageFont.load_default()
        font_bold = font

    # Wrap long lines to fit panel
    max_chars = (PANEL_W - PADDING * 2) // 8
    wrapped = []
    for line in summary_text.split("\n"):
        if len(line) <= max_chars:
            wrapped.append(line)
        else:
            wrapped.extend(textwrap.wrap(line, width=max_chars) or [""])

    line_h = FONT_SIZE + 4
    panel_h = max(ih, len(wrapped) * line_h + PADDING * 2)

    # New canvas: original image + right panel
    canvas = Image.new("RGB", (iw + PANEL_W, panel_h), (15, 15, 20))
    canvas.paste(img, (0, (panel_h - ih) // 2))

    draw = ImageDraw.Draw(canvas)

    # Panel background with subtle border
    draw.rectangle([(iw, 0), (iw + PANEL_W, panel_h)], fill=(18, 20, 28))
    draw.line([(iw, 0), (iw, panel_h)], fill=(60, 80, 120), width=2)

    # Color map for client summary lines
    _SECTION_HEADERS = {
        "СЕЙЧАС НА РЫНКЕ",
        "ПЛАН ВХОДА",
        "ГДЕ ИДЕЯ ЛОМАЕТСЯ",
        "ГДЕ ЗАБРАТЬ ПРИБЫЛЬ",
        "СЕЙЧАС ОРДЕР НЕ СТАВИМ",
        "ЗОНА НАБЛЮДЕНИЯ",
        "ПЛАН ПРИ ПОДТВЕРЖДЕНИИ",
        "ЧТО НУЖНО ДЛЯ ВХОДА",
        "ЧТО НУЖНО ДЛЯ ПОЯВЛЕНИЯ СЦЕНАРИЯ",
        "КОГДА ИДЕЯ ТЕРЯЕТ СМЫСЛ",
        "НЕ ДЕЛАТЬ",
        "КОГДА ВЕРНУТЬСЯ",
    }

    def line_color(text: str) -> tuple:
        stripped = text.strip()
        if "═" in text:
            return (100, 160, 255)
        # Status lines — colored by mode
        if stripped.startswith("Статус:"):
            if "ГОТОВ ВХОД" in stripped:  return (80, 210, 120)   # green
            if "НАБЛЮДАЕМ" in stripped:   return (220, 180, 60)   # amber
            return (150, 150, 155)                                  # gray (ВНЕ РЫНКА)
        if stripped.startswith("Направление:"):
            if "только LONG" in stripped:   return (80, 210, 120)
            if "только SHORT" in stripped:  return (210, 80,  80)
            return (150, 150, 155)
        if stripped in _SECTION_HEADERS:
            return (80, 140, 210)
        if stripped.startswith("Вход можно"):
            return (255, 200, 80)
        if stripped.startswith(("Первая цель:", "Вторая цель:", "Зона входа:", "Триггерная цена:")):
            return (255, 200, 80)
        if stripped.startswith(("Защитный выход:", "Ориентир стопа:", "Сценарий ломается:")):
            return (220, 130, 80)
        if stripped.startswith("Волатильность:"):
            if "высокая" in stripped or "расширение" in stripped:
                return (220, 130, 80)
            if "низкая" in stripped or "сжатие" in stripped:
                return (100, 160, 255)
        if stripped.startswith("("):
            return (145, 150, 165)
        return (190, 195, 210)

    y = PADDING
    for line in wrapped:
        color = line_color(line)
        draw.text((iw + PADDING, y), line, font=font, fill=color)
        y += line_h
        if y > panel_h - PADDING:
            break

    canvas.save(out_path, quality=92)
    print(f"Saved: {out_path}")


def generate_chart_png(
    raw_15m: list,
    result: dict,
    symbol: str,
    captured_at: str,
    out_path: str,
    llm_levels: dict | None = None,
    entry_signal: str = None,
    direction: str = None,
    trade_style: str = None,
) -> None:
    """Generate candlestick chart from OKX data with EMA + price level overlays."""
    try:
        import io
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as e:
        print(f"WARNING: Missing library for chart: {e}")
        return

    # ── Candle slices (chronological, last 60 bars) ────────────────────────
    candles = list(reversed(raw_15m))
    n_show  = min(60, len(candles))
    candles = candles[-n_show:]

    ts_list = [int(c[0])   for c in candles]
    opens   = [float(c[1]) for c in candles]
    highs_c = [float(c[2]) for c in candles]
    lows_c  = [float(c[3]) for c in candles]
    closes  = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    # EMA + BB + SuperTrend on full history, then slice last n_show
    closes_full = np.array([float(c[4]) for c in list(reversed(raw_15m))])
    highs_full  = np.array([float(c[2]) for c in list(reversed(raw_15m))])
    lows_full   = np.array([float(c[3]) for c in list(reversed(raw_15m))])
    ema20_slice = calc_ema(closes_full, 20)[-n_show:]
    ema50_slice = calc_ema(closes_full, 50)[-n_show:]

    # Bollinger Bands series
    bb_upper_arr = np.zeros(len(closes_full))
    bb_lower_arr = np.zeros(len(closes_full))
    for i in range(20, len(closes_full)):
        mid = np.mean(closes_full[i-20:i])
        std = np.std(closes_full[i-20:i], ddof=0)
        bb_upper_arr[i] = mid + 2 * std
        bb_lower_arr[i] = mid - 2 * std
    bb_upper_slice = bb_upper_arr[-n_show:]
    bb_lower_slice = bb_lower_arr[-n_show:]

    # SuperTrend series
    st_vals = np.zeros(len(closes_full))
    st_dirs = np.zeros(len(closes_full))  # 1=up, -1=down
    _atr_st = np.zeros(len(closes_full))
    for i in range(1, len(closes_full)):
        tr = max(highs_full[i]-lows_full[i], abs(highs_full[i]-closes_full[i-1]), abs(lows_full[i]-closes_full[i-1]))
        _atr_st[i] = (_atr_st[i-1] * 13 + tr) / 14 if i >= 14 else tr
    _upper_st = np.zeros(len(closes_full))
    _lower_st = np.zeros(len(closes_full))
    for i in range(14, len(closes_full)):
        mid = (highs_full[i] + lows_full[i]) / 2
        bu  = mid + 3 * _atr_st[i]
        bl  = mid - 3 * _atr_st[i]
        _upper_st[i] = bu if bu < _upper_st[i-1] or closes_full[i-1] > _upper_st[i-1] else _upper_st[i-1]
        _lower_st[i] = bl if bl > _lower_st[i-1] or closes_full[i-1] < _lower_st[i-1] else _lower_st[i-1]
        if st_vals[i-1] == _upper_st[i-1]:
            st_vals[i] = _lower_st[i] if closes_full[i] > _upper_st[i] else _upper_st[i]
        else:
            st_vals[i] = _upper_st[i] if closes_full[i] < _lower_st[i] else _lower_st[i]
        st_dirs[i] = 1 if closes_full[i] > st_vals[i] else -1
    st_vals_slice = st_vals[-n_show:]
    st_dirs_slice = st_dirs[-n_show:]

    COL_EMA20 = "#2196F3"
    COL_EMA50 = "#FF9800"

    # ── Figure: price chart (top) + volume (bottom) ────────────────────────
    BG = "#0b0d14"
    matplotlib.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8})
    fig, (ax, ax_vol) = plt.subplots(
        2, 1, figsize=(10, 6.2), facecolor=BG,
        gridspec_kw={"height_ratios": [3.5, 1], "hspace": 0.06},
        sharex=True,
    )
    ax.set_facecolor(BG)
    ax_vol.set_facecolor(BG)

    # Candlesticks
    for i, (o, h, l, c) in enumerate(zip(opens, highs_c, lows_c, closes)):
        col = "#26a69a" if c >= o else "#ef5350"
        ax.plot([i, i], [l, h], color=col, linewidth=0.7, zorder=2)
        bh = max(abs(c - o), (h - l) * 0.015)
        ax.add_patch(mpatches.Rectangle(
            (i - 0.35, min(c, o)), 0.7, bh,
            facecolor=col, edgecolor=col, linewidth=0, zorder=3,
        ))

    # EMA lines + inline price labels at right edge
    R_MARGIN = 9  # extra x-units for labels
    for arr, col, lbl in [(ema20_slice, COL_EMA20, "EMA20"), (ema50_slice, COL_EMA50, "EMA50")]:
        pts = [(i, v) for i, v in enumerate(arr) if v > 0]
        if not pts:
            continue
        xi, yi = zip(*pts)
        ax.plot(xi, yi, color=col, linewidth=1.2, zorder=4)
        last_x, last_y = pts[-1]
        ax.text(
            last_x + 0.8, last_y,
            f"{lbl}: {_fmt_price(symbol, last_y)}",
            color=col, fontsize=6.5, va="center", ha="left", zorder=7,
            bbox=dict(boxstyle="round,pad=0.15", facecolor=BG, edgecolor="none", alpha=0.85),
        )

    # Bollinger Bands
    bb_u_pts = [(i, v) for i, v in enumerate(bb_upper_slice) if v > 0]
    bb_l_pts = [(i, v) for i, v in enumerate(bb_lower_slice) if v > 0]
    if bb_u_pts and bb_l_pts:
        ax.plot([p[0] for p in bb_u_pts], [p[1] for p in bb_u_pts],
                color="#7E57C2", linewidth=0.7, linestyle="--", alpha=0.6, zorder=3)
        ax.plot([p[0] for p in bb_l_pts], [p[1] for p in bb_l_pts],
                color="#7E57C2", linewidth=0.7, linestyle="--", alpha=0.6, zorder=3)
        ax.text(bb_u_pts[-1][0] + 0.5, bb_u_pts[-1][1], f" BB↑{_fmt_price(symbol, bb_u_pts[-1][1])}",
                color="#7E57C2", fontsize=5.5, va="center", ha="left", zorder=7)
        ax.text(bb_l_pts[-1][0] + 0.5, bb_l_pts[-1][1], f" BB↓{_fmt_price(symbol, bb_l_pts[-1][1])}",
                color="#7E57C2", fontsize=5.5, va="center", ha="left", zorder=7)

    # SuperTrend — green when up, red when down
    for i in range(1, n_show):
        if st_vals_slice[i] == 0:
            continue
        col_st = "#26a69a" if st_dirs_slice[i] == 1 else "#ef5350"
        ax.plot([i-1, i], [st_vals_slice[i-1] if st_vals_slice[i-1] > 0 else st_vals_slice[i],
                           st_vals_slice[i]], color=col_st, linewidth=1.5, alpha=0.8, zorder=4)
    if st_vals_slice[-1] > 0:
        col_st_last = "#26a69a" if st_dirs_slice[-1] == 1 else "#ef5350"
        ax.text(n_show - 1 + 0.5, st_vals_slice[-1], f" ST{_fmt_price(symbol, st_vals_slice[-1])}",
                color=col_st_last, fontsize=5.5, va="center", ha="left", zorder=7)

    # ── Price levels ───────────────────────────────────────────────────────
    pp  = result.get("pending_plan", {})
    act = result.get("action", {})

    level_keys = [
        ("entry_zone", "Зона входа", "#4CAF50", "--"),
        ("trigger",    "Триггер",    "#00E676", "-"),
        ("sl",         "SL",         "#F44336", "--"),
        ("tp1",        "TP1",        "#FFD700", "--"),
        ("tp2",        "TP2",        "#FFA500", ":"),
    ]
    # Prefer old-strategy levels, fall back to llm_context levels
    src = pp if pp.get("available") else (act if act.get("valid") else (llm_levels or {}))
    for key, label, col, ls in level_keys:
        if not src.get(key):
            continue
        price = float(src[key])
        ax.axhline(y=price, color=col, linestyle=ls, linewidth=0.9, alpha=0.85, zorder=5)
        ax.text(n_show - 1, price, f"  {label}: {_fmt_price(symbol, price)}",
                color=col, fontsize=7, va="bottom", ha="right", zorder=6)

    # ── Entry price marker with explicit LONG/SHORT label ─────────────────
    _entry_p = (llm_levels or {}).get("entry_price")
    if _entry_p and direction and entry_signal in ("ENTRY", "WAIT"):
        _dir_text = "ВХОД LONG ▲" if direction == "buy" else "ВХОД SHORT ▼"
        _ecol = "#00E676" if direction == "buy" else "#FF5252"
        ax.axhline(y=_entry_p, color=_ecol, linestyle="-", linewidth=1.2, alpha=0.9, zorder=6)
        ax.text(
            n_show // 2, _entry_p,
            f"  {_dir_text}: {_fmt_price(symbol, _entry_p)}",
            color=_ecol, fontsize=8, fontweight="bold", va="bottom", ha="center", zorder=8,
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#0b0d14", edgecolor=_ecol, alpha=0.9),
        )

    # ── Estimated levels (range mode, no confirmed signal) ────────────────
    if not src:
        h1_res  = result.get("1h", {})
        m15_res = result.get("15m", {})
        if not (h1_res.get("bull") or h1_res.get("bear")):  # range mode only
            close_p = m15_res.get("close")
            sh_list = m15_res.get("swing_highs") or []
            sl_list = m15_res.get("swing_lows")  or []
            if close_p and sh_list and sl_list:
                sh_p = float(sh_list[-1])
                sl_p = float(sl_list[-1])
                rng  = sh_p - sl_p
                cp   = float(close_p)
                # Only draw if range is meaningful (≥0.5% of price) and price is inside range
                if rng > 0 and (rng / cp) >= 0.005:
                    pct = (cp - sl_p) / rng
                    if 0.0 <= pct < 0.25:
                        est_side, tp_est, sl_est = "ЛОНГ", sh_p, sl_p - rng * 0.05
                    elif 0.75 < pct <= 1.0:
                        est_side, tp_est, sl_est = "ШОРТ", sl_p, sh_p + rng * 0.05
                    else:
                        est_side = None
                    if est_side:
                        for price, lbl, col in [
                            (float(close_p), f"расч. Вход ({est_side})", "#90CAF9"),
                            (tp_est,         "расч. TP",                 "#FFD700"),
                            (sl_est,         "расч. SL",                 "#F44336"),
                        ]:
                            ax.axhline(y=price, color=col, linestyle=":", linewidth=0.85, alpha=0.55, zorder=5)
                            ax.text(n_show - 1, price, f"  {lbl}: {_fmt_price(symbol, price)}",
                                    color=col, fontsize=6.5, va="bottom", ha="right", zorder=6,
                                    bbox=dict(boxstyle="round,pad=0.1", facecolor=BG, edgecolor="none", alpha=0.75))

    # ── Swing High / Swing Low (only if level is within ±8% of current price)
    _cur = float(result.get("15m", {}).get("close") or closes[-1])
    m15 = result.get("15m", {})
    swing_highs = m15.get("swing_highs") or []
    swing_lows  = m15.get("swing_lows")  or []
    if swing_highs:
        sh = float(swing_highs[-1])
        if abs(sh - _cur) / _cur <= 0.08:
            ax.axhline(y=sh, color="#B39DDB", linestyle=":", linewidth=0.8, alpha=0.75, zorder=4)
            ax.text(0, sh, f"  Swing H: {_fmt_price(symbol, sh)}",
                    color="#B39DDB", fontsize=6, va="bottom", ha="left", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.1", facecolor=BG, edgecolor="none", alpha=0.8))
    if swing_lows:
        sl_sw = float(swing_lows[-1])
        if abs(sl_sw - _cur) / _cur <= 0.08:
            ax.axhline(y=sl_sw, color="#80CBC4", linestyle=":", linewidth=0.8, alpha=0.75, zorder=4)
            ax.text(0, sl_sw, f"  Swing L: {_fmt_price(symbol, sl_sw)}",
                    color="#80CBC4", fontsize=6, va="top", ha="left", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.1", facecolor=BG, edgecolor="none", alpha=0.8))

    # ── Volume bars ────────────────────────────────────────────────────────
    vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(closes, opens)]
    ax_vol.bar(range(n_show), volumes, color=vol_colors, alpha=0.8, width=0.7, zorder=2)
    if len(volumes) >= 20:
        vol_sma = np.convolve(volumes, np.ones(20) / 20, mode="valid")
        ax_vol.plot(range(19, n_show), vol_sma, color="#888", linewidth=0.9, zorder=3)
    ax_vol.set_facecolor(BG)
    ax_vol.tick_params(colors="#555", labelsize=6, left=False, right=True, labelleft=False, labelright=True)
    ax_vol.yaxis.tick_right()
    ax_vol.yaxis.set_label_position("right")
    ax_vol.set_ylabel("Vol", color="#555", fontsize=6, rotation=0, labelpad=28)
    for spine in ax_vol.spines.values():
        spine.set_edgecolor("#252838")
    ax_vol.grid(axis="y", color="#1a1d2e", linewidth=0.4, zorder=0)
    ax_vol.set_yticks([max(volumes) * 0.5, max(volumes)])
    ax_vol.set_yticklabels(
        [f"{max(volumes)*0.5/1e3:.0f}K", f"{max(volumes)/1e3:.0f}K"],
        color="#555", fontsize=5.5,
    )

    # ── Axes ───────────────────────────────────────────────────────────────
    # Clip y-axis to 2nd/98th percentile of visible candles — prevents spike distortion
    all_prices = lows_c + highs_c
    y_min = float(np.percentile(all_prices, 2))
    y_max = float(np.percentile(all_prices, 98))
    margin = (y_max - y_min) * 0.06
    ax.set_xlim(-0.8, n_show + R_MARGIN)
    ax.set_ylim(y_min - margin, y_max + margin)
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.tick_params(colors="#666", labelsize=7)
    for spine in ax.spines.values():
        spine.set_edgecolor("#252838")
    ax.grid(axis="y", color="#1a1d2e", linewidth=0.5, zorder=0)
    ax.grid(axis="x", color="#161828", linewidth=0.3, zorder=0)

    step = max(1, n_show // 8)
    ticks = list(range(0, n_show, step))
    ax_vol.set_xticks(ticks)
    ax_vol.set_xticklabels(
        [datetime.fromtimestamp(ts_list[i] / 1000, tz=timezone.utc).strftime("%H:%M") for i in ticks],
        color="#555", fontsize=6,
    )

    # Build title with direction/signal badge if available
    _title_base = f"{symbol} · 15m · {captured_at[:16].replace('T', ' ')} UTC"
    ax.set_title(_title_base, color="#7788aa", fontsize=8, loc="left", pad=5)
    if entry_signal and entry_signal != "NO_TRADE" and direction:
        _dir_label = "▲ LONG" if direction == "buy" else "▼ SHORT"
        _style_label = f" {trade_style}" if trade_style and trade_style != "NO_TRADE" else ""
        if entry_signal == "ENTRY":
            _badge_col = "#00E676"
        elif entry_signal == "WAIT":
            _badge_col = "#FFD700"
        else:
            _badge_col = "#888888"
        ax.text(
            0.99, 0.97, f"{_dir_label}{_style_label}  [{entry_signal}]",
            transform=ax.transAxes, color=_badge_col, fontsize=9, fontweight="bold",
            va="top", ha="right", zorder=10,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#0b0d14", edgecolor=_badge_col, alpha=0.9),
        )

    plt.tight_layout(pad=0.4)

    # ── Render chart to PIL Image ──────────────────────────────────────────
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    chart_img = Image.open(buf).convert("RGB")

    chart_img.save(out_path, quality=92)
    print(f"Saved: {out_path}")


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(symbol: str, captured_at_iso: str, limit: int, image_path: str = None, send_telegram: bool = False, output_dir: Path = None) -> None:
    api_key    = os.getenv("OKX_API_KEY", "")
    secret_key = os.getenv("OKX_SECRET_KEY", "")
    passphrase = os.getenv("OKX_PASSPHRASE", "")
    is_demo    = os.getenv("OKX_IS_DEMO", "1") == "1"

    client          = OKXClient(api_key, secret_key, passphrase, is_demo)
    params          = load_strategy_params()
    min_sl_percent  = load_symbol_min_sl_percent(symbol)

    captured_ms = ts_to_ms(captured_at_iso)
    after_ts    = captured_ms + 1

    print(f"Fetching candles for {symbol} ending at {captured_at_iso} ...")
    raw_4h, raw_1h, raw_15m, raw_5m, _funding, _oi, _oi_hist = await asyncio.gather(
        client.get_history_candles(symbol, "4H",  after=after_ts, limit=60),
        client.get_history_candles(symbol, "1H",  after=after_ts, limit=limit),
        client.get_history_candles(symbol, "15m", after=after_ts, limit=limit),
        client.get_history_candles(symbol, "5m",  after=after_ts, limit=limit),
        client.get_funding_rate(symbol),
        client.get_open_interest(symbol),
        client.get_oi_history(symbol, period="1H", limit=5),
    )
    await client.close()

    if not raw_1h or not raw_15m or not raw_5m:
        print("ERROR: No candle data returned. Check symbol and captured-at timestamp.")
        return

    c1h  = confirm_label(raw_1h)
    c15m = confirm_label(raw_15m)
    c5m  = confirm_label(raw_5m)
    print(f"Latest bar status:  1H={c1h}  15m={c15m}  5m={c5m}\n")

    _pair_adx = params.get("adx_thresholds", {}).get(symbol)
    if _pair_adx is not None:
        params = {**params, "adx_threshold_1h": float(_pair_adx)}

    result         = analyze(raw_1h, raw_15m, raw_5m, params, min_sl_percent, raw_4h=raw_4h)
    report_text    = format_report(symbol, captured_at_iso, result)
    client_summary = build_client_summary(symbol, captured_at_iso, result)

    print(report_text)
    print("\n── CLIENT SUMMARY " + "─" * 44)
    print(client_summary)

    # Save outputs — one folder per run
    ts_label = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    if output_dir is not None:
        run_dir = Path(output_dir)  # caller already provides the exact folder
    else:
        run_dir = Path(__file__).parent / "analysis_output" / ts_label
    run_dir.mkdir(parents=True, exist_ok=True)

    report_path = run_dir / f"{symbol}_report.md"
    snap_path   = run_dir / f"{symbol}_snapshot.json"
    png_path    = run_dir / f"{symbol}_annotated.png"

    report_path.write_text(report_text + "\n", encoding="utf-8")

    # Expiry time: how long this analysis is valid
    _act = result["action"]
    _pp  = result["pending_plan"]
    _r1h = result["1h"]
    if _act.get("valid") or _pp.get("available"):
        _tf_exp = 5
    elif _r1h.get("bull") or _r1h.get("bear"):
        _tf_exp = 15
    else:
        _tf_exp = 60
    expiry_time = _next_candle_close(captured_at_iso, _tf_exp)

    # Pre-compute LLM context — Python decides, LLM formats
    _h4  = result.get("4h", {})
    _h1  = result["1h"]
    _h15 = result["15m"]
    _h5  = result["5m"]
    _act = result["action"]

    # Bias: EMA-only (matches backtest logic — no ADX requirement for direction)
    _ema20_4h = float(_h4.get("ema20") or 0)
    _ema50_4h = float(_h4.get("ema50") or 0)
    _ema20_1h = float(_h1.get("ema20") or 0)
    _ema50_1h = float(_h1.get("ema50") or 0)
    _bias_4h = "UP" if _ema20_4h > _ema50_4h > 0 else ("DOWN" if _ema20_4h < _ema50_4h else "NEUTRAL")
    _bias_1h = "UP" if _ema20_1h > _ema50_1h > 0 else ("DOWN" if _ema20_1h < _ema50_1h else "NEUTRAL")
    _adx_1h      = float(_h1.get("adx") or 0)
    _adx_4h      = float(_h4.get("adx") or 0)
    _bb_width_1h = float(_h1.get("bb_width_pct") or 99.0)
    # Per-pair ADX threshold — slow pairs (BTC/ETH) need lower threshold
    _per_pair_adx = params.get("adx_thresholds", {})
    adx_thresh_4h = float(_per_pair_adx.get(symbol, params.get("adx_threshold_4h", params.get("adx_threshold_1h", 25))))
    scalp_enabled  = bool(params.get("scalp_enabled", True))
    scalp_symbols  = params.get("scalp_symbols", ["XRP-USDT", "ETH-USDT"])
    scalp_vol_min  = float(params.get("scalp_vol_ratio", 2.0))
    _atr_15m = float(_h15.get("atr") or 0)
    _close   = float(_h15.get("close") or 0)
    _vol_ratio = min(float(_h15.get("vol_ratio_pb") or 0), 10.0)
    _rsi_15m   = float(_h15.get("rsi") or 50)

    # Extra indicators already calculated — use for PULLBACK filter
    _plus_di_1h     = float(_h1.get("plus_di") or 0)
    _minus_di_1h    = float(_h1.get("minus_di") or 0)
    _supertrend_dir = str(_h15.get("supertrend_dir") or "")   # "up" / "down"
    _ce_long        = float(_h15.get("ce_long") or 0)
    _ce_short       = float(_h15.get("ce_short") or 0)

    # ATR 1H + ADX rising check
    if raw_1h and len(raw_1h) >= 14:
        _highs_1h, _lows_1h, _closes_1h = parse_candles(raw_1h)
        _atr_1h = float(calc_atr(_highs_1h, _lows_1h, _closes_1h, period=14))
        # ADX rising: last closed bar vs previous bar (bar[-2] vs bar[-3])
        _adx_1h_prev3, _, _ = calc_adx(_highs_1h, _lows_1h, _closes_1h, period=14, bar_index=-3)
        _adx_1h_rising = _adx_1h > float(_adx_1h_prev3)
    else:
        _atr_1h = _atr_15m * 4  # fallback: rough 1H estimate
        _adx_1h_rising = False

    # Vol ratio: impulse version — last 3 bars vs prior 15 on 15m
    if raw_15m and len(raw_15m) >= 20:
        _vols_imp  = [float(c[5]) for c in list(reversed(raw_15m))]
        _prior_imp = float(np.mean(_vols_imp[5:20]))
        _vol_ratio_sig = float(np.mean(_vols_imp[:3])) / max(_prior_imp, 1e-9)
    else:
        _vol_ratio_sig = 1.0

    # OI delta: (current - previous) / previous from last 2 bars
    _oi_delta = 0.0
    if _oi_hist and len(_oi_hist) >= 2:
        def _parse_oi_entry(e):
            if isinstance(e, dict):            return float(e.get("oi", 0) or e.get("oiCcy", 0))
            if isinstance(e, (list, tuple)) and len(e) >= 2: return float(e[1])
            return 0.0
        _oic = _parse_oi_entry(_oi_hist[0])
        _oip = _parse_oi_entry(_oi_hist[1])
        if _oip > 0:
            _oi_delta = (_oic - _oip) / _oip

    # VWAP and daily High/Low from 15m candles (current UTC day only)
    _captured_dt = datetime.fromisoformat(captured_at_iso.replace("Z", "+00:00"))
    _day_start_ms = int(datetime(_captured_dt.year, _captured_dt.month, _captured_dt.day,
                                  tzinfo=timezone.utc).timestamp() * 1000)
    _day_candles = [c for c in raw_15m if int(c[0]) >= _day_start_ms]
    if _day_candles:
        _dc_closes = [float(c[4]) for c in _day_candles]
        _dc_vols   = [float(c[5]) for c in _day_candles]
        _dc_highs  = [float(c[2]) for c in _day_candles]
        _dc_lows   = [float(c[3]) for c in _day_candles]
        _vol_sum = sum(_dc_vols)
        _vwap     = round(sum(c * v for c, v in zip(_dc_closes, _dc_vols)) / _vol_sum, 4) if _vol_sum > 0 else None
        _day_high = round(max(_dc_highs), 4)
        _day_low  = round(min(_dc_lows),  4)
    else:
        _vwap = _day_high = _day_low = None

    # Day position: 0.0 = day_low, 1.0 = day_high
    if _day_high and _day_low and _day_high != _day_low and _close:
        _day_position = round((_close - _day_low) / (_day_high - _day_low), 3)
    else:
        _day_position = None

    # Night session: 01:00–06:59 UTC — low liquidity
    _signal_hour = _captured_dt.hour
    _is_night    = 1 <= _signal_hour < 7

    # Dynamic TP multiplier: tighter in low-volatility days, wider in high-volatility days
    if _day_high and _day_low and _day_low > 0:
        _daily_range_pct = (_day_high - _day_low) / _day_low * 100
    else:
        _daily_range_pct = 0.0
    if _daily_range_pct >= 4.0:
        _tp1_mult = 1.0   # high volatility day — full 1:1
    elif _daily_range_pct >= 2.0:
        _tp1_mult = 0.8   # medium — 0.8:1
    else:
        _tp1_mult = 0.6   # low volatility — tight TP, hits more often

    # ── FAST / SWING Signal Engine (backtest-validated, March 2026) ─────────────
    # Per-pair specialization from 2×14d walk-forward test
    _PAIR_PARAMS = {
        "BTC-USDT": {"fast_vol": 1.6, "fast_adx": 18, "fast_sl_k": 1.2,
                     "swing_vol": 1.3, "swing_adx": 18, "swing_sl_k": 1.6,
                     "late_range": 4.0, "allowed_modes": ["SWING"]},
        "ETH-USDT": {"fast_vol": 1.8, "fast_adx": 18, "fast_sl_k": 1.3,
                     "swing_vol": 1.5, "swing_adx": 18, "swing_sl_k": 1.6,
                     "late_range": 7.0, "allowed_modes": ["FAST"]},
        "SOL-USDT": {"fast_vol": 2.2, "fast_adx": 20, "fast_sl_k": 1.6,
                     "swing_vol": 1.8, "swing_adx": 20, "swing_sl_k": 1.9,
                     "late_range": 10.0, "allowed_modes": ["FAST"]},
        "XRP-USDT": {"fast_vol": 1.8, "fast_adx": 18, "fast_sl_k": 1.4,
                     "swing_vol": 1.4, "swing_adx": 18, "swing_sl_k": 1.8,
                     "late_range": 7.0, "allowed_modes": ["SWING"]},
        "ADA-USDT": {"fast_vol": 1.8, "fast_adx": 20, "fast_sl_k": 1.4,
                     "swing_vol": 1.4, "swing_adx": 18, "swing_sl_k": 1.8,
                     "late_range": 7.0, "allowed_modes": ["FAST", "SWING"]},
    }
    _pp = _PAIR_PARAMS.get(symbol, {
        "fast_vol": 2.0, "fast_adx": 20, "fast_sl_k": 1.4,
        "swing_vol": 1.5, "swing_adx": 20, "swing_sl_k": 1.8,
        "late_range": 7.0, "allowed_modes": ["FAST", "SWING"],
    })

    # BB expansion on 15m (>1.5% width = trending, not sideways)
    _bb_expanding = float(_h15.get("bb_width_pct") or 0) > 1.5

    # Trade style: FAST first, SWING fallback
    _trade_style = "NO_TRADE"
    if (_adx_1h >= _pp["fast_adx"] and _adx_1h_rising
            and _vol_ratio_sig >= _pp["fast_vol"] and _bb_expanding
            and "FAST" in _pp["allowed_modes"]):
        _trade_style = "FAST"
    if _trade_style == "NO_TRADE":
        if (_adx_1h >= _pp["swing_adx"] and _adx_1h_rising
                and _vol_ratio_sig >= _pp["swing_vol"] and _bb_expanding
                and _bias_1h != "NEUTRAL"
                and "SWING" in _pp["allowed_modes"]):
            _trade_style = "SWING"

    # Night filter (01-07 UTC)
    if _is_night:
        _trade_style = "NO_TRADE"

    # Late-move veto: daily range exhausted and price at extreme
    if _daily_range_pct > _pp["late_range"] and _day_position is not None and _day_position > 0.90:
        _trade_style = "NO_TRADE"

    # Direction from 1H EMA bias
    if _bias_1h == "UP":
        _side = "buy"
    elif _bias_1h == "DOWN":
        _side = "sell"
    else:
        _trade_style = "NO_TRADE"
        _side = None

    # 4H veto: strong opposing trend (ADX > 30 and 4H bias conflicts with 1H)
    _4h_veto = float(_adx_4h) > 30 and _bias_4h != "NEUTRAL" and _bias_4h != _bias_1h
    if _4h_veto:
        _trade_style = "NO_TRADE"

    # VWAP filter: price must be on trend side of VWAP
    _vwap_ok = True
    if _vwap and _close and _side:
        if _side == "buy"  and _close < _vwap: _vwap_ok = False
        if _side == "sell" and _close > _vwap: _vwap_ok = False

    # Side-aware funding filter (0.05% threshold)
    _funding_val = _funding if _funding is not None else 0.0
    _FUND_THRESH = 0.0005
    _funding_block = ((_side == "buy"  and _funding_val >  _FUND_THRESH) or
                      (_side == "sell" and _funding_val < -_FUND_THRESH))
    _funding_warn  = not _funding_block and abs(_funding_val) > _FUND_THRESH * 0.5

    # OI weak: positions closing into the move = weaker signal
    _oi_weak = _oi_delta < -0.03

    # SL / TP
    _sl_p = _tp1_p = _tp2_p = None
    _sl_dist = 0.0
    _swing_highs = _h15.get("swing_highs", [])
    _swing_lows  = _h15.get("swing_lows",  [])

    if _trade_style == "FAST" and _side and _close:
        _sl_dist = max(_pp["fast_sl_k"] * _atr_15m, _close * 0.004)
        if _side == "buy":
            _sl_p  = round(_close - _sl_dist, 4)
            _tp1_p = round(_close + _sl_dist * 0.8, 4)
            _tp2_p = round(_close + _sl_dist * 1.5, 4)
        else:
            _sl_p  = round(_close + _sl_dist, 4)
            _tp1_p = round(_close - _sl_dist * 0.8, 4)
            _tp2_p = round(_close - _sl_dist * 1.5, 4)

    elif _trade_style == "SWING" and _side and _close:
        if _side == "buy":
            _atr_sl  = _close - _pp["swing_sl_k"] * _atr_15m
            _struct  = (_swing_lows[-1] - 0.3 * _atr_15m) if _swing_lows else None
            _sl_p    = round(min(_struct, _atr_sl) if _struct else _atr_sl, 4)
            _sl_dist = _close - _sl_p
            _tp1_p   = round(_close + min(_sl_dist * 1.0, _atr_1h * 0.5), 4)
            _tp2_p   = round(_close + min(_sl_dist * 2.5, _atr_1h * 1.2), 4)
        else:
            _atr_sl  = _close + _pp["swing_sl_k"] * _atr_15m
            _struct  = (_swing_highs[-1] + 0.3 * _atr_15m) if _swing_highs else None
            _sl_p    = round(max(_struct, _atr_sl) if _struct else _atr_sl, 4)
            _sl_dist = _sl_p - _close
            _tp1_p   = round(_close - min(_sl_dist * 1.0, _atr_1h * 0.5), 4)
            _tp2_p   = round(_close - min(_sl_dist * 2.5, _atr_1h * 1.2), 4)

    # Max hold by style
    _max_hold_minutes = 120 if _trade_style == "FAST" else 240  # FAST: 2h, SWING: 4h
    _tp1_mult = 1.0  # kept for snapshot compatibility

    # Final entry signal
    if (_trade_style == "NO_TRADE" or not _vwap_ok
            or _4h_veto or _oi_weak
            or not _sl_p or not _tp1_p):
        _entry_signal = "NO_TRADE"
    elif _funding_warn or _funding_block:
        # Funding high — warn but don't block: profit covers funding cost for 2-4h trades
        _entry_signal = "WAIT"
    else:
        _entry_signal = "ENTRY"

    _high_risk_scalp = False  # FAST replaces SCALP

    snapshot = {
        "symbol":       symbol,
        "captured_at":  captured_at_iso,
        "expiry_time":  expiry_time,
        "llm_context": {
            "bias_4h":          _bias_4h,
            "bias_1h":          _bias_1h,
            "adx_1h":           round(_adx_1h, 1),
            "adx_4h":           round(_adx_4h, 1),
            "plus_di_1h":       round(_plus_di_1h, 1),
            "minus_di_1h":      round(_minus_di_1h, 1),
            "supertrend_dir":   _supertrend_dir,
            "rsi_1h":           _h1.get("rsi"),
            "rsi_15m":          _h15.get("rsi"),
            "volume_ratio_15m": round(_vol_ratio_sig, 2),
            "bb_width_15m":     _h15.get("bb_width_pct"),
            "day_position":     _day_position,
            "trade_style_hint": _trade_style,
            "adx_1h_rising":    _adx_1h_rising,
            "oi_delta":         round(_oi_delta, 4),
            "entry_signal":     _entry_signal,
            "funding_rate":     round(_funding, 6) if _funding is not None else None,
            "funding_blocked":  bool(_funding_block),
            "open_interest":    round(_oi, 0) if _oi is not None else None,
            "vwap_day":         _vwap,
            "day_high":         _day_high,
            "day_low":          _day_low,
            "atr_1h":           round(_atr_1h, 4),
            "atr_15m":          round(_atr_15m, 4),
            "side":             _side,
            "entry_price":      _close if _sl_p else None,
            "sl_price":         _sl_p,
            "tp1_price":        _tp1_p,
            "tp2_price":        _tp2_p,
            "max_hold_minutes":  _max_hold_minutes,
            "daily_range_pct":   round(_daily_range_pct, 2),
            "tp1_mult":          _tp1_mult,
            "is_night_session":  _is_night,
            "high_risk_scalp":   _high_risk_scalp,
        },
        "4h":           result.get("4h", {}),
        "1h":           result["1h"],
        "15m":          result["15m"],
        "5m":           result["5m"],
        "bot_decision": {
            "side":             result["signal"].get("side"),
            "reason":           result["signal"].get("reason"),
            "stopped_at_stage": _stopped_stage(result["signal"].get("reason", "")),
        },
        "action":       result["action"],
        "pending_plan": result["pending_plan"],
        "trader_notes": [],
    }
    snap_path.write_text(
        json.dumps(_json_safe(snapshot), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\nSaved: {report_path}")
    print(f"Saved: {snap_path}")

    # Chart first — so LLM can see it as visual context
    # Pass llm_context levels so chart shows SL/TP even when old strategy has no signal
    _llm_levels = {
        "sl":          _sl_p,
        "tp1":         _tp1_p,
        "tp2":         _tp2_p,
        "entry_price": _close,
    } if _sl_p else {}
    generate_chart_png(
        raw_15m, result, symbol, captured_at_iso, str(png_path),
        llm_levels=_llm_levels,
        entry_signal=_entry_signal,
        direction=_side,
        trade_style=_trade_style,
    )

    # LLM only for actionable signals — NO_TRADE uses Python template (saves API calls)
    llm_text = None
    if _entry_signal in ("ENTRY", "WAIT"):
        from src.utils.llm_formatter import generate_client_text
        llm_image = str(png_path) if png_path.exists() else image_path
        llm_text = await generate_client_text(symbol, captured_at_iso, snapshot, llm_image, client_summary=client_summary)

    if llm_text:
        delivery_text = llm_text
    else:
        # Python template — patch status label to match entry_signal
        _status_map = {"ENTRY": "ВХОД", "WAIT": "НАБЛЮДАЕМ", "NO_TRADE": "ВНЕ РЫНКА"}
        _correct = _status_map.get(_entry_signal, "ВНЕ РЫНКА")
        delivery_text = client_summary
        for _old in ["ГОТОВ ВХОД", "НАБЛЮДАЕМ", "ВНЕ РЫНКА"]:
            if f"  Статус:      {_old}" in delivery_text:
                delivery_text = delivery_text.replace(f"  Статус:      {_old}", f"  Статус:      {_correct}", 1)
                break

        # Fix new-engine vs old-engine mismatches in Python template
        # 1. Direction: new engine has a side but old engine said "нет направления"
        if _side == "buy" and "направления нет" in delivery_text:
            delivery_text = delivery_text.replace(
                "направления нет — ни LONG, ни SHORT не рассматриваются",
                "только LONG — короткая сторона не рассматривается", 1)
        elif _side == "sell" and "направления нет" in delivery_text:
            delivery_text = delivery_text.replace(
                "направления нет — ни LONG, ни SHORT не рассматриваются",
                "только SHORT — длинная сторона не рассматривается", 1)
        # 2. Remove fake pending_plan levels when new engine has no SL/TP
        if _sl_p is None:
            delivery_text = re.sub(
                r'  (Зона входа|Триггерная цена|Ориентир стопа|Первая цель|Вторая цель|Сценарий ломается):[^\n]*\n',
                '', delivery_text)

    # Append high-risk scalp warning for BTC/SOL
    if _high_risk_scalp:
        delivery_text += (
            "\n\n⚠️ Внимание: BTC и SOL — широкий спред и быстрые развороты.\n"
            "   Скальп на этих парах: плечо ≤3x, вход малой долей (до 30% депо)."
        )

    # Save for downstream consumers (e.g. telegram_bot.py)
    summary_path = run_dir / f"{symbol}_client_summary.txt"
    summary_path.write_text(delivery_text, encoding="utf-8")
    print(f"Saved: {summary_path}")

    if send_telegram:
        from src.utils.telegram import send_message
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip("'\"")
        tg_chat  = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not tg_token or not tg_chat:
            print("Telegram: not sent — TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env")
        else:
            from src.utils.telegram import send_photo_to
            import html as _html
            tg_text = _html.escape(delivery_text) if llm_text else _format_telegram(client_summary)
            await send_message(tg_text)
            if image_path and os.path.exists(image_path):
                await send_photo_to(tg_chat, image_path)
            print("Telegram: sent.")

    print(f"\nРезультаты: {run_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chart Analyzer — bot + trader + action view for a historical snapshot"
    )
    parser.add_argument("--symbol",      required=True,  help="e.g. XRP-USDT")
    parser.add_argument("--captured-at", required=True,  dest="captured_at",
                        help="ISO UTC timestamp e.g. 2026-03-09T11:42:35Z")
    parser.add_argument("--image",       default=None,   help="Path to screenshot (optional)")
    parser.add_argument("--limit",        type=int, default=100,
                        help="Candles to fetch per timeframe (default 100)")
    parser.add_argument("--send-telegram", action="store_true", dest="send_telegram",
                        help="Send client summary to Telegram after analysis")
    args = parser.parse_args()
    asyncio.run(run(args.symbol, args.captured_at, args.limit, args.image, args.send_telegram))


if __name__ == "__main__":
    main()
