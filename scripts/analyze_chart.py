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
        "signal": signal,
        "action": action,
    }


def _action_hint(bear: bool, bull: bool, near_ema: bool,
                 m_close: float, ema20: float, atr: float) -> str:
    if not bear and not bull:
        return "Нет тренда — не торговать. Ждать ADX ≥ 20."
    direction = "шорт" if bear else "лонг"
    if not near_ema:
        dist = abs(m_close - ema20)
        return f"Ждать {direction}-сетапа у EMA20(15m) ≈ {ema20:.4f} (сейчас gap={dist:.4f})"
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
    """Two distinct templates based on whether a signal is confirmed:
    - act["valid"]: ПЛАН ВХОДА (trade plan, 3 blocks)
    - otherwise:   СЕЙЧАС ОРДЕР НЕ СТАВИМ (observation, 4-5 blocks)
    """
    h      = r["1h"]
    m      = r["15m"]
    f      = r["5m"]
    sig    = r["signal"]
    act    = r["action"]
    reason = sig.get("reason", "")

    try:
        dt     = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        ts_str = dt.strftime("%d %b %Y  %H:%M UTC")
    except Exception:
        ts_str = captured_at

    has_trend   = h["bull"] or h["bear"]
    setup_zone  = m["near_ema"] and m["structure_ok"]
    side_is_buy = sig.get("side") == "buy"
    gap         = abs(m["close"] - m["ema20"])
    threshold   = m["pb_touch_threshold"]

    if gap <= threshold:
        zone_prox = "уже в зоне"
    elif gap <= threshold * 2:
        zone_prox = "приближается к зоне"
    else:
        zone_prox = "пока не добралась до зоны"

    adx_word   = "устойчивый" if h["adx"] >= 25 else "умеренный" if h["adx"] >= 20 else "слабый"
    em_rel     = "выше" if h["ema20"] > h["ema50"] else "ниже"
    pb_char    = "с умеренным объёмом" if m["pb_vol_weak"] else "с повышенным объёмом"
    side_above = "выше" if h["bull"] else "ниже"   # direction to recover structure
    side_below = "ниже" if h["bull"] else "выше"   # direction that breaks scenario
    bk_dir     = "выше" if h["bull"] else "ниже"   # breakout direction on 5m
    direction  = "лонга" if h["bull"] else "шорта"

    SEP   = "═" * 46
    lines = [SEP, f"  {symbol}  |  {ts_str}", SEP, ""]

    # ── СЕЙЧАС НА РЫНКЕ (one strong analytical block, same for both templates) ──
    lines.append("СЕЙЧАС НА РЫНКЕ")
    if act["valid"]:
        struct = "бычий" if h["bull"] else "медвежий"
        lines.append(f"  На 1H контекст {struct} — EMA20 {em_rel} EMA50, ADX {adx_word}. На 15m цена откатила к средней зоне {pb_char}, структура удержалась. На 5m зафиксирован пробой с объёмом — все условия выполнены.")
        lines.append("  (Проще: рынок дал нужную последовательность — контекст, откат, пробой. Всё сошлось.)")
    elif not has_trend:
        lines.append(f"  На старшем таймфрейме уверенного направленного движения нет — ADX {adx_word}, рынок не показывает ясного импульса в одну сторону.")
        if f["vol_strong"]:
            lines.append("  На 5m есть локальная активность, но без контекста сверху это не основание для позиции.")
        lines.append("  (Проще: нет нужного контекста — ждать пока рынок определится.)")
    elif setup_zone and reason == "pullback_volume_strong":
        struct = "бычий" if h["bull"] else "медвежий"
        lines.append(f"  На 1H контекст {struct} — EMA20 {em_rel} EMA50, ADX {adx_word}. На 15m цена вернулась к средней зоне, однако откат сопровождается повышенным объёмом — структура под давлением.")
        lines.append("  (Проще: уровень правильный, но движение к нему слишком агрессивное — лучше подождать.)")
    elif setup_zone and f["breakout"]:
        struct = "бычий" if h["bull"] else "медвежий"
        lines.append(f"  На 1H контекст {struct} — EMA20 {em_rel} EMA50, ADX {adx_word}. На 15m цена у средней зоны, на 5m появляется движение — однако не все условия подтверждения пока выполнены.")
        lines.append("  (Проще: что-то начинается, но сигнал пока неполный — ждём.)")
    elif setup_zone:
        struct = "бычий" if h["bull"] else "медвежий"
        lines.append(f"  На 1H контекст {struct} — EMA20 {em_rel} EMA50, ADX {adx_word}. На 15m цена вернулась к средней зоне {pb_char} — структура держится, откат выглядит как коррекция. Ждём сигнала на 5m.")
        lines.append("  (Проще: всё сходится — контекст, уровень, характер отката. Остаётся только сигнал.)")
    elif not m["structure_ok"]:
        struct = "бычий" if h["bull"] else "медвежий"
        lines.append(f"  На 1H контекст {struct} — EMA20 {em_rel} EMA50, ADX {adx_word}. На 15m структура не удержана — цена {side_below} EMA50, качество сетапа низкое.")
        lines.append("  (Проще: тренд есть, но на 15m цена пробила опорную зону. Ждать восстановления структуры.)")
    else:
        struct   = "бычий" if h["bull"] else "медвежий"
        move_dir = "растёт" if h["bull"] else "падает"
        lines.append(f"  На 1H контекст {struct} — EMA20 {em_rel} EMA50, ADX {adx_word}. На 15m цена ещё не откатила к средней зоне — рынок {move_dir} без паузы, удобной точки входа пока нет.")
        lines.append("  (Проще: тренд есть, но цена не в том месте. Ждём отката к EMA20.)")
    lines.append("")

    # ════════════════════════════════════════════════════════════════
    # TEMPLATE A — вход подтверждён (act["valid"] = True)
    # ════════════════════════════════════════════════════════════════
    if act["valid"]:
        lines.append("ПЛАН ВХОДА")
        lines.append(f"  Вход можно рассматривать около {act['entry']}.")
        lines.append(f"  Защитный выход:  {act['sl']}")
        lines.append(f"  Первая цель:     {act['tp1']}")
        lines.append(f"  Вторая цель:     {act['tp2']}")
        lines.append("")

        lines.append("КОГДА ЛУЧШЕ НЕ ВХОДИТЬ")
        lines.append("  Если цена резко ушла от уровня к моменту исполнения.")
        lines.append("  Если рынок начал хаотично двигаться в обе стороны.")

    # ════════════════════════════════════════════════════════════════
    # TEMPLATE B — наблюдение (act["valid"] = False)
    # ════════════════════════════════════════════════════════════════
    else:
        # ── СЕЙЧАС ОРДЕР НЕ СТАВИМ ───────────────────────────────
        lines.append("СЕЙЧАС ОРДЕР НЕ СТАВИМ")
        if not has_trend:
            lines.append("  Нет направленного контекста на 1H — торговать нечего.")
        elif not m["structure_ok"]:
            lines.append("  На 15m структура не удержана — сетап некачественный.")
        elif reason == "pullback_volume_strong":
            lines.append("  Откат к зоне слишком агрессивный — лучше дождаться ослабления давления.")
        elif f["breakout"]:
            lines.append("  Сетап формируется — не все условия подтверждения пока выполнены.")
        else:
            lines.append("  Сетап формируется — ждём подтверждающего импульса на 5m.")
        lines.append("")

        # ── ЗОНА НАБЛЮДЕНИЯ ──────────────────────────────────────
        lines.append("ЗОНА НАБЛЮДЕНИЯ")
        if not has_trend:
            lines.append("  Зона не определена — сначала нужен контекст на 1H.")
        elif not m["structure_ok"]:
            lines.append("  Сейчас зона входа не сформирована.")
            lines.append(f"  Сначала цене нужно вернуться {side_above} EMA50 (15m) ≈ {m['ema50']:.4f}.")
        elif setup_zone:
            lines.append(f"  EMA20 (средняя зона цены на 15m) ≈ {m['ema20']:.4f}.")
            if reason == "pullback_volume_strong":
                lines.append("  Цена у зоны, но характер движения к ней агрессивный.")
            elif f["breakout"]:
                lines.append("  Цена у зоны, на 5m формируется движение.")
            else:
                lines.append("  Цена у зоны, откат выглядит как коррекция.")
        else:
            # structure_ok=True, not near EMA20
            lines.append(f"  EMA20 (средняя зона цены на 15m) ≈ {m['ema20']:.4f}.")
            if zone_prox == "приближается к зоне":
                lines.append("  Цена приближается к зоне — следим за поведением при подходе.")
            else:
                lines.append("  Цена пока не добралась до зоны — ждём отката.")
        lines.append("")

        # ── ЧТО НУЖНО ДЛЯ ВХОДА ─────────────────────────────────
        lines.append("ЧТО НУЖНО ДЛЯ ВХОДА")
        if not has_trend:
            lines.append("  Лучше дождаться пока рынок определится с направлением на 1H.")
        elif not m["structure_ok"]:
            lines.append(f"  Цена должна закрепиться {side_above} EMA50 (15m).")
            lines.append("  После этого — спокойный откат к EMA20 и подтверждение на 5m.")
        elif reason == "pullback_volume_strong":
            lines.append(f"  Новый откат к EMA20 с умеренным объёмом.")
            lines.append(f"  Затем подтверждающий импульс на 5m в сторону {direction}а.")
        elif f["breakout"]:
            lines.append(f"  Свеча на 5m должна закрыться {bk_dir} последней структуры с объёмом выше среднего.")
            lines.append("  Все условия должны выполниться одновременно — частичного сигнала недостаточно.")
        else:
            lines.append(f"  Свеча на 5m должна закрыться {bk_dir} последней структуры.")
            lines.append("  Объём на 5m должен быть выше среднего.")
        lines.append("")

        # ── КОГДА ИДЕЯ ТЕРЯЕТ СМЫСЛ (только если есть тренд) ────
        if has_trend:
            lines.append("КОГДА ИДЕЯ ТЕРЯЕТ СМЫСЛ")
            if not m["structure_ok"]:
                lines.append(f"  Если цена продолжает удерживаться {side_below} EMA50 — наблюдение откладываем.")
            elif setup_zone:
                lines.append(f"  Если цена уйдёт {side_below} EMA50 (15m) ≈ {m['ema50']:.4f} — сценарий наблюдения отменяем.")
            else:
                lines.append(f"  Если цена пробьёт EMA50 (15m) ≈ {m['ema50']:.4f}, сценарий наблюдения отменяем.")

    lines += ["", SEP]
    return "\n".join(lines)


def _format_telegram(client_summary: str) -> str:
    """Wrap client summary in a monospace block for Telegram HTML."""
    return f"<pre>{client_summary}</pre>"


# ── Trader view ───────────────────────────────────────────────────────────────

def build_trader_view(r: dict) -> list:
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
        lines.append(f"• На 15m цена у EMA20 ({m['ema20']:.4f}) — зона пулбэка есть")
    else:
        dist = abs(m["close"] - m["ema20"])
        lines.append(
            f"• На 15m цена далеко от EMA20 (gap={dist:.4f}, "
            f"порог={m['pb_touch_threshold']:.4f}) — сетапа нет"
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
        lines.append(f"• Итог: нет сетапа. Ждать цены к EMA20(15m) ≈ {m['ema20']:.4f}")
    elif reason == "pullback_volume_strong":
        lines.append("• Итог: пулбэк слишком агрессивный. Ждать ослабления объёма")
    elif reason == "no_breakout_5m":
        lines.append("• Итог: сетап есть — ждать пробойной свечи на 5m")
    elif reason == "breakout_volume_weak":
        lines.append("• Итог: пробой есть, объём слабый — не входить")
    elif reason == "di_not_confirmed_5m":
        lines.append("• Итог: пробой и объём есть, DI против — не входить")

    return lines


def build_action_view(r: dict) -> list:
    lines = []
    act = r["action"]
    sig = r["signal"]

    if act["valid"]:
        side_str = "LONG" if sig["side"] == "buy" else "SHORT"
        lines.append(f"  Направление:  {side_str}  ({act['type']})")
        lines.append(f"  Entry:        {act['entry']}")
        lines.append(f"  SL:           {act['sl']}  (dist={act['sl_dist']:.4f})")
        lines.append(f"  TP1:          {act['tp1']}  (R×{act['r_multiple']})")
        lines.append(f"  TP2:          {act['tp2']}  (R×{act['r_multiple'] * 1.5:.1f})")
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
    trader = build_trader_view(r)
    action = build_action_view(r)

    SEP  = "=" * 62
    THIN = "─" * 62
    trend_str = "BULLISH" if h["bull"] else "BEARISH" if h["bear"] else "NONE (flat)"

    lines = [
        SEP,
        f"  CHART ANALYSIS: {symbol} @ {captured_at}",
        SEP,
        "",
        f"── 1H TREND {THIN[11:]}",
        f"  EMA20:  {h['ema20']:<12.4f} EMA50:  {h['ema50']:.4f}",
        f"  ADX:    {h['adx']:<12.1f} +DI: {h['plus_di']:.1f}   -DI: {h['minus_di']:.1f}",
        f"  Trend:  {trend_str}",
        "",
        f"── 15m SETUP {THIN[12:]}",
        f"  Close:  {m['close']:<12.4f} EMA20: {m['ema20']:.4f}   EMA50: {m['ema50']:.4f}",
        f"  ATR:    {m['atr']:.4f}",
        f"  Near EMA20:   gap={abs(m['close'] - m['ema20']):.4f}  "
        f"threshold={m['pb_touch_threshold']:.4f}  {ok(m['near_ema'])}",
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
        "СЕЙЧАС ОРДЕР НЕ СТАВИМ",
        "ЗОНА НАБЛЮДЕНИЯ",
        "ЧТО НУЖНО ДЛЯ ВХОДА",
        "КОГДА ИДЕЯ ТЕРЯЕТ СМЫСЛ",
        "КОГДА ЛУЧШЕ НЕ ВХОДИТЬ",
    }

    def line_color(text: str) -> tuple:
        stripped = text.strip()
        if "═" in text:
            return (100, 160, 255)
        if stripped in _SECTION_HEADERS:
            return (80, 140, 210)
        if stripped.startswith("Вход можно"):
            return (255, 200, 80)
        if stripped.startswith(("Первая цель:", "Вторая цель:")):
            return (255, 200, 80)
        if stripped.startswith("Защитный выход:"):
            return (220, 130, 80)
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
