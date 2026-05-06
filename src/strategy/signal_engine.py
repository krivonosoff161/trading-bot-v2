"""Pure signal-engine logic extracted from scripts.analyze_chart."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from src.strategy.indicators import (
    atr_regime,
    calc_adx,
    calc_atr,
    calc_bollinger_bands,
    calc_chandelier_exit,
    calc_ema,
    calc_rsi,
    calc_slope,
    calc_supertrend,
    find_swing_levels,
    parse_candles,
)

_MSK_OFFSET = 3  # UTC+3, no DST


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


def _fmt_price(symbol: str, price) -> str:
    if price is None:
        return "—"
    base = symbol.split("-")[0].upper()
    if base == "BTC":
        return f"{float(price):.1f}"
    if base in ("ETH", "SOL"):
        return f"{float(price):.2f}"
    return f"{float(price):.4f}"


def _to_msk(dt) -> "datetime":
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


def compute_indicators(
    raw_1h: list,
    raw_15m: list,
    raw_5m: list,
    params: dict,
    raw_4h: list | None = None,
) -> dict:
    """Compute all indicators across timeframes. No signal, no action."""
    ema_fast = int(params["ema_fast"])
    ema_slow = int(params["ema_slow"])
    adx_period = int(params["adx_period"])

    h4_data: dict = {}
    if raw_4h:
        highs_4h, lows_4h, closes_4h = parse_candles(raw_4h)
        adx_4h, plus_di_4h, minus_di_4h = calc_adx(highs_4h, lows_4h, closes_4h, period=adx_period, bar_index=-2)
        ema20_4h = calc_ema(closes_4h, ema_fast)
        ema50_4h = calc_ema(closes_4h, ema_slow)
        bb_4h = calc_bollinger_bands(closes_4h, period=20, std_mult=2.5)
        h4_data = {
            "ema20": round(float(ema20_4h[-2]), 6),
            "ema50": round(float(ema50_4h[-2]), 6),
            "adx": round(adx_4h, 1),
            "plus_di": round(plus_di_4h, 1),
            "minus_di": round(minus_di_4h, 1),
            "bb_upper": bb_4h["upper"],
            "bb_lower": bb_4h["lower"],
            "bb_width": bb_4h["width_pct"],
        }

    highs_1h, lows_1h, closes_1h = parse_candles(raw_1h)
    adx, plus_di, minus_di = calc_adx(highs_1h, lows_1h, closes_1h, period=adx_period, bar_index=-2)
    ema20_1h = calc_ema(closes_1h, ema_fast)
    ema50_1h = calc_ema(closes_1h, ema_slow)
    atr_1h = calc_atr(highs_1h, lows_1h, closes_1h, period=adx_period)
    rsi_1h = calc_rsi(closes_1h, period=14)
    bb_1h = calc_bollinger_bands(closes_1h, period=20, std_mult=2.0)
    ce_1h = calc_chandelier_exit(highs_1h, lows_1h, closes_1h, lookback=22, multiplier=3.5)

    bull_1h = float(ema20_1h[-2]) > float(ema50_1h[-2])
    bear_1h = float(ema20_1h[-2]) < float(ema50_1h[-2])

    highs_15m, lows_15m, closes_15m = parse_candles(raw_15m)
    atr_15m = calc_atr(highs_15m, lows_15m, closes_15m, period=adx_period)
    ema20_15m = calc_ema(closes_15m, ema_fast)
    ema50_15m = calc_ema(closes_15m, ema_slow)
    rsi_15m = calc_rsi(closes_15m, period=14)
    bb_15m = calc_bollinger_bands(closes_15m, period=20, std_mult=2.0)
    swings_15m = find_swing_levels(highs_15m, lows_15m, lookback=3, count=4)
    atr_pct, atr_lbl = atr_regime(highs_15m, lows_15m, closes_15m, period=adx_period)
    supertrend_15m = calc_supertrend(highs_15m, lows_15m, closes_15m, period=14, multiplier=3.0)
    cur_close = closes_15m[-2]

    highs_5m, lows_5m, closes_5m = parse_candles(raw_5m)
    ema20_5m = calc_ema(closes_5m, ema_fast)
    rsi_5m = calc_rsi(closes_5m, period=14)

    return {
        "1h": {
            "ema20": round(float(ema20_1h[-2]), 6),
            "ema50": round(float(ema50_1h[-2]), 6),
            "ema20_series": [round(float(x), 6) for x in ema20_1h[-6:]],
            "adx": round(adx, 2),
            "plus_di": round(plus_di, 2),
            "minus_di": round(minus_di, 2),
            "atr": round(float(atr_1h), 6),
            "rsi": round(rsi_1h, 1),
            "bb_width_pct": bb_1h["width_pct"],
            "bull": bull_1h,
            "bear": bear_1h,
        },
        "15m": {
            "close": round(float(cur_close), 6),
            "ema20": round(float(ema20_15m[-2]), 6),
            "ema50": round(float(ema50_15m[-2]), 6),
            "atr": round(float(atr_15m), 6),
            "rsi": round(rsi_15m, 1),
            "atr_pct": atr_pct,
            "atr_label": atr_lbl,
            "swing_highs": swings_15m["recent_highs"],
            "swing_lows": swings_15m["recent_lows"],
            "bb_upper": bb_15m["upper"],
            "bb_middle": bb_15m["middle"],
            "bb_lower": bb_15m["lower"],
            "bb_pct_b": bb_15m["pct_b"],
            "bb_width_pct": bb_15m["width_pct"],
            "supertrend": supertrend_15m["value"],
            "supertrend_dir": supertrend_15m["direction"],
            "supertrend_dist": supertrend_15m["distance_pct"],
            "ce_long": ce_1h["ce_long"],
            "ce_short": ce_1h["ce_short"],
        },
        "5m": {
            "trigger_close": round(float(closes_5m[-2]), 6),
            "ema20": round(float(ema20_5m[-2]), 6),
            "rsi": round(rsi_5m, 1),
        },
        "4h": h4_data,
    }


def _format_telegram(text: str) -> str:
    return f"<pre>{text}</pre>"


_PAIR_PARAMS_DEFAULT = {
    "allowed_modes": ["FAST", "SWING"],
    "late_range": 7.0,
    "regimes": {
        "trending": {
            "fast": {
                "adx": 18,
                "vol": 0.8,
                "sl_k": 1.3,
                "bb_width_min": 0.5,
                "require_bb_expanding": False,
            },
            "swing": {
                "adx": 18,
                "vol": 0.8,
                "sl_k": 1.6,
                "bb_width_min": 0.8,
                "require_bb_expanding": False,
            },
        },
        "drift": {
            "fast": {"vol": 1.1, "sl_k": 1.3},
            "swing": {"vol": 1.1, "sl_k": 1.6},
        },
        "ranging": {
            "fast": {
                "vol": 1.5,
                "adx_max": 22,
                "buy_pos_max": 0.35,
                "sell_pos_min": 0.65,
                "bb_width_min": 0.8,
                "bb_width_max": 2.5,
                "sl_k": 1.3,
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
                "fast": {"adx": 18, "vol": 0.7, "sl_k": 1.2, "bb_width_min": 0.4},
                "swing": {"adx": 18, "vol": 0.7, "sl_k": 1.6, "bb_width_min": 0.7},
            },
            "drift": {"fast": {"vol": 1.2, "sl_k": 1.2}},
            "ranging": {"fast": {"vol": 1.3, "adx_max": 20, "buy_pos_max": 0.30, "sell_pos_min": 0.70, "sl_k": 1.2}},
        },
    },
    "ETH-USDT": {
        "late_range": 7.0,
        "allowed_modes": ["FAST", "SWING"],
        "drift_bias_check": True,
        "regimes": {
            "trending": {
                "fast": {"adx": 22, "vol": 1.0, "sl_k": 1.3, "bb_width_min": 0.7},
                "swing": {"adx": 22, "vol": 1.0, "sl_k": 1.6, "bb_width_min": 0.9},
            },
            "drift": {"fast": {"vol": 1.8, "sl_k": 1.3}},
            "ranging": {"fast": {"vol": 1.4, "adx_max": 20, "buy_pos_max": 0.32, "sell_pos_min": 0.68, "sl_k": 1.3}},
        },
    },
    "SOL-USDT": {
        "late_range": 10.0,
        "allowed_modes": ["FAST", "SWING"],
        "regimes": {
            "trending": {
                "fast": {"adx": 26, "vol": 1.5, "sl_k": 1.6, "bb_width_min": 0.8},
                "swing": {"adx": 28, "vol": 1.5, "sl_k": 1.9, "bb_width_min": 1.2},
            },
            "drift": {"fast": {"vol": 2.0, "sl_k": 1.6}},
            "ranging": {"fast": {"vol": 2.0, "adx_max": 22, "buy_pos_max": 0.35, "sell_pos_min": 0.65, "sl_k": 1.6}},
        },
    },
    "XRP-USDT": {
        "late_range": 7.0,
        "allowed_modes": ["FAST", "SWING"],
        "regimes": {
            "trending": {
                "fast": {"adx": 18, "vol": 0.8, "sl_k": 1.4, "bb_width_min": 0.4},
                "swing": {"adx": 18, "vol": 0.8, "sl_k": 1.8, "bb_width_min": 0.8},
            },
            "drift": {"fast": {"vol": 1.3, "sl_k": 1.4}},
            "ranging": {"fast": {"vol": 1.4, "adx_max": 20, "buy_pos_max": 0.30, "sell_pos_min": 0.70, "sl_k": 1.4}},
        },
    },
    "ADA-USDT": {
        "late_range": 7.0,
        "allowed_modes": ["FAST", "SWING"],
        "regimes": {
            "trending": {
                "fast": {"adx": 20, "vol": 0.8, "sl_k": 1.4, "bb_width_min": 0.5},
                "swing": {"adx": 18, "vol": 0.8, "sl_k": 1.8, "bb_width_min": 0.8},
            },
            "drift": {"fast": {"vol": 1.1, "sl_k": 1.4}},
            "ranging": {"fast": {"vol": 1.5, "adx_max": 20, "buy_pos_max": 0.32, "sell_pos_min": 0.68, "sl_k": 1.4}},
        },
    },
    "DOGE-USDT": {
        "late_range": 7.0,
        "allowed_modes": ["FAST", "SWING"],
        "regimes": {
            "trending": {
                "fast": {"adx": 18, "vol": 1.0, "sl_k": 1.4, "bb_width_min": 0.5},
                "swing": {"adx": 20, "vol": 1.0, "sl_k": 1.8, "bb_width_min": 0.8},
            },
            "drift": {"fast": {"vol": 1.3, "sl_k": 1.4}},
            "ranging": {"fast": {"vol": 1.6, "adx_max": 22, "buy_pos_max": 0.35, "sell_pos_min": 0.65, "sl_k": 1.4}},
        },
    },
}


def _mode_cfg(pp: dict, regime: str, style: str) -> dict:
    """Resolve effective config for regime+style."""
    base = dict(_PAIR_PARAMS_DEFAULT["regimes"].get(regime, {}).get(style, {}))
    base.update(pp.get("regimes", {}).get(regime, {}).get(style, {}))
    return base


def _detect_regime(adx_1h: float, adx_4h: float, adx_4h_rising: bool, di_spread_4h: float, di_spread_1h: float, bb_width: float) -> str:
    trend_4h = adx_4h >= 22 and di_spread_4h >= 10
    trend_1h = adx_1h >= 18 and di_spread_1h >= 8
    if trend_4h and trend_1h:
        return "TRENDING"
    drift = 12 <= adx_1h <= 30 and di_spread_1h >= 5
    if drift:
        return "DRIFT"
    if bb_width >= 3.0 and di_spread_1h < 6 and adx_4h < 22:
        return "CHOPPY"
    return "RANGING"


@dataclass
class SignalResult:
    symbol: str
    captured_at: str
    entry_signal: str
    side: str | None
    trade_style: str | None
    regime: str
    entry_price: float | None
    sl_price: float | None
    tp1_price: float | None
    tp2_price: float | None
    max_hold_min: int | None
    drop_reason: str
    context: dict = field(default_factory=dict)
    indicators: dict = field(default_factory=dict)
    engine_vars: dict = field(default_factory=dict)
    microstructure: dict = field(default_factory=dict)
    engine_summary: str = ""
    report_text: str = ""
    five_m_fade_hint: str | None = None
    five_m_fade_data: dict = field(default_factory=dict)

    @property
    def expiry_time(self) -> str:
        tf_exp = 5 if self.entry_signal == "ENTRY" else (15 if self.entry_signal == "WAIT" else 60)
        return _next_candle_close(self.captured_at, tf_exp)

    def to_legacy_result(self, delivery_text: str) -> dict:
        return {
            "entry_signal": self.entry_signal,
            "trade_style": self.trade_style,
            "side": self.side,
            "symbol": self.symbol,
            "expiry_time": self.expiry_time,
            "delivery_text": delivery_text,
            "5m_fade_hint": self.five_m_fade_hint,
            "5m_fade_data": self.five_m_fade_data,
            "entry_price": self.entry_price,
            "sl_price": self.sl_price,
            "tp1_price": self.tp1_price,
            "max_hold_min": self.max_hold_min,
            "strong_4h_veto": self.engine_vars.get("strong_4h_veto"),
            "microstructure": self.microstructure,
            "regime": self.regime,
            "adx_1h": round(float(self.engine_vars.get("adx_1h", 0)), 1),
            "adx_4h": round(float(self.engine_vars.get("adx_4h", 0)), 1),
            "day_position": self.engine_vars.get("day_position"),
            "vol_ratio": round(float(self.engine_vars.get("vol_ratio_sig", 0)), 3),
            "funding": round(float(self.engine_vars.get("funding_val", 0)), 6),
            "slope_1h": round(float(self.context.get("slope_1h", 0)), 1),
            "slope_15m": round(float(self.context.get("slope_15m", 0)), 1),
        }


def _resolve_drop_reason(entry_signal: str, engine_vars: dict) -> str:
    if entry_signal == "WAIT":
        return "funding_warn"
    checks = [
        ("drift_short_veto", "drift_short_veto"),
        ("strong_4h_veto", "strong_4h_veto"),
        ("four_h_conflict", "four_h_conflict"),
        ("perp_div_short_veto", "perp_div_short_veto"),
        ("drift_adx1h_veto", "drift_adx1h_veto"),
        ("oi_weak", "oi_weak"),
        ("funding_block", "funding_block"),
    ]
    for key, reason in checks:
        if engine_vars.get(key):
            return reason
    if not engine_vars.get("vwap_ok", True):
        return "vwap_veto"
    if not engine_vars.get("sl_p") or not engine_vars.get("tp1_p"):
        return "missing_levels"
    if engine_vars.get("entry_signal") == "NO_TRADE" and engine_vars.get("trade_style") == "NO_TRADE":
        return "conditions_not_met"
    return ""


def build_engine_summary(symbol: str, captured_at: str, eng: dict) -> str:
    trade_style = eng["trade_style"]
    entry_signal = eng["entry_signal"]
    side = eng["side"]
    bias_1h = eng["bias_1h"]
    adx_1h = eng["adx_1h"]
    adx_rising = eng["adx_1h_rising"]
    vol_ratio = eng["vol_ratio_sig"]
    bb_expanding = eng["bb_expanding"]
    vwap_ok = eng["vwap_ok"]
    oi_weak = eng["oi_weak"]
    is_night = eng["is_night"]
    funding_warn = eng["funding_warn"]
    funding_block = eng["funding_block"]
    funding_val = eng["funding_val"]
    close = eng["close"]
    sl_p = eng["sl_p"]
    tp1_p = eng["tp1_p"]
    tp2_p = eng["tp2_p"]
    vwap = eng["vwap"]
    day_high = eng["day_high"]
    day_low = eng["day_low"]
    max_hold = eng["max_hold_minutes"]
    daily_range_pct = eng.get("daily_range_pct", 0.0)
    day_position = eng.get("day_position")
    bb_width_pct = eng.get("bb_width_15m", 0.0)
    rsi_1h = eng.get("rsi_1h", 50.0)
    four_h_conflict = eng.get("four_h_conflict", False)
    adx_4h_ok = eng.get("adx_4h_ok", True)
    five_m_trigger = eng.get("five_m_trigger", True)
    adx_4h = eng.get("adx_4h", 0.0)
    regime = eng.get("regime", "RANGING")

    try:
        dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        ts_str = _to_msk(dt).strftime("%d %b %Y  %H:%M МСК")
    except Exception:
        ts_str = captured_at

    status_map = {"ENTRY": "ВХОД", "WAIT": "НАБЛЮДАЕМ", "NO_TRADE": "ВНЕ РЫНКА"}
    status = status_map.get(entry_signal, "ВНЕ РЫНКА")
    style_map = {"FAST": "⚡ БЫСТРЫЙ — закрыть в течение 2 часов", "SWING": "📈 СВИНГ — держать до 4 часов"}
    type_str = style_map.get(trade_style, "")

    if side == "buy":
        direction_text = "только LONG — короткая сторона не рассматривается"
    elif side == "sell":
        direction_text = "только SHORT — длинная сторона не рассматривается"
    else:
        direction_text = "направления нет — ни LONG, ни SHORT не рассматриваются"

    fp = lambda p: _fmt_price(symbol, p)
    sep = "═" * 46
    lines = [sep, f"  {symbol}  |  {ts_str}", sep, "", f"  Статус:      {status}"]
    if type_str and entry_signal != "NO_TRADE":
        lines.append(f"  Тип:         {type_str}")
    lines += [f"  Направление: {direction_text}", ""]

    if entry_signal in ("ENTRY", "WAIT"):
        direction_word = "вверх" if side == "buy" else "вниз"
        long_short = "ЛОНГ" if side == "buy" else "ШОРТ"
        lines.append("📊 СЕЙЧАС НА РЫНКЕ")
        if regime == "RANGING":
            zone = "дна" if side == "buy" else "вершины"
            expect = "отскок вверх" if side == "buy" else "откат вниз"
            lines.append(f"  Рынок в диапазоне. Цена у {zone} дневного диапазона — ожидаем {expect} к VWAP.")
        else:
            lines.append("  Тренд 1H направлен " + direction_word + " и набирает силу — объём импульса подтверждает движение.")
        if vwap and close:
            vwap_word = "выше" if float(close) >= float(vwap) else "ниже"
            control = "покупатели" if side == "buy" else "продавцы"
            lines.append(f"  Цена {vwap_word} дневного уровня равновесия ({fp(vwap)}) — {control} контролируют день.")
        if day_high and day_low:
            range_note = ""
            if daily_range_pct >= 20:
                range_note = f" ⚠️ Волатильность экстремальная ({daily_range_pct:.1f}% дня)."
            elif daily_range_pct >= 8:
                range_note = f" Широкий день ({daily_range_pct:.1f}%)."
            lines.append(f"  Диапазон дня: {fp(day_low)} — {fp(day_high)}.{range_note}")
        if day_position is not None:
            if day_position <= 0.15:
                lines.append(f"  Цена у дна дня ({int(day_position * 100)}%) — потенциал вверх остаётся.")
            elif day_position >= 0.85:
                lines.append(f"  Цена у вершины дня ({int(day_position * 100)}%) — потенциал вниз остаётся.")
        if rsi_1h <= 25:
            lines.append(f"  RSI 1H перепродан ({rsi_1h:.0f}) — возможен отскок.")
        elif rsi_1h >= 75:
            lines.append(f"  RSI 1H перекуплен ({rsi_1h:.0f}) — давление на рост снизится.")
        lines.append("")

        if funding_warn or funding_block:
            pct = round(abs(funding_val) * 100, 3)
            funding_text = "лонги переплачивают" if funding_val > 0 else "шорты переплачивают"
            lines.append(f"⚠️ Ставка финансирования повышена ({pct}%, {funding_text}) — учитывай при удержании позиции.")
            lines.append("")

        if entry_signal == "ENTRY":
            lines.append("✅ СТАВИМ ЛИМИТКУ")
            lines.append(f"  Лимитка {long_short} по цене {fp(close)}.")
        else:
            lines.append("⏸️ ЖДЁМ ПОДТВЕРЖДЕНИЯ")
            lines.append(f"  Сигнал формируется — ставим лимитку {long_short} при подтверждении.")
        lines.append("")

        if sl_p and tp1_p and tp2_p:
            arrow = "📈" if side == "buy" else "📉"
            sl_pct = abs(close - sl_p) / close * 100
            lines.append("📋 ИСПОЛНЕНИЕ (авто + ручное)")
            lines.append(f"  {arrow} Вход:   {fp(close)}")
            lines.append(f"  🛑 Стоп:   {fp(sl_p)}  (−{sl_pct:.2f}% от входа)")
            lines.append(f"  🎯 Цель:   {fp(tp1_p)}   — основная фиксация")
            lines.append(f"  🔝 Стретч: {fp(tp2_p)}   — если импульс сохранится")
            lines.append("")
            ex_notional = round(1000 * 0.01 / (sl_pct / 100))
            ex_margin = round(ex_notional / 10)
        else:
            sl_pct = None
            ex_notional = None
            ex_margin = None

        risk_note = "0.5-1% депозита (СВИНГ — стоп шире)" if trade_style == "SWING" else "1% депозита (СКАЛЬП)"
        fade_hint = eng.get("5m_fade_hint")
        fade_data = eng.get("5m_fade_data", {})
        if fade_hint and fade_data:
            fade_dir = "ЛОНГ" if fade_hint == "LONG" else "ШОРТ"
            fade_arrow = "↗️" if fade_hint == "LONG" else "↘️"
            lines.append(f"📌 BB FADE 5m — {fade_arrow} {fade_dir}")
            lines.append(f"   Вход: {fp(fade_data['entry'])}  🛑 SL: {fp(fade_data['sl'])}  🎯 TP: {fp(fade_data['tp'])}  (R:R ≈{fade_data['rr']:.1f})")
            lines.append("   Объём низкий, цена у BB — откат к середине. Макс. удержание: 60 мин.")
            lines.append("")

        lines.append(f"⏱ Закрыть через {max_hold} минут если уровни не достигнуты.")
        lines.append("")
        lines.append("⚠️ ПРАВИЛА ВХОДА")
        lines.append("  ├─ Плечо: 10x")
        lines.append("  ├─ Стоп: не двигать дальше")
        lines.append(f"  ├─ Риск: {risk_note}")
        if sl_pct and ex_notional:
            lines.append(f"  ├─ Размер: 1% ÷ {sl_pct:.2f}% = ${ex_notional} нотионала на $1000")
            lines.append(f"  │          → маржа ${ex_margin} при 10x")
        lines.append("  └─ Это аналитика — не инвест-рекомендация")
    else:
        if regime == "RANGING":
            if side is None:
                why = "Цена в середине дневного диапазона — ждём пока уйдёт в крайние зоны (< 35% или > 65%)."
                what = "Следить за приближением к дневным экстремумам — только оттуда берём отскок."
            elif adx_rising:
                why = f"ADX на 1H растёт ({adx_1h:.1f}) — рынок выходит из диапазона, mean reversion опасен."
                what = "Ждать пока ADX стабилизируется или начнёт падать."
            elif not five_m_trigger:
                need5 = "выше" if side == "buy" else "ниже"
                why = f"5m не подтвердил движение — FAST ждёт пробоя EMA20 на 5m {need5}."
                what = f"Следить за 5m: как только trigger_close окажется {need5} EMA20 — условие выполнено."
            elif not vwap_ok:
                need = "ниже" if side == "buy" else "выше"
                why = f"Для отскока {'вверх' if side == 'buy' else 'вниз'} нужна цена {need} VWAP ({fp(vwap)})."
                what = f"Ждать пока цена окажется {need} {fp(vwap)}."
            elif vol_ratio < 1.3:
                why = f"Объём слабый (×{vol_ratio:.2f}) — нет импульса для отскока."
                what = "Ждать свечи с повышенным объёмом у экстремума диапазона."
            elif oi_weak:
                why = "Открытый интерес падает — позиции закрываются, отскок ненадёжен."
                what = "Ждать стабилизации OI перед входом."
            else:
                why = "Условия mean reversion не выполнены."
                what = "Следить за следующей 15m свечой у экстремума диапазона."
        else:
            pp_sum = _PAIR_PARAMS.get(symbol, _PAIR_PARAMS_DEFAULT)
            cfg_f = _mode_cfg(pp_sum, "trending", "fast")
            cfg_sw = _mode_cfg(pp_sum, "trending", "swing")
            t_vol = min(cfg_f["vol"], cfg_sw["vol"])
            t_bb = cfg_f.get("bb_width_min", 0.5)
            if bias_1h == "NEUTRAL":
                why = "EMA на 1H без чёткого расхождения — рынок без направления, шансы 50/50."
                what = "Ждать пока EMA20 и EMA50 разойдутся и ADX начнёт расти."
            elif four_h_conflict:
                why = "4H направление против 1H — SWING требует согласования таймфреймов."
                what = "Ждать пока 4H и 1H совпадут по направлению."
            elif not adx_4h_ok:
                why = f"На 4H нет выраженного тренда (ADX {adx_4h:.0f})."
                what = "Ждать роста ADX 4H выше 20."
            elif not adx_rising:
                why = f"Тренд 1H есть (ADX {adx_1h:.1f}), но не ускоряется — движение в паузе."
                what = "Ждать когда ADX начнёт расти."
            elif vol_ratio < t_vol:
                why = f"Объём импульса слабый (×{vol_ratio:.2f}, нужно ×{t_vol:.1f})."
                what = "Ждать свечей с повышенным объёмом."
            elif bb_width_pct < t_bb:
                why = f"Bollinger Bands слишком узкие ({bb_width_pct:.2f}%, нужно >{t_bb:.1f}%)."
                what = "Ждать расширения полос."
            elif not five_m_trigger:
                need5 = "выше" if side == "buy" else "ниже"
                why = f"5m не подтвердил движение — FAST ждёт пробоя EMA20 на 5m {need5}."
                what = f"Следить за 5m: как только trigger_close окажется {need5} EMA20 — условие выполнено."
            elif oi_weak:
                why = "Открытый интерес падает при движении цены — сигнал слабый."
                what = "Ждать стабилизации или роста открытого интереса."
            else:
                why = "Условия входа не выполнены в полном объёме."
                what = "Следить за следующей 15m свечой."

        if side == "buy":
            dir_ctx = "Направление — LONG, но условия входа пока не созрели."
        elif side == "sell":
            dir_ctx = "Направление — SHORT, но условия входа пока не созрели."
        else:
            dir_ctx = ""

        lines.append("📊 СЕЙЧАС НА РЫНКЕ")
        if dir_ctx:
            lines.append(f"  {dir_ctx}")
        lines.append("")
        lines.append("🚫 НЕТ СДЕЛКИ")
        lines.append(f"  {why}")
        lines.append("")
        lines.append("👃 ЗА ЧЕМ СЛЕДИМ")
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

    if is_night:
        lines += ["", "⚠️ АЗИАТСКАЯ СЕССИЯ", "  Сейчас 01:00–07:00 UTC — ликвидность ниже дневной,", "  возможен расширенный спред. Повышенная осторожность."]

    next_close = _next_candle_close(captured_at, 5 if entry_signal == "ENTRY" else (15 if entry_signal == "WAIT" else 60))
    lines += ["", "🔄 ПОВТОРНЫЙ АНАЛИЗ", f"  После {next_close}.", "", sep]
    return "\n".join(lines)


def format_report(symbol: str, captured_at: str, indicators: dict, eng: dict) -> str:
    h1 = indicators.get("1h", {})
    h15 = indicators.get("15m", {})
    h4 = indicators.get("4h", {})
    sep = "=" * 62
    thin = "─" * 62

    def _f(v, fmt=".1f"):
        return format(v, fmt) if isinstance(v, (int, float)) else str(v or "?")

    lines = [
        sep,
        f"  CHART ANALYSIS: {symbol} @ {captured_at}",
        sep,
        "",
        f"── 4H {thin[5:]}",
        f"  Bias: {eng['bias_4h']}  ADX: {_f(h4.get('adx'))}  +DI: {_f(h4.get('plus_di'))}  -DI: {_f(h4.get('minus_di'))}",
        f"  EMA20: {h4.get('ema20', '?')}  EMA50: {h4.get('ema50', '?')}",
        "",
        f"── 1H {thin[5:]}",
        f"  Bias: {eng['bias_1h']}  ADX: {_f(h1.get('adx'))}  Rising: {ok(eng['adx_1h_rising'])}",
        f"  +DI: {_f(h1.get('plus_di'))}  -DI: {_f(h1.get('minus_di'))}  RSI: {h1.get('rsi', '?')}",
        f"  ATR_1H: {h1.get('atr', '?')}  BB_width: {_f(h1.get('bb_width_pct'), '.1f')}%",
        "",
        f"── 15m {thin[6:]}",
        f"  Close: {h15.get('close', '?')}  ATR: {h15.get('atr', '?')}  RSI: {h15.get('rsi', '?')}",
        f"  Vol_ratio_sig: {eng['vol_ratio_sig']:.2f}  BB_expanding: {ok(eng['bb_expanding'])}",
        f"  BB_width: {_f(h15.get('bb_width_pct'), '.1f')}%",
        f"  Swing highs: {h15.get('swing_highs', [])}",
        f"  Swing lows:  {h15.get('swing_lows', [])}",
        "",
        f"── 5m {thin[5:]}",
        f"  Trigger close: {indicators.get('5m', {}).get('trigger_close', '?')}  EMA20: {indicators.get('5m', {}).get('ema20', '?')}  RSI: {indicators.get('5m', {}).get('rsi', '?')}",
        "",
        f"── FAST/SWING ENGINE {thin[20:]}",
        f"  Regime:       {eng.get('regime', '?')}  DI_1H: {eng.get('di_spread_1h', '?')}  DI_4H: {eng.get('di_spread_4h', '?')}  4H↑: {ok(eng.get('adx_4h_rising', False))}",
        f"  Style:        {eng['trade_style']}",
        f"  Side:         {eng['side']}",
        f"  Entry signal: {eng['entry_signal']}",
        f"  VWAP ok:      {ok(eng['vwap_ok'])}  VWAP: {eng.get('vwap')}",
        f"  4H conflict:  {ok(eng.get('four_h_conflict', False))}  4H ADX ok: {ok(eng.get('adx_4h_ok', True))}",
        f"  5m trigger:   {ok(eng.get('five_m_trigger', True))}  RSI_5m: {eng.get('rsi_5m', '?')}",
        f"  OI weak:      {ok(eng['oi_weak'])}  delta: {eng.get('oi_delta', 0):.4f}",
        f"  Funding:      {round(eng.get('funding_val', 0) * 100, 4)}%  warn: {ok(eng['funding_warn'])}  block: {ok(eng['funding_block'])}",
        f"  Night:        {ok(eng['is_night'])}",
        "",
        f"── LEVELS {thin[9:]}",
        f"  SL:           {eng.get('sl_p')}",
        f"  TP1 (auto):   {eng.get('tp1_p')}",
        f"  TP2 (manual): {eng.get('tp2_p')}",
        "",
        sep,
    ]
    return "\n".join(lines)


def build_analysis_snapshot(symbol: str, captured_at_iso: str, result: SignalResult, open_interest=None) -> dict:
    ctx = dict(result.context)
    llm_context = {
        "bias_4h": ctx.get("bias_4h"),
        "bias_1h": ctx.get("bias_1h"),
        "adx_1h": round(float(ctx.get("adx_1h", 0)), 1),
        "adx_4h": round(float(ctx.get("adx_4h", 0)), 1),
        "adx_1h_rising": ctx.get("adx_1h_rising"),
        "plus_di_1h": round(float(ctx.get("plus_di_1h", 0)), 1),
        "minus_di_1h": round(float(ctx.get("minus_di_1h", 0)), 1),
        "supertrend_dir": ctx.get("supertrend_dir"),
        "rsi_1h": result.indicators.get("1h", {}).get("rsi"),
        "rsi_15m": result.indicators.get("15m", {}).get("rsi"),
        "volume_ratio_15m": round(float(ctx.get("vol_ratio_sig", 0)), 2),
        "bb_width_15m": result.indicators.get("15m", {}).get("bb_width_pct"),
        "bb_expanding": ctx.get("bb_expanding"),
        "day_position": ctx.get("day_position"),
        "trade_style_hint": result.trade_style,
        "oi_delta": round(float(ctx.get("oi_delta", 0)), 4),
        "entry_signal": result.entry_signal,
        "funding_rate": round(float(ctx.get("funding_val", 0)), 6),
        "funding_blocked": bool(ctx.get("funding_block")),
        "open_interest": round(open_interest, 0) if open_interest is not None else None,
        "vwap_day": ctx.get("vwap"),
        "day_high": ctx.get("day_high"),
        "day_low": ctx.get("day_low"),
        "atr_1h": round(float(ctx.get("atr_1h", 0)), 4),
        "atr_15m": round(float(ctx.get("atr_15m", 0)), 4),
        "side": result.side,
        "entry_price": result.entry_price if result.sl_price else None,
        "sl_price": result.sl_price,
        "tp1_price": result.tp1_price,
        "tp2_price": result.tp2_price,
        "max_hold_minutes": result.max_hold_min,
        "daily_range_pct": round(float(ctx.get("daily_range_pct", 0)), 2),
        "is_night_session": ctx.get("is_night"),
        "adx_4h_ok": ctx.get("adx_4h_ok"),
        "four_h_conflict": ctx.get("four_h_conflict"),
        "five_m_trigger": ctx.get("five_m_trigger"),
        "regime": result.regime,
        "5m_fade_hint": result.five_m_fade_hint,
        "5m_fade_data": result.five_m_fade_data,
        "strong_4h_veto": ctx.get("strong_4h_veto"),
        "obi_top5": result.microstructure.get("obi_top5"),
        "trade_delta_100": result.microstructure.get("trade_delta_100"),
        "spread_bps": result.microstructure.get("spread_bps"),
        "drop_reason": result.drop_reason,
    }
    return {
        "symbol": symbol,
        "captured_at": captured_at_iso,
        "expiry_time": result.expiry_time,
        "llm_context": llm_context,
        "microstructure": result.microstructure,
        "4h": result.indicators.get("4h", {}),
        "1h": result.indicators.get("1h", {}),
        "15m": result.indicators.get("15m", {}),
        "5m": result.indicators.get("5m", {}),
        "trader_notes": [],
    }


def compute_signal(
    candles_15m: list,
    candles_1h: list,
    candles_4h: list,
    candles_5m: list,
    symbol: str,
    config: dict,
    captured_at_iso: str,
    funding: float | None = None,
    open_interest: float | None = None,
    oi_history: list | None = None,
    books5: dict | None = None,
    trades100: list | None = None,
    raw_idx15: list | None = None,
) -> SignalResult:
    indicators = compute_indicators(candles_1h, candles_15m, candles_5m, config, raw_4h=candles_4h)
    h4 = indicators.get("4h", {})
    h1 = indicators["1h"]
    h15 = indicators["15m"]
    h5 = indicators.get("5m", {})

    ema20_4h = float(h4.get("ema20") or 0)
    ema50_4h = float(h4.get("ema50") or 0)
    ema20_1h = float(h1.get("ema20") or 0)
    ema50_1h = float(h1.get("ema50") or 0)
    bias_4h = "UP" if ema20_4h > ema50_4h > 0 else ("DOWN" if ema20_4h < ema50_4h else "NEUTRAL")
    bias_1h = "UP" if ema20_1h > ema50_1h > 0 else ("DOWN" if ema20_1h < ema50_1h else "NEUTRAL")

    adx_1h = float(h1.get("adx") or 0)
    adx_4h = float(h4.get("adx") or 0)
    atr_15m = float(h15.get("atr") or 0)
    close = float(h15.get("close") or 0)
    rsi_15m = float(h15.get("rsi") or 50)
    rsi_1h = float(h1.get("rsi") or 50)
    plus_di_1h = float(h1.get("plus_di") or 0)
    minus_di_1h = float(h1.get("minus_di") or 0)
    supertrend_dir = str(h15.get("supertrend_dir") or "")
    swing_highs = h15.get("swing_highs", [])
    swing_lows = h15.get("swing_lows", [])
    adx_period = int(config.get("adx_period", 14))

    if candles_1h and len(candles_1h) >= 15:
        highs_1h, lows_1h, closes_1h = parse_candles(candles_1h)
        atr_1h = float(calc_atr(highs_1h, lows_1h, closes_1h, period=adx_period))
        adx_1h_prev, _, _ = calc_adx(highs_1h, lows_1h, closes_1h, period=adx_period, bar_index=-2)
        adx_1h_rising = adx_1h > float(adx_1h_prev)
        di_spread_1h = abs(plus_di_1h - minus_di_1h)
        st_1h = calc_supertrend(highs_1h, lows_1h, closes_1h, period=14, multiplier=3.0)
    else:
        atr_1h = atr_15m * 4
        adx_1h_rising = False
        di_spread_1h = 0.0
        st_1h = {"direction": "up"}
        closes_1h = np.array([])

    slope_1h = calc_slope(closes_1h, period=5) if len(closes_1h) >= 6 else 0.0
    slope_1h_prev = calc_slope(closes_1h[:-1], period=5) if len(closes_1h) >= 7 else 0.0
    if candles_15m and len(candles_15m) >= 7:
        highs_15m_s, lows_15m_s, closes_15m_s = parse_candles(candles_15m)
        slope_15m = calc_slope(closes_15m_s, period=5)
        slope_15m_prev = calc_slope(closes_15m_s[:-1], period=5)
    else:
        highs_15m_s = lows_15m_s = closes_15m_s = np.array([])
        slope_15m = slope_15m_prev = 0.0

    if candles_15m and len(candles_15m) >= 15:
        _, pdi_15m, mdi_15m = calc_adx(highs_15m_s, lows_15m_s, closes_15m_s, period=adx_period, bar_index=-1)
        di_spread_15m = abs(float(pdi_15m) - float(mdi_15m))
    else:
        di_spread_15m = 0.0

    if candles_4h and len(candles_4h) >= 15:
        highs_4h, lows_4h, closes_4h = parse_candles(candles_4h)
        adx_4h_curr, pdi_4h, mdi_4h = calc_adx(highs_4h, lows_4h, closes_4h, period=adx_period, bar_index=-1)
        adx_4h_prev, pdi_4h_c, mdi_4h_c = calc_adx(highs_4h, lows_4h, closes_4h, period=adx_period, bar_index=-2)
        adx_4h_rising = float(adx_4h_curr) > float(adx_4h_prev)
        di_spread_4h = abs(float(pdi_4h) - float(mdi_4h))
        di_spread_4h_closed = abs(float(pdi_4h_c) - float(mdi_4h_c))
    else:
        adx_4h_rising = False
        di_spread_4h = 0.0
        di_spread_4h_closed = 0.0

    if candles_15m and len(candles_15m) >= 20:
        vols_imp = [float(c[5]) for c in candles_15m]
        recent_imp = float(np.mean(vols_imp[1:4]))
        prior_imp = float(np.mean(vols_imp[5:20]))
        vol_ratio_sig = recent_imp / max(prior_imp, 1e-9)
    else:
        vol_ratio_sig = 1.0

    oi_delta = 0.0
    if oi_history and len(oi_history) >= 2:
        def _parse_oi(entry):
            if isinstance(entry, dict):
                return float(entry.get("oi", 0) or entry.get("oiCcy", 0))
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                return float(entry[1])
            return 0.0

        oi_current = _parse_oi(oi_history[0])
        oi_prev = _parse_oi(oi_history[1])
        if oi_prev > 0:
            oi_delta = (oi_current - oi_prev) / oi_prev

    captured_dt = datetime.fromisoformat(captured_at_iso.replace("Z", "+00:00"))
    day_start_ms = int(datetime(captured_dt.year, captured_dt.month, captured_dt.day, tzinfo=timezone.utc).timestamp() * 1000)
    day_candles = [c for c in candles_15m[1:] if int(c[0]) >= day_start_ms]
    if len(day_candles) < 4:
        day_candles = []
    if day_candles:
        dc_closes = [float(c[4]) for c in day_candles]
        dc_vols = [float(c[5]) for c in day_candles]
        dc_highs = [float(c[2]) for c in day_candles]
        dc_lows = [float(c[3]) for c in day_candles]
        vol_sum = sum(dc_vols)
        vwap = round(sum(c * v for c, v in zip(dc_closes, dc_vols)) / vol_sum, 4) if vol_sum > 0 else None
        day_high = round(max(dc_highs), 4)
        day_low = round(min(dc_lows), 4)
    else:
        vwap = day_high = day_low = None

    day_position = round((close - day_low) / (day_high - day_low), 3) if day_high and day_low and day_high != day_low and close else None
    signal_hour = captured_dt.hour
    is_night = 1 <= signal_hour < 7
    daily_range_pct = (day_high - day_low) / day_low * 100 if day_high and day_low and day_low > 0 else 0.0
    bb_expanding = float(h15.get("bb_width_pct") or 0) > 1.5

    h4_available = bool(h4)
    adx_4h_ok = (not h4_available) or float(adx_4h) >= 20
    four_h_dir_conflict = h4_available and bias_4h != "NEUTRAL" and bias_4h != bias_1h and di_spread_4h_closed >= 12
    ema20_5m = float(h5.get("ema20") or 0)
    rsi_5m = float(h5.get("rsi") or 50)
    trigger_close = float(h5.get("trigger_close") or 0)
    if ema20_5m > 0:
        five_m_long = trigger_close > ema20_5m
        five_m_short = trigger_close < ema20_5m
    else:
        five_m_long = True
        five_m_short = True

    pp = _PAIR_PARAMS.get(symbol, _PAIR_PARAMS_DEFAULT)
    bb_width_pct = float(h15.get("bb_width_pct") or 0)
    regime = _detect_regime(adx_1h, adx_4h, adx_4h_rising, di_spread_4h, di_spread_1h, bb_width_pct)
    if regime == "TRENDING" and four_h_dir_conflict:
        regime = "RANGING"

    drift_adx1h_veto = regime == "DRIFT" and adx_1h < 15.0
    trade_style = "NO_TRADE"
    side = None
    entry_cfg = {}

    if regime == "TRENDING":
        def _bb_ok(cfg: dict) -> bool:
            if bb_width_pct < cfg.get("bb_width_min", 0.0):
                return False
            if cfg.get("require_bb_expanding", False) and not bb_expanding:
                return False
            return True

        cfg_sw = _mode_cfg(pp, "trending", "swing")
        swing_base = adx_1h >= cfg_sw.get("adx", 18) and adx_1h_rising and vol_ratio_sig >= cfg_sw["vol"] and _bb_ok(cfg_sw) and not four_h_dir_conflict and adx_4h_ok and di_spread_4h >= 8 and di_spread_1h >= 8
        if "SWING" in pp["allowed_modes"]:
            if swing_base and bias_1h == "UP" and five_m_long:
                trade_style, side, entry_cfg = "SWING", "buy", cfg_sw
            elif swing_base and bias_1h == "DOWN" and five_m_short:
                trade_style, side, entry_cfg = "SWING", "sell", cfg_sw

        cfg_f = _mode_cfg(pp, "trending", "fast")
        fast_base = adx_1h >= cfg_f.get("adx", 18) and adx_1h_rising and vol_ratio_sig >= cfg_f["vol"] and _bb_ok(cfg_f)
        if "FAST" in pp["allowed_modes"] and trade_style == "NO_TRADE":
            if fast_base and five_m_long and bias_1h == "UP":
                trade_style, side, entry_cfg = "FAST", "buy", cfg_f
            elif fast_base and five_m_short and bias_1h == "DOWN":
                trade_style, side, entry_cfg = "FAST", "sell", cfg_f

    elif regime == "RANGING":
        cfg_r = _mode_cfg(pp, "ranging", "fast")
        bb_corridor = cfg_r.get("bb_width_min", 0.8) <= bb_width_pct <= cfg_r.get("bb_width_max", 2.5)
        ranging_base = adx_1h <= cfg_r.get("adx_max", 22) and not adx_1h_rising and vol_ratio_sig >= cfg_r["vol"] and bb_corridor and day_position is not None
        ranging_recovery = cfg_r.get("adx_max", 22) < adx_1h <= 28 and not adx_1h_rising and di_spread_15m >= 12 and vol_ratio_sig >= cfg_r["vol"] and day_position is not None
        buy_pos_ok = day_position is not None and day_position <= cfg_r.get("buy_pos_max", 0.35)
        sell_pos_ok = day_position is not None and day_position >= cfg_r.get("sell_pos_min", 0.65)
        if "FAST" in pp["allowed_modes"]:
            if (ranging_base or ranging_recovery) and five_m_long and buy_pos_ok:
                trade_style, side, entry_cfg = "FAST", "buy", cfg_r
            elif (ranging_base or ranging_recovery) and five_m_short and sell_pos_ok:
                trade_style, side, entry_cfg = "FAST", "sell", cfg_r

    elif regime == "DRIFT":
        cfg_d = _mode_cfg(pp, "drift", "fast")
        ema_series = h1.get("ema20_series") or []
        ema_slope_up = len(ema_series) >= 4 and ema_series[-1] > ema_series[-2] > ema_series[-3] > ema_series[-4]
        ema_slope_down = len(ema_series) >= 4 and ema_series[-1] < ema_series[-2] < ema_series[-3] < ema_series[-4]
        st_dir_1h = st_1h["direction"]
        if ema_slope_up:
            ema_drift_dir = "UP"
        elif ema_slope_down:
            ema_drift_dir = "DOWN"
        elif st_dir_1h == "up":
            ema_drift_dir = "UP"
        elif st_dir_1h == "down":
            ema_drift_dir = "DOWN"
        else:
            ema_drift_dir = "FLAT"
        drift_vwap_ok = ((_ema := ema_drift_dir) == "UP" and close > vwap) or (_ema == "DOWN" and close < vwap) if vwap else False
        drift_bias_ok = True
        if pp.get("drift_bias_check", False):
            drift_bias_ok = not (ema_drift_dir == "UP" and bias_1h == "DOWN") and not (ema_drift_dir == "DOWN" and bias_1h == "UP")
        drift_base = ema_drift_dir != "FLAT" and drift_vwap_ok and vol_ratio_sig >= cfg_d["vol"] and drift_bias_ok
        if "FAST" in pp["allowed_modes"]:
            if drift_base and five_m_long and ema_drift_dir == "UP":
                trade_style, side, entry_cfg = "FAST", "buy", cfg_d
            elif drift_base and five_m_short and ema_drift_dir == "DOWN":
                trade_style, side, entry_cfg = "FAST", "sell", cfg_d

    slope_min = float(config.get("slope_min", 30.0))
    hold_fast_m = 60 if regime == "DRIFT" else int(config.get("hold_fast_minutes", 90))
    if trade_style != "NO_TRADE" and side and regime in ("TRENDING", "DRIFT"):
        sl_cur = slope_15m if trade_style == "FAST" else slope_1h
        sl_prev = slope_15m_prev if trade_style == "FAST" else slope_1h_prev
        slope_ok = sl_cur >= slope_min and sl_cur > sl_prev if side == "buy" else sl_cur <= -slope_min and sl_cur < sl_prev
        if not slope_ok:
            trade_style, side = "NO_TRADE", None

    drift_short_veto = regime == "DRIFT" and side == "sell"
    if drift_short_veto:
        trade_style, side = "NO_TRADE", None

    # D2: DRIFT trigger-bar volume decay veto (trigger vol must be ≥0.9× prior baseline)
    if trade_style == "FAST" and side and regime == "DRIFT":
        if candles_15m and len(candles_15m) >= 20:
            _vols_d2 = [float(c[5]) for c in candles_15m]
            _prior_vol_d2 = float(np.mean(_vols_d2[5:20]))
            if _prior_vol_d2 > 0 and _vols_d2[1] / _prior_vol_d2 < 0.9:
                trade_style, side = "NO_TRADE", None

    # B3: ETH DRIFT hour veto — avoid low-liquidity UTC hours 22–01
    if trade_style == "FAST" and side and regime == "DRIFT" and symbol == "ETH-USDT":
        if signal_hour in (22, 23, 0, 1):
            trade_style, side = "NO_TRADE", None

    # D3: BTC DRIFT late-entry veto — skip when vol_ratio_sig > 3.0 (chasing exhausted move)
    if trade_style == "FAST" and side and regime == "DRIFT" and symbol == "BTC-USDT":
        if vol_ratio_sig > 3.0:
            trade_style, side = "NO_TRADE", None

    strong_4h_veto = trade_style == "FAST" and side is not None and bias_4h != "NEUTRAL" and di_spread_4h_closed >= 8 and ((side == "buy" and bias_4h == "DOWN") or (side == "sell" and bias_4h == "UP"))
    if strong_4h_veto:
        trade_style, side = "NO_TRADE", None

    if day_position is not None and daily_range_pct > pp["late_range"]:
        if side == "buy" and day_position > 0.90:
            trade_style, side = "NO_TRADE", None
        if side == "sell" and day_position < 0.10:
            trade_style, side = "NO_TRADE", None

    four_h_veto = four_h_dir_conflict or strong_4h_veto

    vwap_ok = True
    if vwap and close and side and regime != "TRENDING":
        if regime == "RANGING":
            if side == "buy" and close > vwap:
                vwap_ok = False
            if side == "sell" and close < vwap:
                vwap_ok = False
        elif regime == "DRIFT":
            if side == "buy" and close < vwap:
                vwap_ok = False
            if side == "sell" and close > vwap:
                vwap_ok = False

    funding_val = funding if funding is not None else 0.0
    fund_thresh = 0.0005
    funding_block = (side == "buy" and funding_val > fund_thresh) or (side == "sell" and funding_val < -fund_thresh)
    funding_warn = not funding_block and abs(funding_val) > fund_thresh * 0.5
    oi_weak = oi_delta < -0.03

    sl_p = tp1_p = tp2_p = None
    sl_dist = 0.0
    if trade_style == "FAST" and side and close:
        sl_k = entry_cfg.get("sl_k", 1.4) * 1.2
        atr_sl_dist = max(sl_k * atr_15m, close * 0.004)
        if side == "buy":
            atr_sl = close - atr_sl_dist
            struct = (swing_lows[-1] - 0.2 * atr_15m) if swing_lows else None
            sl_p = round(min(struct, atr_sl) if struct and struct < close else atr_sl, 4)
            sl_dist = close - sl_p
            tp1_k = 0.5 if regime == "DRIFT" else 0.8
            tp1_p = round(close + sl_dist * tp1_k, 4)
            tp2_p = round(close + sl_dist * 1.5, 4)
        else:
            atr_sl = close + atr_sl_dist
            struct = (swing_highs[-1] + 0.2 * atr_15m) if swing_highs else None
            sl_p = round(max(struct, atr_sl) if struct and struct > close else atr_sl, 4)
            sl_dist = sl_p - close
            tp1_k = 0.5 if regime == "DRIFT" else 0.8
            tp1_p = round(close - sl_dist * tp1_k, 4)
            tp2_p = round(close - sl_dist * 1.5, 4)
    elif trade_style == "SWING" and side and close:
        sl_k = entry_cfg.get("sl_k", 1.8) * 1.2
        if side == "buy":
            atr_sl = close - sl_k * atr_15m
            struct = (swing_lows[-1] - 0.3 * atr_15m) if swing_lows else None
            sl_p = round(min(struct, atr_sl) if struct else atr_sl, 4)
            sl_dist = close - sl_p
            tp1_p = round(close + min(sl_dist * 1.0, atr_1h * 0.5), 4)
            tp2_p = round(close + min(sl_dist * 2.5, atr_1h * 1.2), 4)
        else:
            atr_sl = close + sl_k * atr_15m
            struct = (swing_highs[-1] + 0.3 * atr_15m) if swing_highs else None
            sl_p = round(max(struct, atr_sl) if struct else atr_sl, 4)
            sl_dist = sl_p - close
            tp1_p = round(close - min(sl_dist * 1.0, atr_1h * 0.5), 4)
            tp2_p = round(close - min(sl_dist * 2.5, atr_1h * 1.2), 4)

    max_hold_minutes = (240 if is_night else hold_fast_m) if trade_style == "FAST" else 300
    rr_ok = True
    if sl_p and tp2_p and sl_dist > 0:
        rr2 = abs(tp2_p - close) / sl_dist
        if rr2 < 0.8:
            rr_ok = False

    perp_div_4bar = None
    if candles_15m and len(candles_15m) >= 5 and raw_idx15 and len(raw_idx15) >= 5:
        try:
            perp_now = float(candles_15m[0][4])
            perp_4back = float(candles_15m[4][4])
            idx_now = float(raw_idx15[0][4])
            idx_4back = float(raw_idx15[4][4])
            if perp_4back > 0 and idx_4back > 0:
                perp_div_4bar = (perp_now - perp_4back) / perp_4back * 100 - (idx_now - idx_4back) / idx_4back * 100
        except (IndexError, ValueError, ZeroDivisionError):
            pass

    perp_div_short_veto = side == "sell" and perp_div_4bar is not None and perp_div_4bar > 0.0
    if trade_style == "NO_TRADE" or not vwap_ok or oi_weak or funding_block or not rr_ok or not sl_p or not tp1_p or perp_div_short_veto or drift_adx1h_veto:
        entry_signal = "NO_TRADE"
    elif funding_warn:
        entry_signal = "WAIT"
    else:
        entry_signal = "ENTRY"

    fade_hint = None
    fade_data: dict = {}
    if len(candles_5m) >= 23:
        try:
            raw5 = list(reversed(candles_5m[1:23]))
            c5 = [float(b[4]) for b in raw5]
            v5 = [float(b[5]) for b in raw5]
            h5v = [float(b[2]) for b in raw5]
            l5v = [float(b[3]) for b in raw5]
            bb5_mid = sum(c5[-20:]) / 20
            bb5_std = (sum((x - bb5_mid) ** 2 for x in c5[-20:]) / 20) ** 0.5
            bb5_up = bb5_mid + 2.0 * bb5_std
            bb5_lo = bb5_mid - 2.0 * bb5_std
            vol_ma5 = sum(v5[-20:]) / 20
            vol_low = vol_ma5 > 0 and v5[-1] < vol_ma5 * 0.70
            vol_declining = len(v5) >= 2 and v5[-1] < v5[-2]
            at_lo = bb5_lo > 0 and c5[-1] <= bb5_lo * 1.002
            at_hi = bb5_up > 0 and c5[-1] >= bb5_up * 0.998
            ranging5 = adx_1h < 20
            tr5 = [max(h5v[i] - l5v[i], abs(h5v[i] - c5[i - 1]), abs(l5v[i] - c5[i - 1])) for i in range(1, min(15, len(c5)))]
            atr5 = sum(tr5) / len(tr5) if tr5 else 0.0
            not_thrust = (h5v[-1] - l5v[-1]) < 1.5 * atr5
            slope5_now = calc_slope(c5, period=5)
            slope5_prev = calc_slope(c5[:-1], period=5)
            if ranging5 and vol_low and vol_declining and atr5 > 0 and not_thrust:
                if at_hi and (c5[-1] - bb5_mid) >= atr5 and slope5_now < slope5_prev:
                    fade_hint = "SHORT"
                    fade_data = {"entry": c5[-1], "sl": bb5_up + atr5, "tp": bb5_mid, "rr": (c5[-1] - bb5_mid) / (bb5_up + atr5 - c5[-1])}
                elif at_lo and (bb5_mid - c5[-1]) >= atr5 and slope5_now > slope5_prev:
                    fade_hint = "LONG"
                    fade_data = {"entry": c5[-1], "sl": bb5_lo - atr5, "tp": bb5_mid, "rr": (bb5_mid - c5[-1]) / (c5[-1] - (bb5_lo - atr5))}
        except Exception:
            pass

    micro = _build_micro_snapshot(books5, trades100)
    engine_vars = {
        "trade_style": trade_style,
        "entry_signal": entry_signal,
        "side": side,
        "bias_1h": bias_1h,
        "bias_4h": bias_4h,
        "adx_1h": adx_1h,
        "adx_1h_rising": adx_1h_rising,
        "vol_ratio_sig": vol_ratio_sig,
        "bb_expanding": bb_expanding,
        "vwap_ok": vwap_ok,
        "four_h_veto": four_h_veto,
        "oi_weak": oi_weak,
        "oi_delta": oi_delta,
        "is_night": is_night,
        "funding_warn": funding_warn,
        "funding_block": funding_block,
        "funding_val": funding_val,
        "close": close,
        "sl_p": sl_p,
        "tp1_p": tp1_p,
        "tp2_p": tp2_p,
        "vwap": vwap,
        "day_high": day_high,
        "day_low": day_low,
        "max_hold_minutes": max_hold_minutes,
        "daily_range_pct": daily_range_pct,
        "day_position": day_position,
        "rsi_1h": rsi_1h,
        "rsi_15m": rsi_15m,
        "adx_4h": adx_4h,
        "four_h_conflict": four_h_dir_conflict,
        "adx_4h_ok": adx_4h_ok,
        "five_m_trigger": (five_m_long if side == "buy" else five_m_short) if side else True,
        "rsi_5m": rsi_5m,
        "regime": regime,
        "di_spread_1h": round(di_spread_1h, 1),
        "di_spread_4h": round(di_spread_4h, 1),
        "adx_4h_rising": adx_4h_rising,
        "strong_4h_veto": strong_4h_veto,
        "perp_div_4bar": round(perp_div_4bar, 4) if perp_div_4bar is not None else None,
        "perp_div_short_veto": perp_div_short_veto,
        "drift_adx1h_veto": drift_adx1h_veto,
        "drift_short_veto": drift_short_veto,
        "5m_fade_hint": fade_hint,
        "5m_fade_data": fade_data,
        "micro": micro,
    }
    drop_reason = _resolve_drop_reason(entry_signal, engine_vars)
    context = {
        "bias_4h": bias_4h,
        "bias_1h": bias_1h,
        "adx_1h": adx_1h,
        "adx_4h": adx_4h,
        "adx_1h_rising": adx_1h_rising,
        "plus_di_1h": plus_di_1h,
        "minus_di_1h": minus_di_1h,
        "supertrend_dir": supertrend_dir,
        "vol_ratio_sig": vol_ratio_sig,
        "bb_expanding": bb_expanding,
        "day_position": day_position,
        "oi_delta": oi_delta,
        "funding_val": funding_val,
        "funding_block": funding_block,
        "vwap": vwap,
        "day_high": day_high,
        "day_low": day_low,
        "atr_1h": atr_1h,
        "atr_15m": atr_15m,
        "daily_range_pct": daily_range_pct,
        "is_night": is_night,
        "adx_4h_ok": adx_4h_ok,
        "four_h_conflict": four_h_dir_conflict,
        "five_m_trigger": (five_m_long if side == "buy" else five_m_short) if side else True,
        "slope_1h": slope_1h,
        "slope_15m": slope_15m,
        "oi": open_interest,
    }
    engine_summary = build_engine_summary(symbol, captured_at_iso, engine_vars)
    report_text = format_report(symbol, captured_at_iso, indicators, engine_vars)
    return SignalResult(
        symbol=symbol,
        captured_at=captured_at_iso,
        entry_signal=entry_signal,
        side=side,
        trade_style=trade_style,
        regime=regime,
        entry_price=close,
        sl_price=sl_p,
        tp1_price=tp1_p,
        tp2_price=tp2_p,
        max_hold_min=max_hold_minutes,
        drop_reason=drop_reason,
        context=context,
        indicators=indicators,
        engine_vars=engine_vars,
        microstructure=micro,
        engine_summary=engine_summary,
        report_text=report_text,
        five_m_fade_hint=fade_hint,
        five_m_fade_data=fade_data,
    )
