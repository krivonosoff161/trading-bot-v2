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
    find_swing_levels, atr_regime,
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

def analyze(raw_1h: list, raw_15m: list, raw_5m: list, params: dict, min_sl_percent: float = 0.003) -> dict:
    ema_fast   = int(params["ema_fast"])
    ema_slow   = int(params["ema_slow"])
    adx_period = int(params["adx_period"])
    adx_thresh = float(params["adx_threshold_1h"])
    pb_touch   = float(params["pullback_touch_atr"])
    pb_bars    = int(params["pullback_volume_bars"])
    pb_factor  = float(params["pullback_volume_factor"])
    bk_lookbk  = int(params["breakout_lookback_5m"])
    vol_period = int(params["trigger_volume_ma_period"])
    vol_factor = float(params["trigger_volume_factor"])
    sl_buffer  = float(params["sl_buffer_atr"])
    tp_r       = float(params["tp_r_multiple"])

    # ── 1H ──────────────────────────────────────────────────────────────────
    highs_1h, lows_1h, closes_1h = parse_candles(raw_1h)
    adx, plus_di, minus_di = calc_adx(
        highs_1h, lows_1h, closes_1h, period=adx_period, bar_index=-2
    )
    ema20_1h = calc_ema(closes_1h, ema_fast)
    ema50_1h = calc_ema(closes_1h, ema_slow)
    bull_1h = ema20_1h[-2] > ema50_1h[-2] and plus_di > minus_di and adx >= adx_thresh
    bear_1h = ema20_1h[-2] < ema50_1h[-2] and minus_di > plus_di and adx >= adx_thresh

    # ── 15m ─────────────────────────────────────────────────────────────────
    highs_15m, lows_15m, closes_15m = parse_candles(raw_15m)
    vols_15m  = parse_volumes(raw_15m)
    atr_15m   = calc_atr(highs_15m, lows_15m, closes_15m, period=adx_period)
    ema20_15m = calc_ema(closes_15m, ema_fast)
    ema50_15m = calc_ema(closes_15m, ema_slow)

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
        if signal["side"] == "buy":
            sl_price = round(entry_price - sl_dist, 4)
            tp_price = round(entry_price + tp_dist, 4)
            tp2_price = round(entry_price + tp_dist * 1.5, 4)
        else:
            sl_price = round(entry_price + sl_dist, 4)
            tp_price = round(entry_price - tp_dist, 4)
            tp2_price = round(entry_price - tp_dist * 1.5, 4)
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
            if bull_1h:
                tp1 = round(trigger + sl_dist * tp_r,       6)
                tp2 = round(trigger + sl_dist * tp_r * 1.5, 6)
            else:
                tp1 = round(trigger - sl_dist * tp_r,       6)
                tp2 = round(trigger - sl_dist * tp_r * 1.5, 6)
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
        },
        "15m": {
            "close": round(float(cur_close), 6),
            "ema20": round(float(ema20_15m[-2]), 6),
            "ema50": round(float(ema50_15m[-2]), 6),
            "atr": round(float(atr_15m), 6),
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
        "signal":       signal,
        "action":       action,
        "pending_plan": pending_plan,
    }


def _action_hint(bear: bool, bull: bool, near_ema: bool,
                 m_close: float, ema20: float, atr: float) -> str:
    if not bear and not bull:
        return "Нет тренда — не торговать. Ждать ADX ≥ 20."
    direction = "шорт" if bear else "лонг"
    if not near_ema:
        dist = abs(m_close - ema20)
        return f"Ждать {direction}-сетапа у EMA20(15m) ≈ {ema20:.4f} (сейчас gap={_fmt_metric(dist)})"
    return f"Сетап близко для {direction} — ждать пробойной 5m свечи с объёмом"


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
    lines += ["", f"  Актуально до: {until_str}", "", SEP]
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


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(symbol: str, captured_at_iso: str, limit: int, image_path: str = None, send_telegram: bool = False) -> None:
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
    raw_1h, raw_15m, raw_5m = await asyncio.gather(
        client.get_history_candles(symbol, "1H",  after=after_ts, limit=limit),
        client.get_history_candles(symbol, "15m", after=after_ts, limit=limit),
        client.get_history_candles(symbol, "5m",  after=after_ts, limit=limit),
    )
    await client.close()

    if not raw_1h or not raw_15m or not raw_5m:
        print("ERROR: No candle data returned. Check symbol and captured-at timestamp.")
        return

    c1h  = confirm_label(raw_1h)
    c15m = confirm_label(raw_15m)
    c5m  = confirm_label(raw_5m)
    print(f"Latest bar status:  1H={c1h}  15m={c15m}  5m={c5m}\n")

    result         = analyze(raw_1h, raw_15m, raw_5m, params, min_sl_percent)
    report_text    = format_report(symbol, captured_at_iso, result)
    client_summary = build_client_summary(symbol, captured_at_iso, result)

    print(report_text)
    print("\n── CLIENT SUMMARY " + "─" * 44)
    print(client_summary)

    # Save outputs — one folder per run
    ts_label = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    run_dir  = Path(__file__).parent / "analysis_output" / ts_label
    run_dir.mkdir(parents=True, exist_ok=True)

    report_path = run_dir / f"{symbol}_report.md"
    snap_path   = run_dir / f"{symbol}_snapshot.json"
    png_path    = run_dir / f"{symbol}_annotated.png"

    report_path.write_text(report_text + "\n", encoding="utf-8")

    snapshot = {
        "symbol":       symbol,
        "captured_at":  captured_at_iso,
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

    if image_path and Path(image_path).exists():
        generate_annotated_png(image_path, client_summary, str(png_path))
    elif image_path:
        print(f"WARNING: Image not found: {image_path}")

    if send_telegram:
        from src.utils.telegram import send_message
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip("'\"")
        tg_chat  = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not tg_token or not tg_chat:
            print("Telegram: not sent — TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env")
        else:
            await send_message(_format_telegram(client_summary))
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
