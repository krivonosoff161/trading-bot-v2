"""
Chart Analyzer — pulls historical OKX data for a given moment and produces:
  - Console report
  - analysis_output/<sym>_<ts>_report.md
  - analysis_output/<sym>_<ts>_snapshot.json
  - analysis_output/<sym>_<ts>_chart.png

Usage (manual):
    python scripts/analyze_chart.py --symbol XRP-USDT --captured-at "2026-03-09T11:42:35Z"
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
    calc_adx, calc_atr, calc_ema, parse_candles,
    find_swing_levels, atr_regime, calc_bollinger_bands, calc_supertrend,
    calc_chandelier_exit, calc_rsi,
)


# ── Config ────────────────────────────────────────────────────────────────────

def load_strategy_params() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("strategy", {})



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


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _fmt_metric(value: float) -> str:
    val = float(value)
    abs_val = abs(val)
    if abs_val >= 1:
        return f"{val:.4f}"
    if abs_val >= 0.01:
        return f"{val:.5f}"
    return f"{val:.6f}"


def _fmt_price(symbol: str, price) -> str:
    if price is None:
        return "—"
    base = symbol.split("-")[0].upper()
    if base == "BTC":
        return f"{float(price):.1f}"
    if base in ("ETH", "SOL"):
        return f"{float(price):.2f}"
    return f"{float(price):.4f}"


_MSK_OFFSET = 3  # UTC+3, no DST


def _to_msk(dt) -> "datetime":
    """Convert UTC datetime to Moscow time (UTC+3)."""
    from datetime import timedelta
    return dt + timedelta(hours=_MSK_OFFSET)


def _next_candle_close(captured_at: str, tf_minutes: int) -> str:
    try:
        dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        total_min = dt.hour * 60 + dt.minute
        next_boundary = ((total_min // tf_minutes) + 1) * tf_minutes
        h_utc, m = divmod(next_boundary % (24 * 60), 60)
        h_msk = (h_utc + _MSK_OFFSET) % 24
        return f"{h_msk:02d}:{m:02d} МСК"
    except Exception:
        return "—"


def _build_micro_snapshot(book: dict | None, trades: list | None) -> dict:
    """Summarize microstructure from books5 + recent trades."""
    if not book and not trades:
        return {}
    bids = book.get("bids", []) if book else []
    asks = book.get("asks", []) if book else []
    bid_sum = sum(float(level[1]) for level in bids[:5]) if bids else 0.0
    ask_sum = sum(float(level[1]) for level in asks[:5]) if asks else 0.0
    denom = bid_sum + ask_sum
    obi = (bid_sum - ask_sum) / denom if denom > 0 else 0.0
    best_bid = float(bids[0][0]) if bids else 0.0
    best_ask = float(asks[0][0]) if asks else 0.0
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
    spread_bps = ((best_ask - best_bid) / mid * 10000) if mid > 0 else 0.0
    buy_vol = sell_vol = 0.0
    buy_count = sell_count = 0
    for trade in trades or []:
        side = (trade.get("side") or "").lower()
        size = float(trade.get("sz", 0) or 0)
        if side == "buy":
            buy_vol += size
            buy_count += 1
        elif side == "sell":
            sell_vol += size
            sell_count += 1
    delta_denom = buy_vol + sell_vol
    trade_delta = (buy_vol - sell_vol) / delta_denom if delta_denom > 0 else 0.0
    return {
        "obi_top5": round(obi, 4),
        "spread_bps": round(spread_bps, 2),
        "bid_sum_5": round(bid_sum, 4),
        "ask_sum_5": round(ask_sum, 4),
        "trade_delta_100": round(trade_delta, 4),
        "buy_vol_100": round(buy_vol, 4),
        "sell_vol_100": round(sell_vol, 4),
        "buy_count_100": buy_count,
        "sell_count_100": sell_count,
        "best_bid": best_bid or None,
        "best_ask": best_ask or None,
    }


# ── Indicators ────────────────────────────────────────────────────────────────

def compute_indicators(
    raw_1h: list,
    raw_15m: list,
    raw_5m: list,
    params: dict,
    raw_4h: list | None = None,
) -> dict:
    """Compute all indicators across timeframes. No signal, no action — pure data."""
    ema_fast   = int(params["ema_fast"])
    ema_slow   = int(params["ema_slow"])
    adx_period = int(params["adx_period"])

    # ── 4H ──────────────────────────────────────────────────────────────────
    h4_data: dict = {}
    if raw_4h:
        highs_4h, lows_4h, closes_4h = parse_candles(raw_4h)
        adx_4h, plus_di_4h, minus_di_4h = calc_adx(
            highs_4h, lows_4h, closes_4h, period=adx_period, bar_index=-2
        )
        ema20_4h = calc_ema(closes_4h, ema_fast)
        ema50_4h = calc_ema(closes_4h, ema_slow)
        bb_4h    = calc_bollinger_bands(closes_4h, period=20, std_mult=2.5)
        h4_data = {
            "ema20":    round(float(ema20_4h[-2]), 6),
            "ema50":    round(float(ema50_4h[-2]), 6),
            "adx":      round(adx_4h, 1),
            "plus_di":  round(plus_di_4h, 1),
            "minus_di": round(minus_di_4h, 1),
            "bb_upper": bb_4h["upper"],
            "bb_lower": bb_4h["lower"],
            "bb_width": bb_4h["width_pct"],
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
    bb_1h    = calc_bollinger_bands(closes_1h, period=20, std_mult=2.0)
    ce_1h    = calc_chandelier_exit(highs_1h, lows_1h, closes_1h, lookback=22, multiplier=3.5)

    # EMA-based bias (no ADX requirement)
    _bull_1h = float(ema20_1h[-2]) > float(ema50_1h[-2])
    _bear_1h = float(ema20_1h[-2]) < float(ema50_1h[-2])

    # ── 15m ─────────────────────────────────────────────────────────────────
    highs_15m, lows_15m, closes_15m = parse_candles(raw_15m)
    atr_15m   = calc_atr(highs_15m, lows_15m, closes_15m, period=adx_period)
    ema20_15m = calc_ema(closes_15m, ema_fast)
    ema50_15m = calc_ema(closes_15m, ema_slow)
    rsi_15m   = calc_rsi(closes_15m, period=14)
    bb_15m    = calc_bollinger_bands(closes_15m, period=20, std_mult=2.0)
    swings_15m     = find_swing_levels(highs_15m, lows_15m, lookback=3, count=4)
    atr_pct, atr_lbl = atr_regime(highs_15m, lows_15m, closes_15m, period=adx_period)
    supertrend_15m = calc_supertrend(highs_15m, lows_15m, closes_15m, period=14, multiplier=3.0)
    cur_close = closes_15m[-2]

    # ── 5m ──────────────────────────────────────────────────────────────────
    highs_5m, lows_5m, closes_5m = parse_candles(raw_5m)
    ema20_5m = calc_ema(closes_5m, ema_fast)
    rsi_5m   = calc_rsi(closes_5m, period=14)

    return {
        "1h": {
            "ema20":        round(float(ema20_1h[-2]), 6),
            "ema50":        round(float(ema50_1h[-2]), 6),
            "ema20_series": [round(float(x), 6) for x in ema20_1h[-6:]],
            "adx":          round(adx, 2),
            "plus_di":      round(plus_di, 2),
            "minus_di":     round(minus_di, 2),
            "atr":          round(float(atr_1h), 6),
            "rsi":          round(rsi_1h, 1),
            "bb_width_pct": bb_1h["width_pct"],
            "bull":         _bull_1h,
            "bear":         _bear_1h,
        },
        "15m": {
            "close":          round(float(cur_close), 6),
            "ema20":          round(float(ema20_15m[-2]), 6),
            "ema50":          round(float(ema50_15m[-2]), 6),
            "atr":            round(float(atr_15m), 6),
            "rsi":            round(rsi_15m, 1),
            "atr_pct":        atr_pct,
            "atr_label":      atr_lbl,
            "swing_highs":    swings_15m["recent_highs"],
            "swing_lows":     swings_15m["recent_lows"],
            "bb_upper":       bb_15m["upper"],
            "bb_middle":      bb_15m["middle"],
            "bb_lower":       bb_15m["lower"],
            "bb_pct_b":       bb_15m["pct_b"],
            "bb_width_pct":   bb_15m["width_pct"],
            "supertrend":     supertrend_15m["value"],
            "supertrend_dir": supertrend_15m["direction"],
            "supertrend_dist":supertrend_15m["distance_pct"],
            "ce_long":        ce_1h["ce_long"],
            "ce_short":       ce_1h["ce_short"],
        },
        "5m": {
            "trigger_close": round(float(closes_5m[-2]), 6),
            "ema20":         round(float(ema20_5m[-2]), 6),
            "rsi":           round(rsi_5m, 1),
        },
        "4h": h4_data,
    }


# ── Client summary (FAST/SWING engine) ────────────────────────────────────────

def build_engine_summary(symbol: str, captured_at: str, eng: dict) -> str:
    """Python fallback client text — explains FAST/SWING engine decision in plain words."""
    trade_style   = eng["trade_style"]
    entry_signal  = eng["entry_signal"]
    side          = eng["side"]
    bias_1h       = eng["bias_1h"]
    adx_1h        = eng["adx_1h"]
    adx_rising    = eng["adx_1h_rising"]
    vol_ratio     = eng["vol_ratio_sig"]
    bb_expanding  = eng["bb_expanding"]
    vwap_ok       = eng["vwap_ok"]
    oi_weak       = eng["oi_weak"]
    is_night      = eng["is_night"]
    funding_warn  = eng["funding_warn"]
    funding_block = eng["funding_block"]
    funding_val   = eng["funding_val"]
    close         = eng["close"]
    sl_p          = eng["sl_p"]
    tp1_p         = eng["tp1_p"]
    tp2_p         = eng["tp2_p"]
    vwap          = eng["vwap"]
    day_high      = eng["day_high"]
    day_low       = eng["day_low"]
    max_hold      = eng["max_hold_minutes"]
    daily_range_pct = eng.get("daily_range_pct", 0.0)
    day_position    = eng.get("day_position")
    bb_width_pct    = eng.get("bb_width_15m", 0.0)
    rsi_1h          = eng.get("rsi_1h", 50.0)
    rsi_15m         = eng.get("rsi_15m", 50.0)
    four_h_conflict = eng.get("four_h_conflict", False)
    adx_4h_ok       = eng.get("adx_4h_ok", True)
    five_m_trigger  = eng.get("five_m_trigger", True)
    adx_4h          = eng.get("adx_4h", 0.0)
    regime          = eng.get("regime", "RANGING")

    try:
        dt     = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        ts_str = _to_msk(dt).strftime("%d %b %Y  %H:%M МСК")
    except Exception:
        ts_str = captured_at

    _status_map = {"ENTRY": "ВХОД", "WAIT": "НАБЛЮДАЕМ", "NO_TRADE": "ВНЕ РЫНКА"}
    _status = _status_map.get(entry_signal, "ВНЕ РЫНКА")

    _style_map = {
        "FAST":  "⚡ БЫСТРЫЙ — закрыть в течение 2 часов",
        "SWING": "📈 СВИНГ — держать до 4 часов",
    }
    _type_str = _style_map.get(trade_style, "")

    if side == "buy":
        _dir = "только LONG — короткая сторона не рассматривается"
    elif side == "sell":
        _dir = "только SHORT — длинная сторона не рассматривается"
    else:
        _dir = "направления нет — ни LONG, ни SHORT не рассматриваются"

    fp  = lambda p: _fmt_price(symbol, p)
    SEP = "═" * 46
    lines = [SEP, f"  {symbol}  |  {ts_str}", SEP, "",
             f"  Статус:      {_status}"]
    if _type_str and entry_signal != "NO_TRADE":
        lines.append(f"  Тип:         {_type_str}")
    lines += [f"  Направление: {_dir}", ""]

    # ── ENTRY or WAIT ────────────────────────────────────────────────────────
    if entry_signal in ("ENTRY", "WAIT"):
        _dir_word  = "вверх" if side == "buy" else "вниз"
        _long_short = "ЛОНГ" if side == "buy" else "ШОРТ"

        lines.append("📊 СЕЙЧАС НА РЫНКЕ")
        if regime == "RANGING":
            _zone = "дна" if side == "buy" else "вершины"
            _expect = "отскок вверх" if side == "buy" else "откат вниз"
            lines.append(f"  Рынок в диапазоне. Цена у {_zone} дневного диапазона — ожидаем {_expect} к VWAP.")
        else:
            lines.append(f"  Тренд 1H направлен {_dir_word} и набирает силу — объём импульса подтверждает движение.")
        if vwap and close:
            _vwap_word  = "выше" if float(close) >= float(vwap) else "ниже"
            _ctrl       = "покупатели" if side == "buy" else "продавцы"
            lines.append(f"  Цена {_vwap_word} дневного уровня равновесия ({fp(vwap)}) — {_ctrl} контролируют день.")
        if day_high and day_low:
            _range_note = ""
            if daily_range_pct >= 20:
                _range_note = f" ⚠️ Волатильность экстремальная ({daily_range_pct:.1f}% дня)."
            elif daily_range_pct >= 8:
                _range_note = f" Широкий день ({daily_range_pct:.1f}%)."
            lines.append(f"  Диапазон дня: {fp(day_low)} — {fp(day_high)}.{_range_note}")
        if day_position is not None:
            if day_position <= 0.15:
                lines.append(f"  Цена у дна дня ({int(day_position*100)}%) — потенциал вверх остаётся.")
            elif day_position >= 0.85:
                lines.append(f"  Цена у вершины дня ({int(day_position*100)}%) — потенциал вниз остаётся.")
        if rsi_1h <= 25:
            lines.append(f"  RSI 1H перепродан ({rsi_1h:.0f}) — возможен отскок.")
        elif rsi_1h >= 75:
            lines.append(f"  RSI 1H перекуплен ({rsi_1h:.0f}) — давление на рост снизится.")
        lines.append("")

        if funding_warn or funding_block:
            pct  = round(abs(funding_val) * 100, 3)
            _fw  = "лонги переплачивают" if funding_val > 0 else "шорты переплачивают"
            lines.append(f"⚠️ Ставка финансирования повышена ({pct}%, {_fw}) — учитывай при удержании позиции.")
            lines.append("")

        if entry_signal == "ENTRY":
            lines.append("✅ СТАВИМ ЛИМИТКУ")
            lines.append(f"  Лимитка {_long_short} по цене {fp(close)}.")
        else:
            lines.append("⏸️ ЖДЁМ ПОДТВЕРЖДЕНИЯ")
            lines.append(f"  Сигнал формируется — ставим лимитку {_long_short} при подтверждении.")
        lines.append("")

        if sl_p and tp1_p and tp2_p:
            _arr = "📈" if side == "buy" else "📉"
            _sl_pct = abs(close - sl_p) / close * 100
            lines.append("📋 ИСПОЛНЕНИЕ (авто + ручное)")
            lines.append(f"  {_arr} Вход:            {fp(close)}")
            lines.append(f"  🛑 Стоп:            {fp(sl_p)}  (−{_sl_pct:.2f}% от входа)")
            lines.append(f"  🎯 Цель (авто):     {fp(tp1_p)}")
            lines.append(f"  🎯 Цель 2 (ручная): {fp(tp2_p)}")
            lines.append("")

            # Position sizing hint — dynamic per signal
            _ex_notional = round(1000 * 0.01 / (_sl_pct / 100))
            _ex_margin   = round(_ex_notional / 10)
            _risk_note   = "0.5-1% депозита (СВИНГ — стоп шире)" if trade_style == "SWING" else "1% депозита (СКАЛЬП)"
        else:
            _sl_pct      = None
            _risk_note   = "0.5-1% депозита (СВИНГ — стоп шире)" if trade_style == "SWING" else "1% депозита (СКАЛЬП)"
            _ex_notional = None
            _ex_margin   = None

        lines.append(f"⏱ Закрыть через {max_hold} минут если уровни не достигнуты.")
        lines.append("")
        lines.append("⚠️ ПРАВИЛА ВХОДА")
        lines.append("  ├─ Плечо: 10x")
        lines.append("  ├─ Стоп: не двигать дальше")
        lines.append(f"  ├─ Риск: {_risk_note}")
        if _sl_pct and _ex_notional:
            lines.append(f"  ├─ Размер: 1% ÷ {_sl_pct:.2f}% = ${_ex_notional} нотионала на $1000")
            lines.append(f"  │          → маржа ${_ex_margin} при 10x")
        lines.append("  └─ Это аналитика — не инвест-рекомендация")

    # ── NO_TRADE ─────────────────────────────────────────────────────────────
    else:
        # Primary reason — first match wins, branched by regime
        if regime == "RANGING":
            if side is None:
                why  = "Цена в середине дневного диапазона — ждём пока уйдёт в крайние зоны (< 35% или > 65%)."
                what = "Следить за приближением к дневным экстремумам — только оттуда берём отскок."
            elif adx_rising:
                why  = f"ADX на 1H растёт ({adx_1h:.1f}) — рынок выходит из диапазона, mean reversion опасен."
                what = "Ждать пока ADX стабилизируется или начнёт падать — тогда диапазонная логика снова в силе."
            elif not five_m_trigger:
                _need5 = "выше" if side == "buy" else "ниже"
                why  = f"5m не подтвердила движение — FAST ждёт пробоя EMA20 на 5m {_need5}."
                what = f"Следить за 5m: как только trigger_close окажется {_need5} EMA20 — условие выполнено."
            elif not vwap_ok:
                _need = "ниже" if side == "buy" else "выше"
                why  = f"Для отскока {'вверх' if side == 'buy' else 'вниз'} нужна цена {_need} VWAP ({fp(vwap)})."
                what = f"Ждать пока цена опустится {_need} {fp(vwap)} — тогда направление отскока подтверждено."
            elif vol_ratio < 1.3:
                why  = f"Объём слабый (×{vol_ratio:.2f}) — нет импульса для отскока."
                what = "Ждать свечи с повышенным объёмом у экстремума диапазона."
            elif oi_weak:
                why  = "Открытый интерес падает — позиции закрываются, отскок ненадёжен."
                what = "Ждать стабилизации OI перед входом."
            else:
                why  = "Условия mean reversion не выполнены."
                what = "Следить за следующей 15m свечой у экстремума диапазона."
        else:
            # TRENDING / DRIFT / CHOPPY reasons
            _pp_sum = _PAIR_PARAMS.get(symbol, _PAIR_PARAMS_DEFAULT)
            _cfg_f  = _mode_cfg(_pp_sum, "trending", "fast")
            _cfg_sw = _mode_cfg(_pp_sum, "trending", "swing")
            _t_vol  = min(_cfg_f["vol"], _cfg_sw["vol"])
            _t_bb   = _cfg_f.get("bb_width_min", 0.5)
            if bias_1h == "NEUTRAL":
                why  = "EMA на 1H без чёткого расхождения — рынок без направления, шансы 50/50."
                what = "Ждать пока EMA20 и EMA50 разойдутся и ADX начнёт расти."
            elif four_h_conflict:
                why  = "4H направление против 1H — SWING требует согласования таймфреймов."
                what = "Ждать пока 4H и 1H совпадут по направлению."
            elif not adx_4h_ok:
                why  = f"На 4H нет выраженного тренда (ADX {adx_4h:.0f}) — SWING требует подтверждённого тренда на старшем ТФ."
                what = "Ждать роста ADX 4H выше 20."
            elif not adx_rising:
                why  = f"Тренд 1H есть (ADX {adx_1h:.1f}), но не ускоряется — движение в паузе."
                what = "Ждать когда ADX начнёт расти — это сигнал возобновления тренда."
            elif vol_ratio < _t_vol:
                why  = f"Объём импульса слабый (×{vol_ratio:.2f}, нужно ×{_t_vol:.1f}) — движение без подтверждения."
                what = "Ждать свечей с повышенным объёмом."
            elif bb_width_pct < _t_bb:
                why  = f"Bollinger Bands слишком узкие ({bb_width_pct:.2f}%, нужно >{_t_bb:.1f}%) — консолидация внутри тренда."
                what = "Ждать расширения полос — выход из консолидации даст сигнал."
            elif not five_m_trigger:
                _need5 = "выше" if side == "buy" else "ниже"
                why  = f"5m не подтвердила движение — FAST ждёт пробоя EMA20 на 5m {_need5}."
                what = f"Следить за 5m: как только trigger_close окажется {_need5} EMA20 — условие выполнено."
            elif oi_weak:
                why  = "Открытый интерес падает при движении цены — позиции закрываются, сигнал слабый."
                what = "Ждать стабилизации или роста открытого интереса."
            else:
                why  = "Условия входа не выполнены в полном объёме."
                what = "Следить за следующей 15m свечой."

        if side == "buy":
            _dir_ctx = "Направление — LONG, но условия входа пока не созрели."
        elif side == "sell":
            _dir_ctx = "Направление — SHORT, но условия входа пока не созрели."
        else:
            _dir_ctx = ""

        lines.append("📊 СЕЙЧАС НА РЫНКЕ")
        if _dir_ctx:
            lines.append(f"  {_dir_ctx}")
        if day_high and day_low:
            _range_note = ""
            if daily_range_pct >= 20:
                _range_note = f" ⚠️ Волатильность экстремальная ({daily_range_pct:.1f}% дня)."
            elif daily_range_pct >= 8:
                _range_note = f" Широкий день ({daily_range_pct:.1f}%)."
            lines.append(f"  Диапазон дня: {fp(day_low)} — {fp(day_high)}.{_range_note}")
        if day_position is not None:
            if day_position <= 0.10:
                lines.append(f"  Цена у дна дня ({int(day_position*100)}%) — рядом с дневным минимумом.")
            elif day_position >= 0.90:
                lines.append(f"  Цена у вершины дня ({int(day_position*100)}%) — рядом с дневным максимумом.")
            else:
                lines.append(f"  Позиция в дне: {int(day_position*100)}% (0%=дно, 100%=вершина).")
        if rsi_1h <= 25:
            lines.append(f"  RSI 1H перепродан ({rsi_1h:.0f}) — осторожно с шортами.")
        elif rsi_1h >= 75:
            lines.append(f"  RSI 1H перекуплен ({rsi_1h:.0f}) — осторожно с лонгами.")
        lines.append("")

        lines.append("🚫 НЕТ СДЕЛКИ")
        lines.append(f"  {why}")
        lines.append("")

        lines.append("👁 ЗА ЧЕМ СЛЕДИМ")
        lines.append(f"  {what}")
        lines.append("")

        lines.append("❌ НЕ ДЕЛАТЬ")
        if side == "buy":
            lines.append("  Не входить в LONG без подтверждения условий выше.")
            lines.append("  Не открывать SHORT против 1H направления.")
        elif side == "sell":
            lines.append("  Не входить в SHORT без подтверждения условий выше.")
            lines.append("  Не открывать LONG против 1H направления.")
        else:
            lines.append("  Не входить ни в LONG, ни в SHORT — направления нет.")

    # Night session disclaimer
    if is_night:
        lines += ["", "⚠️ АЗИАТСКАЯ СЕССИЯ",
                  "  Сейчас 01:00–07:00 UTC — ликвидность ниже дневной,",
                  "  возможен расширенный спред. Повышенная осторожность."]

    # Expiry
    tf_exp     = 5 if entry_signal == "ENTRY" else (15 if entry_signal == "WAIT" else 60)
    next_close = _next_candle_close(captured_at, tf_exp)
    lines += ["", "🔄 ПОВТОРНЫЙ АНАЛИЗ", f"  После {next_close}.", "", SEP]
    return "\n".join(lines)


# ── Operator report ───────────────────────────────────────────────────────────

def format_report(symbol: str, captured_at: str, indicators: dict, eng: dict) -> str:
    """Operator console report — indicator values + engine decision."""
    h1  = indicators.get("1h", {})
    h15 = indicators.get("15m", {})
    h4  = indicators.get("4h", {})

    SEP  = "=" * 62
    THIN = "─" * 62

    def _f(v, fmt=".1f"):
        return format(v, fmt) if isinstance(v, (int, float)) else str(v or "?")

    lines = [
        SEP,
        f"  CHART ANALYSIS: {symbol} @ {captured_at}",
        SEP,
        "",
        f"── 4H {THIN[5:]}",
        f"  Bias: {eng['bias_4h']}  ADX: {_f(h4.get('adx'))}  "
        f"+DI: {_f(h4.get('plus_di'))}  -DI: {_f(h4.get('minus_di'))}",
        f"  EMA20: {h4.get('ema20', '?')}  EMA50: {h4.get('ema50', '?')}",
        "",
        f"── 1H {THIN[5:]}",
        f"  Bias: {eng['bias_1h']}  ADX: {_f(h1.get('adx'))}  Rising: {ok(eng['adx_1h_rising'])}",
        f"  +DI: {_f(h1.get('plus_di'))}  -DI: {_f(h1.get('minus_di'))}  RSI: {h1.get('rsi', '?')}",
        f"  ATR_1H: {h1.get('atr', '?')}  BB_width: {_f(h1.get('bb_width_pct'), '.1f')}%",
        "",
        f"── 15m {THIN[6:]}",
        f"  Close: {h15.get('close', '?')}  ATR: {h15.get('atr', '?')}  RSI: {h15.get('rsi', '?')}",
        f"  Vol_ratio_sig: {eng['vol_ratio_sig']:.2f}  BB_expanding: {ok(eng['bb_expanding'])}",
        f"  BB_width: {_f(h15.get('bb_width_pct'), '.1f')}%",
        f"  Swing highs: {h15.get('swing_highs', [])}",
        f"  Swing lows:  {h15.get('swing_lows', [])}",
        "",
        f"── 5m {THIN[5:]}",
        f"  Trigger close: {indicators.get('5m', {}).get('trigger_close', '?')}  "
        f"EMA20: {indicators.get('5m', {}).get('ema20', '?')}  RSI: {indicators.get('5m', {}).get('rsi', '?')}",
        "",
        f"── FAST/SWING ENGINE {THIN[20:]}",
        f"  Regime:       {eng.get('regime', '?')}  "
        f"DI_1H: {eng.get('di_spread_1h', '?')}  DI_4H: {eng.get('di_spread_4h', '?')}  "
        f"4H↑: {ok(eng.get('adx_4h_rising', False))}",
        f"  Style:        {eng['trade_style']}",
        f"  Side:         {eng['side']}",
        f"  Entry signal: {eng['entry_signal']}",
        f"  VWAP ok:      {ok(eng['vwap_ok'])}  VWAP: {eng.get('vwap')}",
        f"  4H conflict:  {ok(eng.get('four_h_conflict', False))}  4H ADX ok: {ok(eng.get('adx_4h_ok', True))}",
        f"  5m trigger:   {ok(eng.get('five_m_trigger', True))}  RSI_5m: {eng.get('rsi_5m', '?')}",
        f"  OI weak:      {ok(eng['oi_weak'])}  delta: {eng.get('oi_delta', 0):.4f}",
        f"  Funding:      {round(eng.get('funding_val', 0) * 100, 4)}%"
        f"  warn: {ok(eng['funding_warn'])}  block: {ok(eng['funding_block'])}",
        f"  Night:        {ok(eng['is_night'])}",
        "",
        f"── LEVELS {THIN[9:]}",
        f"  SL:           {eng.get('sl_p')}",
        f"  TP1 (auto):   {eng.get('tp1_p')}",
        f"  TP2 (manual): {eng.get('tp2_p')}",
        "",
        SEP,
    ]
    return "\n".join(lines)


# ── Telegram helper ───────────────────────────────────────────────────────────

def _format_telegram(text: str) -> str:
    return f"<pre>{text}</pre>"


# ── Annotated PNG (user screenshot + sidebar) ─────────────────────────────────

def generate_annotated_png(image_path: str, summary_text: str, out_path: str) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("WARNING: Pillow not installed, skipping annotated PNG")
        return

    img = Image.open(image_path).convert("RGB")
    iw, ih = img.size

    PANEL_W   = 400
    PADDING   = 14
    FONT_SIZE = 14

    try:
        font = ImageFont.truetype("arial.ttf", FONT_SIZE)
    except Exception:
        font = ImageFont.load_default()

    max_chars = (PANEL_W - PADDING * 2) // 8
    wrapped   = []
    for line in summary_text.split("\n"):
        if len(line) <= max_chars:
            wrapped.append(line)
        else:
            wrapped.extend(textwrap.wrap(line, width=max_chars) or [""])

    line_h  = FONT_SIZE + 4
    panel_h = max(ih, len(wrapped) * line_h + PADDING * 2)

    canvas = Image.new("RGB", (iw + PANEL_W, panel_h), (15, 15, 20))
    canvas.paste(img, (0, (panel_h - ih) // 2))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([(iw, 0), (iw + PANEL_W, panel_h)], fill=(18, 20, 28))
    draw.line([(iw, 0), (iw, panel_h)], fill=(60, 80, 120), width=2)

    _SECTION_HEADERS = {
        "📊 СЕЙЧАС НА РЫНКЕ", "✅ СТАВИМ ЛИМИТКУ", "⏸️ ЖДЁМ ПОДТВЕРЖДЕНИЯ",
        "📋 ПЛАН", "🚫 НЕТ СДЕЛКИ", "👁 ЗА ЧЕМ СЛЕДИМ",
        "❌ НЕ ДЕЛАТЬ", "⚠️ ПРАВИЛА ВХОДА", "🔄 ПОВТОРНЫЙ АНАЛИЗ",
    }

    def line_color(text: str) -> tuple:
        s = text.strip()
        if "═" in text:
            return (100, 160, 255)
        if s.startswith("Статус:"):
            if "ВХОД" in s:    return (80, 210, 120)
            if "НАБЛЮДАЕМ" in s: return (220, 180, 60)
            return (150, 150, 155)
        if s.startswith("Направление:"):
            if "LONG"  in s: return (80, 210, 120)
            if "SHORT" in s: return (210, 80,  80)
            return (150, 150, 155)
        if s.startswith("Тип:"): return (130, 180, 255)
        if any(s.startswith(h) for h in _SECTION_HEADERS): return (80, 140, 210)
        if s.startswith("🛑"): return (220, 130, 80)
        if s.startswith("🎯"): return (255, 200, 80)
        if s.startswith("📈") or s.startswith("📉"): return (255, 200, 80)
        if s.startswith("("): return (145, 150, 165)
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


# ── Chart PNG from OKX data ───────────────────────────────────────────────────

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

    candles = list(reversed(raw_15m))
    n_show  = min(60, len(candles))
    candles = candles[-n_show:]

    ts_list = [int(c[0])   for c in candles]
    opens   = [float(c[1]) for c in candles]
    highs_c = [float(c[2]) for c in candles]
    lows_c  = [float(c[3]) for c in candles]
    closes  = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    closes_full = np.array([float(c[4]) for c in list(reversed(raw_15m))])
    highs_full  = np.array([float(c[2]) for c in list(reversed(raw_15m))])
    lows_full   = np.array([float(c[3]) for c in list(reversed(raw_15m))])
    ema20_slice = calc_ema(closes_full, 20)[-n_show:]
    ema50_slice = calc_ema(closes_full, 50)[-n_show:]

    bb_upper_arr = np.zeros(len(closes_full))
    bb_lower_arr = np.zeros(len(closes_full))
    for i in range(20, len(closes_full)):
        mid = np.mean(closes_full[i-20:i])
        std = np.std(closes_full[i-20:i], ddof=0)
        bb_upper_arr[i] = mid + 2 * std
        bb_lower_arr[i] = mid - 2 * std
    bb_upper_slice = bb_upper_arr[-n_show:]
    bb_lower_slice = bb_lower_arr[-n_show:]

    st_vals = np.zeros(len(closes_full))
    st_dirs = np.zeros(len(closes_full))
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
    BG        = "#0b0d14"
    matplotlib.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8})
    fig, (ax, ax_vol) = plt.subplots(
        2, 1, figsize=(10, 6.2), facecolor=BG,
        gridspec_kw={"height_ratios": [3.5, 1], "hspace": 0.06},
        sharex=True,
    )
    ax.set_facecolor(BG)
    ax_vol.set_facecolor(BG)

    for i, (o, h, l, c) in enumerate(zip(opens, highs_c, lows_c, closes)):
        col = "#26a69a" if c >= o else "#ef5350"
        ax.plot([i, i], [l, h], color=col, linewidth=0.7, zorder=2)
        bh = max(abs(c - o), (h - l) * 0.015)
        ax.add_patch(mpatches.Rectangle(
            (i - 0.35, min(c, o)), 0.7, bh,
            facecolor=col, edgecolor=col, linewidth=0, zorder=3,
        ))

    R_MARGIN = 9
    for arr, col, lbl in [(ema20_slice, COL_EMA20, "EMA20"), (ema50_slice, COL_EMA50, "EMA50")]:
        pts = [(i, v) for i, v in enumerate(arr) if v > 0]
        if not pts:
            continue
        xi, yi = zip(*pts)
        ax.plot(xi, yi, color=col, linewidth=1.2, zorder=4)
        last_x, last_y = pts[-1]
        ax.text(
            last_x + 0.8, last_y, f"{lbl}: {_fmt_price(symbol, last_y)}",
            color=col, fontsize=6.5, va="center", ha="left", zorder=7,
            bbox=dict(boxstyle="round,pad=0.15", facecolor=BG, edgecolor="none", alpha=0.85),
        )

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

    # Levels: llm_levels (new engine) only
    level_keys = [
        ("sl",   "SL",   "#F44336", "--"),
        ("tp1",  "TP1",  "#FFD700", "--"),
        ("tp2",  "TP2",  "#FFA500", ":"),
    ]
    src = llm_levels or {}
    for key, label, col, ls in level_keys:
        if not src.get(key):
            continue
        price = float(src[key])
        ax.axhline(y=price, color=col, linestyle=ls, linewidth=0.9, alpha=0.85, zorder=5)
        ax.text(n_show - 1, price, f"  {label}: {_fmt_price(symbol, price)}",
                color=col, fontsize=7, va="bottom", ha="right", zorder=6)

    # Entry marker
    _entry_p = src.get("entry_price")
    if _entry_p and direction and entry_signal in ("ENTRY", "WAIT"):
        _dir_text = "ВХОД LONG ▲" if direction == "buy" else "ВХОД SHORT ▼"
        _ecol = "#00E676" if direction == "buy" else "#FF5252"
        ax.axhline(y=_entry_p, color=_ecol, linestyle="-", linewidth=1.2, alpha=0.9, zorder=6)
        ax.text(
            n_show // 2, _entry_p, f"  {_dir_text}: {_fmt_price(symbol, _entry_p)}",
            color=_ecol, fontsize=8, fontweight="bold", va="bottom", ha="center", zorder=8,
            bbox=dict(boxstyle="round,pad=0.2", facecolor="#0b0d14", edgecolor=_ecol, alpha=0.9),
        )

    # Swing levels (range mode fallback when no new engine levels)
    if not src:
        h1_res  = result.get("1h", {})
        m15_res = result.get("15m", {})
        if not (h1_res.get("bull") or h1_res.get("bear")):
            close_p = m15_res.get("close")
            sh_list = m15_res.get("swing_highs") or []
            sl_list = m15_res.get("swing_lows")  or []
            if close_p and sh_list and sl_list:
                sh_p = float(sh_list[-1])
                sl_p = float(sl_list[-1])
                rng  = sh_p - sl_p
                cp   = float(close_p)
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

    _cur    = float(result.get("15m", {}).get("close") or closes[-1])
    m15     = result.get("15m", {})
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

    try:
        _dt_utc = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        _dt_msk = _to_msk(_dt_utc)
        _title_base = f"{symbol} · 15m · {_dt_msk.strftime('%Y-%m-%d %H:%M')} МСК"
    except Exception:
        _title_base = f"{symbol} · 15m · {captured_at[:16].replace('T', ' ')} UTC"
    ax.set_title(_title_base, color="#7788aa", fontsize=8, loc="left", pad=5)
    if entry_signal and entry_signal != "NO_TRADE" and direction:
        _dir_label   = "▲ LONG" if direction == "buy" else "▼ SHORT"
        _style_label = f" {trade_style}" if trade_style and trade_style != "NO_TRADE" else ""
        _badge_col   = "#00E676" if entry_signal == "ENTRY" else ("#FFD700" if entry_signal == "WAIT" else "#888888")
        ax.text(
            0.99, 0.97, f"{_dir_label}{_style_label}  [{entry_signal}]",
            transform=ax.transAxes, color=_badge_col, fontsize=9, fontweight="bold",
            va="top", ha="right", zorder=10,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#0b0d14", edgecolor=_badge_col, alpha=0.9),
        )

    plt.tight_layout(pad=0.4)

    import io as _io
    buf = _io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    from PIL import Image as _Image
    chart_img = _Image.open(buf).convert("RGB")
    chart_img.save(out_path, quality=92)
    print(f"Saved: {out_path}")


# ── Per-pair FAST/SWING parameters (walk-forward validated, March 2026) ───────

# ── Per-regime parameter packs ────────────────────────────────────────────────
# Structure: regimes → style → params.  Per-pair entries are sparse overrides.
# _mode_cfg() merges default + pair override at runtime.

_PAIR_PARAMS_DEFAULT = {
    "allowed_modes": ["FAST", "SWING"],
    "late_range": 7.0,
    "regimes": {
        "trending": {
            # Confirmed trend (ADX+DI on both TFs). bb_expanding NOT required —
            # in a mature trend bands are already wide; require minimum width instead.
            "fast": {
                "adx": 18, "vol": 0.8, "sl_k": 1.3,
                "bb_width_min": 0.5, "require_bb_expanding": False,
            },
            "swing": {
                "adx": 18, "vol": 0.8, "sl_k": 1.6,
                "bb_width_min": 0.8, "require_bb_expanding": False,
            },
        },
        "drift": {
            # Directional drift without full trend confirmation — volume matters more.
            "fast":  {"vol": 1.1, "sl_k": 1.3},
            "swing": {"vol": 1.1, "sl_k": 1.6},
        },
        "ranging": {
            # Mean-reversion — price at extremes, BB corridor, low ADX.
            "fast": {
                "vol": 1.5, "adx_max": 22,
                "buy_pos_max": 0.35, "sell_pos_min": 0.65,
                "bb_width_min": 0.8, "bb_width_max": 2.5, "sl_k": 1.3,
            },
        },
    },
}

_PAIR_PARAMS = {
    "BTC-USDT": {
        "late_range": 4.0,
        "allowed_modes": ["FAST", "SWING"],
        "regimes": {
            "trending": {
                "fast":  {"adx": 18, "vol": 0.7, "sl_k": 1.2, "bb_width_min": 0.4},
                "swing": {"adx": 18, "vol": 0.7, "sl_k": 1.6, "bb_width_min": 0.7},
            },
            "drift":   {"fast": {"vol": 1.2, "sl_k": 1.2}},
            "ranging": {"fast": {"vol": 1.3, "adx_max": 20,
                                 "buy_pos_max": 0.30, "sell_pos_min": 0.70, "sl_k": 1.2}},
        },
    },
    "ETH-USDT": {
        "late_range": 7.0,
        "allowed_modes": ["FAST", "SWING"],
        "regimes": {
            "trending": {
                "fast":  {"adx": 18, "vol": 0.6, "sl_k": 1.3, "bb_width_min": 0.3},
                "swing": {"adx": 18, "vol": 0.6, "sl_k": 1.6, "bb_width_min": 0.6},
            },
            "drift":   {"fast": {"vol": 1.3, "sl_k": 1.3}},
            "ranging": {"fast": {"vol": 1.4, "adx_max": 20,
                                 "buy_pos_max": 0.32, "sell_pos_min": 0.68, "sl_k": 1.3}},
        },
    },
    "SOL-USDT": {
        "late_range": 10.0,
        "allowed_modes": ["FAST", "SWING"],
        "regimes": {
            "trending": {
                "fast":  {"adx": 26, "vol": 1.5, "sl_k": 1.6, "bb_width_min": 0.8},
                "swing": {"adx": 28, "vol": 1.5, "sl_k": 1.9, "bb_width_min": 1.2},
            },
            "drift":   {"fast": {"vol": 2.0, "sl_k": 1.6}},
            "ranging": {"fast": {"vol": 2.0, "adx_max": 22,
                                 "buy_pos_max": 0.35, "sell_pos_min": 0.65, "sl_k": 1.6}},
        },
    },
    "XRP-USDT": {
        "late_range": 7.0,
        "allowed_modes": ["FAST", "SWING"],
        "regimes": {
            "trending": {
                "fast":  {"adx": 18, "vol": 0.8, "sl_k": 1.4, "bb_width_min": 0.4},
                "swing": {"adx": 18, "vol": 0.8, "sl_k": 1.8, "bb_width_min": 0.8},
            },
            "drift":   {"fast": {"vol": 1.3, "sl_k": 1.4}},
            "ranging": {"fast": {"vol": 1.4, "adx_max": 20,
                                 "buy_pos_max": 0.30, "sell_pos_min": 0.70, "sl_k": 1.4}},
        },
    },
    "ADA-USDT": {
        "late_range": 7.0,
        "allowed_modes": ["FAST", "SWING"],
        "regimes": {
            "trending": {
                "fast":  {"adx": 20, "vol": 0.8, "sl_k": 1.4, "bb_width_min": 0.5},
                "swing": {"adx": 18, "vol": 0.8, "sl_k": 1.8, "bb_width_min": 0.8},
            },
            "drift":   {"fast": {"vol": 1.1, "sl_k": 1.4}},
            "ranging": {"fast": {"vol": 1.5, "adx_max": 20,
                                 "buy_pos_max": 0.32, "sell_pos_min": 0.68, "sl_k": 1.4}},
        },
    },
    "DOGE-USDT": {
        "late_range": 7.0,
        "allowed_modes": ["FAST", "SWING"],
        "regimes": {
            "trending": {
                "fast":  {"adx": 18, "vol": 1.0, "sl_k": 1.4, "bb_width_min": 0.5},
                "swing": {"adx": 20, "vol": 1.0, "sl_k": 1.8, "bb_width_min": 0.8},
            },
            "drift":   {"fast": {"vol": 1.3, "sl_k": 1.4}},
            "ranging": {"fast": {"vol": 1.6, "adx_max": 22,
                                 "buy_pos_max": 0.35, "sell_pos_min": 0.65, "sl_k": 1.4}},
        },
    },
}


def _mode_cfg(pp: dict, regime: str, style: str) -> dict:
    """Resolve effective config for regime+style: default → pair sparse override."""
    base = dict(_PAIR_PARAMS_DEFAULT["regimes"].get(regime, {}).get(style, {}))
    base.update(pp.get("regimes", {}).get(regime, {}).get(style, {}))
    return base


# ── Regime detector ───────────────────────────────────────────────────────────

def _detect_regime(adx_1h: float, adx_4h: float, adx_4h_rising: bool,
                   di_spread_4h: float, di_spread_1h: float, bb_width: float) -> str:
    """TRENDING / DRIFT / RANGING / CHOPPY."""
    trend_4h = adx_4h >= 22 and di_spread_4h >= 10
    trend_1h = adx_1h >= 18 and di_spread_1h >= 8
    if trend_4h and trend_1h:
        return "TRENDING"
    drift = 12 <= adx_1h <= 26 and di_spread_1h >= 5
    if drift:
        return "DRIFT"
    if bb_width >= 3.0 and di_spread_1h < 6 and adx_4h < 22:
        return "CHOPPY"
    return "RANGING"


# ── Entry point ───────────────────────────────────────────────────────────────

async def run(
    symbol: str,
    captured_at_iso: str,
    limit: int,
    image_path: str = None,
    send_telegram: bool = False,
    output_dir: Path = None,
) -> None:
    api_key    = os.getenv("OKX_API_KEY", "")
    secret_key = os.getenv("OKX_SECRET_KEY", "")
    passphrase = os.getenv("OKX_PASSPHRASE", "")
    is_demo    = os.getenv("OKX_IS_DEMO", "1") == "1"

    client = OKXClient(api_key, secret_key, passphrase, is_demo)
    params = load_strategy_params()

    captured_ms = ts_to_ms(captured_at_iso)
    after_ts    = captured_ms + 1
    _is_live    = abs(datetime.now(timezone.utc).timestamp() * 1000 - captured_ms) <= 15 * 60 * 1000

    print(f"Fetching candles for {symbol} ending at {captured_at_iso} ...")
    try:
        (raw_4h, raw_1h, raw_15m, raw_5m, _funding, _oi, _oi_hist, _books5, _trades100,
         _raw_mark15, _raw_idx15) = await asyncio.gather(
            client.get_history_candles(symbol, "4H",  after=after_ts, limit=60),
            client.get_history_candles(symbol, "1H",  after=after_ts, limit=limit),
            client.get_history_candles(symbol, "15m", after=after_ts, limit=limit),
            client.get_history_candles(symbol, "5m",  after=after_ts, limit=limit),
            client.get_funding_rate(symbol),
            client.get_open_interest(symbol),
            client.get_oi_history(symbol, period="1H", limit=5),
            client.get_books(symbol, size=5) if _is_live else asyncio.sleep(0, result=None),
            client.get_trades(symbol, limit=100) if _is_live else asyncio.sleep(0, result=[]),
            client.get_history_mark_price_candles(symbol, "15m", after=after_ts, limit=10),
            client.get_history_index_candles(symbol, "15m", after=after_ts, limit=10),
        )
    finally:
        await client.close()

    if not raw_1h or not raw_15m or not raw_5m:
        print("ERROR: No candle data returned. Check symbol and captured-at timestamp.")
        return

    # Validate minimum bar counts — EMA50 needs 50+, ADX needs 14+, swing needs room
    if len(raw_1h) < 50 or len(raw_15m) < 50 or len(raw_5m) < 20:
        print(f"ERROR: Not enough candles — 1H:{len(raw_1h)} 15m:{len(raw_15m)} 5m:{len(raw_5m)} (need 50/50/20)")
        return

    c1h  = confirm_label(raw_1h)
    c15m = confirm_label(raw_15m)
    c5m  = confirm_label(raw_5m)
    print(f"Latest bar status:  1H={c1h}  15m={c15m}  5m={c5m}\n")

    # Funding/OI are live — disable for historical requests (>15min from now)
    if not _is_live:
        _funding = None
        _oi      = None
        _oi_hist = None
        _books5  = None
        _trades100 = []

    # ── Compute indicators ───────────────────────────────────────────────────
    result = compute_indicators(raw_1h, raw_15m, raw_5m, params, raw_4h=raw_4h)

    _h4  = result.get("4h", {})
    _h1  = result["1h"]
    _h15 = result["15m"]
    _h5  = result.get("5m", {})

    # ── Bias (EMA-only, no ADX requirement) ──────────────────────────────────
    _ema20_4h = float(_h4.get("ema20") or 0)
    _ema50_4h = float(_h4.get("ema50") or 0)
    _ema20_1h = float(_h1.get("ema20") or 0)
    _ema50_1h = float(_h1.get("ema50") or 0)
    _bias_4h  = "UP" if _ema20_4h > _ema50_4h > 0 else ("DOWN" if _ema20_4h < _ema50_4h else "NEUTRAL")
    _bias_1h  = "UP" if _ema20_1h > _ema50_1h > 0 else ("DOWN" if _ema20_1h < _ema50_1h else "NEUTRAL")

    _adx_1h      = float(_h1.get("adx") or 0)
    _adx_4h      = float(_h4.get("adx") or 0)
    _atr_15m     = float(_h15.get("atr") or 0)
    _close       = float(_h15.get("close") or 0)
    _rsi_15m     = float(_h15.get("rsi") or 50)
    _rsi_1h      = float(_h1.get("rsi") or 50)
    _plus_di_1h  = float(_h1.get("plus_di") or 0)
    _minus_di_1h = float(_h1.get("minus_di") or 0)
    _supertrend_dir = str(_h15.get("supertrend_dir") or "")
    _swing_highs = _h15.get("swing_highs", [])
    _swing_lows  = _h15.get("swing_lows",  [])

    # ── ATR 1H + ADX slope + DI spread ──────────────────────────────────────
    if raw_1h and len(raw_1h) >= 15:
        _highs_1h, _lows_1h, _closes_1h = parse_candles(raw_1h)
        _atr_1h = float(calc_atr(_highs_1h, _lows_1h, _closes_1h, period=14))
        _adx_1h_prev, _, _ = calc_adx(_highs_1h, _lows_1h, _closes_1h, period=14, bar_index=-2)
        _adx_1h_rising = _adx_1h > float(_adx_1h_prev)
        _di_spread_1h  = abs(_plus_di_1h - _minus_di_1h)
    else:
        _atr_1h        = _atr_15m * 4
        _adx_1h_rising = False
        _di_spread_1h  = 0.0

    # ── ADX 4H slope + DI spread ─────────────────────────────────────────────
    if raw_4h and len(raw_4h) >= 15:
        _highs_4h, _lows_4h, _closes_4h = parse_candles(raw_4h)
        _adx_4h_curr, _pdi_4h, _mdi_4h = calc_adx(_highs_4h, _lows_4h, _closes_4h, period=14, bar_index=-1)
        _adx_4h_prev, _, _              = calc_adx(_highs_4h, _lows_4h, _closes_4h, period=14, bar_index=-2)
        _adx_4h_rising = float(_adx_4h_curr) > float(_adx_4h_prev)
        _di_spread_4h  = abs(float(_pdi_4h) - float(_mdi_4h))
    else:
        _adx_4h_rising = False
        _di_spread_4h  = 0.0

    # ── Vol ratio (impulse: last 3 closed vs prior 15 on 15m) ───────────────
    # raw_15m is newest-first; [0] may be forming bar — skip it
    if raw_15m and len(raw_15m) >= 20:
        _vols_imp      = [float(c[5]) for c in raw_15m]   # newest-first
        _recent_imp    = float(np.mean(_vols_imp[1:4]))    # last 3 closed bars
        _prior_imp     = float(np.mean(_vols_imp[5:20]))   # prior 15 closed bars
        _vol_ratio_sig = _recent_imp / max(_prior_imp, 1e-9)
    else:
        _vol_ratio_sig = 1.0

    # ── OI delta ─────────────────────────────────────────────────────────────
    _oi_delta = 0.0
    if _oi_hist and len(_oi_hist) >= 2:
        def _parse_oi(e):
            if isinstance(e, dict):                        return float(e.get("oi", 0) or e.get("oiCcy", 0))
            if isinstance(e, (list, tuple)) and len(e) >= 2: return float(e[1])
            return 0.0
        _oic = _parse_oi(_oi_hist[0])
        _oip = _parse_oi(_oi_hist[1])
        if _oip > 0:
            _oi_delta = (_oic - _oip) / _oip

    # ── VWAP + day levels ────────────────────────────────────────────────────
    # raw_15m is newest-first; [0] may be forming — skip it for day level calcs
    _captured_dt  = datetime.fromisoformat(captured_at_iso.replace("Z", "+00:00"))
    _day_start_ms = int(datetime(_captured_dt.year, _captured_dt.month, _captured_dt.day,
                                  tzinfo=timezone.utc).timestamp() * 1000)
    _day_candles  = [c for c in raw_15m[1:] if int(c[0]) >= _day_start_ms]
    if len(_day_candles) < 4:
        _day_candles = []  # too few bars — day_position / VWAP / late_move unreliable
    if _day_candles:
        _dc_closes  = [float(c[4]) for c in _day_candles]
        _dc_vols    = [float(c[5]) for c in _day_candles]
        _dc_highs   = [float(c[2]) for c in _day_candles]
        _dc_lows    = [float(c[3]) for c in _day_candles]
        _vol_sum    = sum(_dc_vols)
        _vwap       = round(sum(c * v for c, v in zip(_dc_closes, _dc_vols)) / _vol_sum, 4) if _vol_sum > 0 else None
        _day_high   = round(max(_dc_highs), 4)
        _day_low    = round(min(_dc_lows),  4)
    else:
        _vwap = _day_high = _day_low = None

    if _day_high and _day_low and _day_high != _day_low and _close:
        _day_position = round((_close - _day_low) / (_day_high - _day_low), 3)
    else:
        _day_position = None

    # ── Session + daily range ─────────────────────────────────────────────────
    _signal_hour = _captured_dt.hour
    _is_night    = 1 <= _signal_hour < 7

    if _day_high and _day_low and _day_low > 0:
        _daily_range_pct = (_day_high - _day_low) / _day_low * 100
    else:
        _daily_range_pct = 0.0

    # ── BB expansion ─────────────────────────────────────────────────────────
    _bb_expanding = float(_h15.get("bb_width_pct") or 0) > 1.5

    # ── 4H context (SWING) + 5m trigger (FAST) ───────────────────────────────
    _h4_available    = bool(_h4)
    # SWING requires a live 4H trend and no direction conflict
    _adx_4h_ok       = (not _h4_available) or float(_adx_4h) >= 20
    _4h_dir_conflict = _h4_available and _bias_4h != "NEUTRAL" and _bias_4h != _bias_1h
    # FAST trigger: 5m EMA cross — bidirectional, no 1H bias lock
    _ema20_5m      = float(_h5.get("ema20") or 0)
    _rsi_5m        = float(_h5.get("rsi") or 50)
    _trigger_close = float(_h5.get("trigger_close") or 0)
    if _ema20_5m > 0:
        _five_m_long  = _trigger_close > _ema20_5m
        _five_m_short = _trigger_close < _ema20_5m
    else:
        _five_m_long  = True   # no 5m data — don't block
        _five_m_short = True

    # ── FAST / SWING engine (regime-based) ───────────────────────────────────
    if symbol not in _PAIR_PARAMS:
        raise ValueError(f"Unsupported symbol: {symbol}. Add to _PAIR_PARAMS before use.")
    _pp = _PAIR_PARAMS[symbol]
    _bb_width_pct = float(_h15.get("bb_width_pct") or 0)

    _regime = _detect_regime(_adx_1h, _adx_4h, _adx_4h_rising,
                             _di_spread_4h, _di_spread_1h, _bb_width_pct)
    # Downgrade TRENDING to RANGING when 4H/1H bias conflict — trend is transitional
    if _regime == "TRENDING" and _4h_dir_conflict:
        _regime = "RANGING"

    # DRIFT lower-bound filter: require ADX_1H >= 15 before allowing entry.
    # Hypothesis A: ADX_1H 12-14 is too weak — market is barely drifting,
    # entries produce mostly TIME_EXIT with no real directional follow-through.
    # Post-classification veto — regime label stays DRIFT, ENTRY blocked.
    _DRIFT_ADX1H_MIN = 15.0
    _drift_adx1h_veto = _regime == "DRIFT" and _adx_1h < _DRIFT_ADX1H_MIN

    _trade_style = "NO_TRADE"
    _side        = None
    _entry_cfg   = {}   # resolved regime+style params, used for SL/TP sl_k

    if _regime == "CHOPPY":
        pass  # no trade in chaotic market

    elif _regime == "TRENDING":
        # Each style has its own param pack — no hard bb_expanding gate.
        def _bb_ok(cfg: dict) -> bool:
            if _bb_width_pct < cfg.get("bb_width_min", 0.0):
                return False
            if cfg.get("require_bb_expanding", False) and not _bb_expanding:
                return False
            return True

        # SWING first (clean trend continuation)
        _cfg_sw = _mode_cfg(_pp, "trending", "swing")
        _swing_base = (
            _adx_1h >= _cfg_sw.get("adx", 18) and _adx_1h_rising
            and _vol_ratio_sig >= _cfg_sw["vol"] and _bb_ok(_cfg_sw)
            and not _4h_dir_conflict and _adx_4h_ok
            and _di_spread_4h >= 8 and _di_spread_1h >= 8
        )
        if "SWING" in _pp["allowed_modes"]:
            if _swing_base and _bias_1h == "UP":
                _trade_style, _side, _entry_cfg = "SWING", "buy", _cfg_sw
            elif _swing_base and _bias_1h == "DOWN":
                _trade_style, _side, _entry_cfg = "SWING", "sell", _cfg_sw

        # FAST as fallback
        _cfg_f = _mode_cfg(_pp, "trending", "fast")
        _fast_base = (
            _adx_1h >= _cfg_f.get("adx", 18) and _adx_1h_rising
            and _vol_ratio_sig >= _cfg_f["vol"] and _bb_ok(_cfg_f)
        )
        if "FAST" in _pp["allowed_modes"] and _trade_style == "NO_TRADE":
            if _fast_base and _five_m_long and _bias_1h == "UP":
                _trade_style, _side, _entry_cfg = "FAST", "buy", _cfg_f
            elif _fast_base and _five_m_short and _bias_1h == "DOWN":
                _trade_style, _side, _entry_cfg = "FAST", "sell", _cfg_f

    elif _regime == "RANGING":
        _cfg_r = _mode_cfg(_pp, "ranging", "fast")
        _bb_corridor = _cfg_r.get("bb_width_min", 0.8) <= _bb_width_pct <= _cfg_r.get("bb_width_max", 2.5)
        _ranging_base = (
            _adx_1h <= _cfg_r.get("adx_max", 22) and not _adx_1h_rising
            and _vol_ratio_sig >= _cfg_r["vol"]
            and _bb_corridor and _day_position is not None
        )
        _buy_pos_ok  = _day_position is not None and _day_position <= _cfg_r.get("buy_pos_max",  0.35)
        _sell_pos_ok = _day_position is not None and _day_position >= _cfg_r.get("sell_pos_min", 0.65)
        if "FAST" in _pp["allowed_modes"]:
            if _ranging_base and _five_m_long and _buy_pos_ok:
                _trade_style, _side, _entry_cfg = "FAST", "buy", _cfg_r
            elif _ranging_base and _five_m_short and _sell_pos_ok:
                _trade_style, _side, _entry_cfg = "FAST", "sell", _cfg_r

    elif _regime == "DRIFT":
        _cfg_d = _mode_cfg(_pp, "drift", "fast")
        _ema_slope_up = (
            len(_h1.get("ema20_series") or []) >= 4
            and _h1["ema20_series"][-1] > _h1["ema20_series"][-2]
            and _h1["ema20_series"][-2] > _h1["ema20_series"][-3]
            and _h1["ema20_series"][-3] > _h1["ema20_series"][-4]
        )
        _ema_slope_down = (
            len(_h1.get("ema20_series") or []) >= 4
            and _h1["ema20_series"][-1] < _h1["ema20_series"][-2]
            and _h1["ema20_series"][-2] < _h1["ema20_series"][-3]
            and _h1["ema20_series"][-3] < _h1["ema20_series"][-4]
        )
        _ema_drift_dir = "UP" if _ema_slope_up else ("DOWN" if _ema_slope_down else "FLAT")
        _drift_vwap_ok = (
            (_ema_drift_dir == "UP" and _close > _vwap)
            or (_ema_drift_dir == "DOWN" and _close < _vwap)
        ) if _vwap else False
        _drift_base = _ema_drift_dir != "FLAT" and _drift_vwap_ok and _vol_ratio_sig >= _cfg_d["vol"]
        if "FAST" in _pp["allowed_modes"]:
            if _drift_base and _five_m_long and _ema_drift_dir == "UP":
                _trade_style, _side, _entry_cfg = "FAST", "buy", _cfg_d
            elif _drift_base and _five_m_short and _ema_drift_dir == "DOWN":
                _trade_style, _side, _entry_cfg = "FAST", "sell", _cfg_d

    _strong_4h_veto = (
        _trade_style == "FAST"
        and _side is not None
        and _bias_4h != "NEUTRAL"
        and _di_spread_4h >= 8
        and ((_side == "buy" and _bias_4h == "DOWN")
             or (_side == "sell" and _bias_4h == "UP"))
    )
    if _strong_4h_veto:
        _trade_style, _side = "NO_TRADE", None

    # Night session — no hard block, disclaimer added to client summary

    # Late-move veto — symmetric for long and short
    if _day_position is not None and _daily_range_pct > _pp["late_range"]:
        if _side == "buy"  and _day_position > 0.90:
            _trade_style, _side = "NO_TRADE", None
        if _side == "sell" and _day_position < 0.10:
            _trade_style, _side = "NO_TRADE", None

    # 4H veto — informational only
    _4h_veto = _4h_dir_conflict or _strong_4h_veto

    # VWAP filter — regime-aware
    # TRENDING: skip — ADX+DI already confirm direction; VWAP lags in strong moves.
    # DRIFT:    trend-follow (buy above, sell below).
    # RANGING:  fade (buy below, sell above).
    _vwap_ok = True
    if _vwap and _close and _side and _regime != "TRENDING":
        if _regime == "RANGING":
            if _side == "buy"  and _close > _vwap: _vwap_ok = False
            if _side == "sell" and _close < _vwap: _vwap_ok = False
        elif _regime == "DRIFT":
            if _side == "buy"  and _close < _vwap: _vwap_ok = False
            if _side == "sell" and _close > _vwap: _vwap_ok = False

    # Funding (side-aware, 0.05% threshold)
    _funding_val  = _funding if _funding is not None else 0.0
    _FUND_THRESH  = 0.0005
    _funding_block = ((_side == "buy"  and _funding_val >  _FUND_THRESH) or
                      (_side == "sell" and _funding_val < -_FUND_THRESH))
    _funding_warn  = not _funding_block and abs(_funding_val) > _FUND_THRESH * 0.5

    # OI weak
    _oi_weak = _oi_delta < -0.03

    # SL / TP
    _sl_p = _tp1_p = _tp2_p = None
    _sl_dist = 0.0

    if _trade_style == "FAST" and _side and _close:
        _sl_k = _entry_cfg.get("sl_k", 1.4)
        _atr_sl_dist = max(_sl_k * _atr_15m, _close * 0.004)
        if _side == "buy":
            _atr_sl   = _close - _atr_sl_dist
            _struct   = (_swing_lows[-1]  - 0.2 * _atr_15m) if _swing_lows  else None
            _sl_p     = round(min(_struct, _atr_sl) if _struct and _struct < _close else _atr_sl, 4)
            _sl_dist  = _close - _sl_p
            _tp1_p    = round(_close + _sl_dist * 0.8, 4)
            _tp2_p    = round(_close + _sl_dist * 1.5, 4)
        else:
            _atr_sl   = _close + _atr_sl_dist
            _struct   = (_swing_highs[-1] + 0.2 * _atr_15m) if _swing_highs else None
            _sl_p     = round(max(_struct, _atr_sl) if _struct and _struct > _close else _atr_sl, 4)
            _sl_dist  = _sl_p - _close
            _tp1_p    = round(_close - _sl_dist * 0.8, 4)
            _tp2_p    = round(_close - _sl_dist * 1.5, 4)

    elif _trade_style == "SWING" and _side and _close:
        _sl_k = _entry_cfg.get("sl_k", 1.8)
        if _side == "buy":
            _atr_sl  = _close - _sl_k * _atr_15m
            _struct  = (_swing_lows[-1] - 0.3 * _atr_15m) if _swing_lows else None
            _sl_p    = round(min(_struct, _atr_sl) if _struct else _atr_sl, 4)
            _sl_dist = _close - _sl_p
            _tp1_p   = round(_close + min(_sl_dist * 1.0, _atr_1h * 0.5), 4)
            _tp2_p   = round(_close + min(_sl_dist * 2.5, _atr_1h * 1.2), 4)
        else:
            _atr_sl  = _close + _sl_k * _atr_15m
            _struct  = (_swing_highs[-1] + 0.3 * _atr_15m) if _swing_highs else None
            _sl_p    = round(max(_struct, _atr_sl) if _struct else _atr_sl, 4)
            _sl_dist = _sl_p - _close
            _tp1_p   = round(_close - min(_sl_dist * 1.0, _atr_1h * 0.5), 4)
            _tp2_p   = round(_close - min(_sl_dist * 2.5, _atr_1h * 1.2), 4)

    _max_hold_minutes = (240 if _is_night else 120) if _trade_style == "FAST" else 240

    # R/R validation — block ENTRY if TP2 doesn't cover 0.8× SL distance
    _rr_ok = True
    if _sl_p and _tp2_p and _sl_dist > 0:
        _rr2 = abs(_tp2_p - _close) / _sl_dist
        if _rr2 < 0.8:
            _rr_ok = False

    # perp_div_4bar: futures 1h change vs index 1h change
    # Positive = futures running ahead of spot (overheated).
    # Backtest: SHORT + perp_div > 0 → WR 66% vs 79% when div < 0.
    _perp_div_4bar = None
    if raw_15m and len(raw_15m) >= 5 and _raw_idx15 and len(_raw_idx15) >= 5:
        try:
            perp_now   = float(raw_15m[0][4])
            perp_4back = float(raw_15m[4][4])
            idx_now    = float(_raw_idx15[0][4])
            idx_4back  = float(_raw_idx15[4][4])
            if perp_4back > 0 and idx_4back > 0:
                _perp_div_4bar = (
                    (perp_now - perp_4back) / perp_4back * 100
                    - (idx_now - idx_4back) / idx_4back * 100
                )
        except (IndexError, ValueError, ZeroDivisionError):
            pass

    _perp_div_short_veto = (
        _side == "sell"
        and _perp_div_4bar is not None
        and _perp_div_4bar > 0.0
    )

    # Final entry signal — funding_block = hard NO_TRADE, not WAIT
    if (_trade_style == "NO_TRADE" or not _vwap_ok or _oi_weak
            or _funding_block or not _rr_ok or not _sl_p or not _tp1_p
            or _perp_div_short_veto or _drift_adx1h_veto):
        _entry_signal = "NO_TRADE"
    elif _funding_warn:
        _entry_signal = "WAIT"
    else:
        _entry_signal = "ENTRY"

    _micro = _build_micro_snapshot(_books5, _trades100)

    # ── Engine vars dict ──────────────────────────────────────────────────────
    engine_vars = {
        "trade_style":   _trade_style,
        "entry_signal":  _entry_signal,
        "side":          _side,
        "bias_1h":       _bias_1h,
        "bias_4h":       _bias_4h,
        "adx_1h":        _adx_1h,
        "adx_1h_rising": _adx_1h_rising,
        "vol_ratio_sig": _vol_ratio_sig,
        "bb_expanding":  _bb_expanding,
        "vwap_ok":       _vwap_ok,
        "four_h_veto":   _4h_veto,
        "oi_weak":       _oi_weak,
        "oi_delta":      _oi_delta,
        "is_night":      _is_night,
        "funding_warn":  _funding_warn,
        "funding_block": _funding_block,
        "funding_val":   _funding_val,
        "close":         _close,
        "sl_p":          _sl_p,
        "tp1_p":         _tp1_p,
        "tp2_p":         _tp2_p,
        "vwap":          _vwap,
        "day_high":      _day_high,
        "day_low":       _day_low,
        "max_hold_minutes": _max_hold_minutes,
        "daily_range_pct":   _daily_range_pct,
        "day_position":      _day_position,
        "rsi_1h":            _rsi_1h,
        "rsi_15m":           _rsi_15m,
        "adx_4h":            _adx_4h,
        "four_h_conflict":   _4h_dir_conflict,
        "adx_4h_ok":         _adx_4h_ok,
        "five_m_trigger":    (_five_m_long if _side == "buy" else _five_m_short) if _side else True,
        "rsi_5m":            _rsi_5m,
        "regime":            _regime,
        "di_spread_1h":      round(_di_spread_1h, 1),
        "di_spread_4h":      round(_di_spread_4h, 1),
        "adx_4h_rising":     _adx_4h_rising,
        "strong_4h_veto":       _strong_4h_veto,
        "perp_div_4bar":        round(_perp_div_4bar, 4) if _perp_div_4bar is not None else None,
        "perp_div_short_veto":  _perp_div_short_veto,
        "drift_adx1h_veto":     _drift_adx1h_veto,
        "micro":                _micro,
    }

    # ── Build texts ───────────────────────────────────────────────────────────
    engine_summary = build_engine_summary(symbol, captured_at_iso, engine_vars)
    report_text    = format_report(symbol, captured_at_iso, result, engine_vars)

    print(report_text)
    print("\n── ENGINE SUMMARY " + "─" * 44)
    print(engine_summary)

    # ── Save outputs ──────────────────────────────────────────────────────────
    ts_label = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    if output_dir is not None:
        run_dir = Path(output_dir)
    else:
        run_dir = Path(__file__).parent / "analysis_output" / ts_label
    run_dir.mkdir(parents=True, exist_ok=True)

    report_path = run_dir / f"{symbol}_report.md"
    snap_path   = run_dir / f"{symbol}_snapshot.json"
    png_path    = run_dir / f"{symbol}_chart.png"

    report_path.write_text(report_text + "\n", encoding="utf-8")

    # Expiry
    _tf_exp     = 5 if _entry_signal == "ENTRY" else (15 if _entry_signal == "WAIT" else 60)
    expiry_time = _next_candle_close(captured_at_iso, _tf_exp)

    snapshot = {
        "symbol":      symbol,
        "captured_at": captured_at_iso,
        "expiry_time": expiry_time,
        "llm_context": {
            "bias_4h":           _bias_4h,
            "bias_1h":           _bias_1h,
            "adx_1h":            round(_adx_1h, 1),
            "adx_4h":            round(_adx_4h, 1),
            "adx_1h_rising":     _adx_1h_rising,
            "plus_di_1h":        round(_plus_di_1h, 1),
            "minus_di_1h":       round(_minus_di_1h, 1),
            "supertrend_dir":    _supertrend_dir,
            "rsi_1h":            _h1.get("rsi"),
            "rsi_15m":           _h15.get("rsi"),
            "volume_ratio_15m":  round(_vol_ratio_sig, 2),
            "bb_width_15m":      _h15.get("bb_width_pct"),
            "bb_expanding":      _bb_expanding,
            "day_position":      _day_position,
            "trade_style_hint":  _trade_style,
            "oi_delta":          round(_oi_delta, 4),
            "entry_signal":      _entry_signal,
            "funding_rate":      round(_funding_val, 6),
            "funding_blocked":   bool(_funding_block),
            "open_interest":     round(_oi, 0) if _oi is not None else None,
            "vwap_day":          _vwap,
            "day_high":          _day_high,
            "day_low":           _day_low,
            "atr_1h":            round(_atr_1h, 4),
            "atr_15m":           round(_atr_15m, 4),
            "side":              _side,
            "entry_price":       _close if _sl_p else None,
            "sl_price":          _sl_p,
            "tp1_price":         _tp1_p,
            "tp2_price":         _tp2_p,
            "max_hold_minutes":  _max_hold_minutes,
            "daily_range_pct":   round(_daily_range_pct, 2),
            "is_night_session":  _is_night,
            "adx_4h_ok":         _adx_4h_ok,
            "four_h_conflict":   _4h_dir_conflict,
            "five_m_trigger":    (_five_m_long if _side == "buy" else _five_m_short) if _side else True,
            "regime":            _regime,
            "strong_4h_veto":    _strong_4h_veto,
            "obi_top5":          _micro.get("obi_top5"),
            "trade_delta_100":   _micro.get("trade_delta_100"),
            "spread_bps":        _micro.get("spread_bps"),
        },
        "microstructure": _micro,
        "4h":          result.get("4h", {}),
        "1h":          result["1h"],
        "15m":         result["15m"],
        "5m":          result["5m"],
        "trader_notes": [],
    }
    snap_path.write_text(
        json.dumps(_json_safe(snapshot), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\nSaved: {report_path}")
    print(f"Saved: {snap_path}")

    # ── Chart ─────────────────────────────────────────────────────────────────
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

    # ── LLM (ENTRY/WAIT only) ─────────────────────────────────────────────────
    llm_text = None
    if _entry_signal in ("ENTRY", "WAIT"):
        from src.utils.llm_formatter import generate_client_text
        llm_image = str(png_path) if png_path.exists() else image_path
        llm_text  = await generate_client_text(
            symbol, captured_at_iso, snapshot, llm_image, client_summary=None
        )

    delivery_text = llm_text if llm_text else engine_summary

    # Save delivery text
    summary_path = run_dir / f"{symbol}_client_summary.txt"
    summary_path.write_text(delivery_text, encoding="utf-8")
    print(f"Saved: {summary_path}")

    # ── Telegram ──────────────────────────────────────────────────────────────
    if send_telegram:
        from src.utils.telegram import send_message
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip("'\"")
        tg_chat  = os.getenv("TELEGRAM_CHAT_ID",    "").strip()
        if not tg_token or not tg_chat:
            print("Telegram: not sent — TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        else:
            import html as _html
            tg_text = _html.escape(delivery_text) if llm_text else _format_telegram(delivery_text)
            await send_message(tg_text)
            if image_path and os.path.exists(image_path):
                from src.utils.telegram import send_photo_to
                await send_photo_to(tg_chat, image_path)
            print("Telegram: sent.")

    print(f"\nРезультаты: {run_dir}")

    return {
        "entry_signal":  _entry_signal,
        "trade_style":   _trade_style,
        "side":          _side,
        "symbol":        symbol,
        "expiry_time":   expiry_time,
        "delivery_text": delivery_text,
        "entry_price":   _close,
        "sl_price":      _sl_p,
        "tp1_price":     _tp1_p,
        "max_hold_min":  _max_hold_minutes,
        "strong_4h_veto": _strong_4h_veto,
        "microstructure": _micro,
        # signal log context (used by telegram_bot signal_log.jsonl)
        "regime":        _regime,
        "adx_1h":        round(float(_adx_1h), 1),
        "adx_4h":        round(float(_adx_4h), 1),
        "day_position":  _day_position,
        "vol_ratio":     round(float(_vol_ratio_sig), 3),
        "funding":       round(float(_funding_val), 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chart Analyzer — FAST/SWING engine + OKX data"
    )
    parser.add_argument("--symbol",       required=True,  help="e.g. XRP-USDT")
    parser.add_argument("--captured-at",  required=True,  dest="captured_at",
                        help="ISO UTC timestamp e.g. 2026-03-09T11:42:35Z")
    parser.add_argument("--image",        default=None,   help="Path to screenshot (optional)")
    parser.add_argument("--limit",        type=int, default=100,
                        help="Candles to fetch per timeframe (default 100)")
    parser.add_argument("--send-telegram", action="store_true", dest="send_telegram",
                        help="Send client summary to Telegram after analysis")
    args = parser.parse_args()
    asyncio.run(run(args.symbol, args.captured_at, args.limit, args.image, args.send_telegram))


if __name__ == "__main__":
    main()
