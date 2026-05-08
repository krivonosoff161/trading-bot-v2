from __future__ import annotations

import bisect
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.backtest.backtest_simulate import (
    NOTIONAL_RATIO,
    _candle_vol_delta,
    _get_hist_funding,
    _get_oi_delta,
    compute_signal,
)
from scripts.backtest.research_common import load_cache
from src.strategy.indicators import calc_rsi


RESULT_JSON = Path(__file__).with_name("bt_entry_filters_results.json")
HOLD_TIMES = [75, 120, 180, 240, 360]
FILTER_CONFIGS = [
    {"name": "baseline", "filters": []},
    {"name": "late_mom", "filters": ["late_momentum"], "late_momentum": {"vol_ratio_max": 3.0}},
    {"name": "rsi", "filters": ["rsi_divergence"], "rsi_divergence": {"rsi_max": 65, "rsi_min": 35}},
    {"name": "delta", "filters": ["volume_delta"], "volume_delta": {"delta_bars": 3}},
    {"name": "ob", "filters": ["order_block"], "order_block": {"ob_lookback": 5, "ob_zone_pct": 0.01}},
    {"name": "fvg", "filters": ["fvg"], "fvg": {"fvg_lookback": 10}},
    {"name": "candle", "filters": ["candle_pattern"]},
    {"name": "bos", "filters": ["bos"], "bos": {"bos_lookback": 10}},
    {"name": "late_mom+delta", "filters": ["late_momentum", "volume_delta"], "late_momentum": {"vol_ratio_max": 3.0}, "volume_delta": {"delta_bars": 3}},
    {"name": "ob+delta", "filters": ["order_block", "volume_delta"], "order_block": {"ob_lookback": 5, "ob_zone_pct": 0.005}, "volume_delta": {"delta_bars": 3}},
    {"name": "fvg+delta", "filters": ["fvg", "volume_delta"], "fvg": {"fvg_lookback": 10}, "volume_delta": {"delta_bars": 3}},
    {"name": "bos+delta", "filters": ["bos", "volume_delta"], "bos": {"bos_lookback": 10}, "volume_delta": {"delta_bars": 3}},
    {"name": "ob+candle", "filters": ["order_block", "candle_pattern"], "order_block": {"ob_lookback": 5, "ob_zone_pct": 0.02}},
    {"name": "fvg+candle", "filters": ["fvg", "candle_pattern"], "fvg": {"fvg_lookback": 10}},
    {"name": "ob+delta+candle", "filters": ["order_block", "volume_delta", "candle_pattern"], "order_block": {"ob_lookback": 5, "ob_zone_pct": 0.01}, "volume_delta": {"delta_bars": 3}},
    {"name": "fvg+delta+candle", "filters": ["fvg", "volume_delta", "candle_pattern"], "fvg": {"fvg_lookback": 10}, "volume_delta": {"delta_bars": 3}},
    {"name": "ob+bos+delta", "filters": ["order_block", "bos", "volume_delta"], "order_block": {"ob_lookback": 5, "ob_zone_pct": 0.02}, "bos": {"bos_lookback": 10}, "volume_delta": {"delta_bars": 3}},
]


def visible(rows: list, end: int, size: int) -> list:
    return list(reversed(rows[max(0, end - size):end]))


def vol_ratio(candles: list, entry_idx: int, lookback: int = 15) -> float:
    if entry_idx < lookback:
        return 0.0
    base = np.mean([float(row[5]) for row in candles[entry_idx - lookback:entry_idx]])
    return float(candles[entry_idx][5]) / base if base > 0 else 0.0


def filter_late_momentum(candles: list, entry_idx: int, side: str, params: dict) -> bool:
    return vol_ratio(candles, entry_idx) <= params["vol_ratio_max"]


def filter_rsi_divergence(candles: list, entry_idx: int, side: str, params: dict) -> bool:
    closes = np.array([float(row[4]) for row in candles[:entry_idx + 1]], dtype=float)
    rsi = calc_rsi(closes, period=14)
    return rsi < params["rsi_max"] if side == "buy" else rsi > params["rsi_min"]


def filter_volume_delta(candles: list, entry_idx: int, side: str, params: dict) -> bool:
    start = max(0, entry_idx - params["delta_bars"] + 1)
    delta = 0.0
    for row in candles[start:entry_idx + 1]:
        delta += float(row[5]) if float(row[4]) >= float(row[1]) else -float(row[5])
    return delta > 0 if side == "buy" else delta < 0


def find_order_block(candles: list, entry_idx: int, side: str, lookback: int) -> tuple[float, float] | None:
    start = max(1, entry_idx - lookback)
    for idx in range(entry_idx - 1, start - 1, -1):
        cur = candles[idx]
        nxt_1 = candles[idx + 1]
        nxt_2 = candles[idx + 2] if idx + 2 < len(candles) else None
        if nxt_2 is None:
            continue
        bull = float(cur[4]) > float(cur[1])
        bear = float(cur[4]) < float(cur[1])
        if side == "buy" and bear and float(nxt_1[4]) > float(cur[2]) and float(nxt_2[4]) > float(cur[2]):
            return float(cur[3]), float(cur[2])
        if side == "sell" and bull and float(nxt_1[4]) < float(cur[3]) and float(nxt_2[4]) < float(cur[3]):
            return float(cur[3]), float(cur[2])
    return None


def filter_order_block(candles: list, entry_idx: int, side: str, params: dict) -> bool:
    zone = find_order_block(candles, entry_idx, side, params["ob_lookback"])
    if zone is None:
        return False
    bar_low = float(candles[entry_idx][3])
    bar_high = float(candles[entry_idx][2])
    price = float(candles[entry_idx][4])
    low, high = zone
    pad = price * params["ob_zone_pct"]
    if max(bar_low, low - pad) <= min(bar_high, high + pad):
        return True
    distance = min(abs(price - low), abs(price - high))
    return distance <= pad


def recent_fvgs(candles: list, entry_idx: int, side: str, lookback: int) -> list[tuple[float, float]]:
    gaps: list[tuple[float, float]] = []
    start = max(2, entry_idx - lookback)
    for idx in range(start, entry_idx + 1):
        c0 = candles[idx - 2]
        c2 = candles[idx]
        if side == "buy" and float(c0[2]) < float(c2[3]):
            low, high = float(c0[2]), float(c2[3])
            min_low = min(float(row[3]) for row in candles[idx + 1:entry_idx + 1]) if idx < entry_idx else high
            if min_low > low:
                gaps.append((low, high))
        if side == "sell" and float(c0[3]) > float(c2[2]):
            low, high = float(c2[2]), float(c0[3])
            max_high = max(float(row[2]) for row in candles[idx + 1:entry_idx + 1]) if idx < entry_idx else low
            if max_high < high:
                gaps.append((low, high))
    return gaps


def filter_fvg(candles: list, entry_idx: int, side: str, params: dict) -> bool:
    bar_low = float(candles[entry_idx][3])
    bar_high = float(candles[entry_idx][2])
    price = float(candles[entry_idx][4])
    pad = price * 0.005
    for low, high in recent_fvgs(candles, entry_idx, side, params["fvg_lookback"]):
        if max(bar_low, low - pad) <= min(bar_high, high + pad):
            return True
    return False


def engulfing(prev: list, cur: list, side: str) -> bool:
    if side == "buy":
        return float(cur[4]) > float(prev[1]) and float(cur[1]) < float(prev[4])
    return float(cur[4]) < float(prev[1]) and float(cur[1]) > float(prev[4])


def filter_candle_pattern(candles: list, entry_idx: int, side: str, params: dict) -> bool:
    cur = candles[entry_idx]
    prev = candles[entry_idx - 1] if entry_idx > 0 else cur
    opn, high, low, close = map(float, cur[1:5])
    body = abs(close - opn)
    lower = min(opn, close) - low
    upper = high - max(opn, close)
    if side == "buy":
        hammer = lower > body * 2 and upper <= max(body, 1e-9)
        return hammer or engulfing(prev, cur, side)
    star = upper > body * 2 and lower <= max(body, 1e-9)
    return star or engulfing(prev, cur, side)


def swing_points(candles: list, end_idx: int) -> tuple[list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    for idx in range(1, end_idx):
        prev_h = float(candles[idx - 1][2])
        cur_h = float(candles[idx][2])
        next_h = float(candles[idx + 1][2])
        prev_l = float(candles[idx - 1][3])
        cur_l = float(candles[idx][3])
        next_l = float(candles[idx + 1][3])
        if cur_h > prev_h and cur_h > next_h:
            highs.append(cur_h)
        if cur_l < prev_l and cur_l < next_l:
            lows.append(cur_l)
    return highs, lows


def filter_bos(candles: list, entry_idx: int, side: str, params: dict) -> bool:
    start = max(2, entry_idx - params["bos_lookback"])
    highs, lows = swing_points(candles[start:entry_idx + 1], entry_idx - start)
    if side == "buy" and len(highs) >= 2:
        return highs[-1] > highs[-2]
    if side == "sell" and len(lows) >= 2:
        return lows[-1] < lows[-2]
    return False


FILTER_FUNCS = {
    "late_momentum": filter_late_momentum,
    "rsi_divergence": filter_rsi_divergence,
    "volume_delta": filter_volume_delta,
    "order_block": filter_order_block,
    "fvg": filter_fvg,
    "candle_pattern": filter_candle_pattern,
    "bos": filter_bos,
}


def always_on_pass(signal: dict, candles_15m: list, entry_idx: int) -> bool:
    if vol_ratio(candles_15m, entry_idx) < 0.9:
        return False
    hour = signal["hour"]
    return not (signal["symbol"] == "ETH-USDT" and hour in {22, 23, 0, 1})


def simulate_trade(signal: dict, hold_min: int) -> dict:
    entry = signal["entry"]
    stop = signal["sl"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    risk = abs(entry - stop)
    last_close = entry
    mfe = mae = 0.0
    tp1_hit = tp2_hit = False
    current_sl = stop

    def make_result(outcome: str, exit_price: float, hold: int) -> dict:
        return {
            "outcome": outcome,
            "exit_price": exit_price,
            "hold_min": hold,
            "mfe_r": round(mfe, 3),
            "mae_r": round(mae, 3),
            "tp1_hit": tp1_hit,
            "tp2_hit": tp2_hit,
        }

    for row in signal["forward_5m"]:
        ts = int(row[0])
        if ts <= signal["ts_ms"]:
            continue
        hold = (ts - signal["ts_ms"]) // 60000
        high = float(row[2])
        low = float(row[3])
        last_close = float(row[4])
        favorable = (high - entry) if signal["side"] == "buy" else (entry - low)
        adverse = (entry - low) if signal["side"] == "buy" else (high - entry)
        mfe = max(mfe, favorable / risk if risk else 0.0)
        mae = max(mae, adverse / risk if risk else 0.0)

        if signal["side"] == "buy":
            if not tp1_hit and high >= tp1:
                tp1_hit = True
                current_sl = entry
            if tp1_hit and high >= tp2:
                tp2_hit = True
                return make_result("TP2", tp2, hold)
            if low <= current_sl:
                return make_result("TP1_SL_EXIT" if tp1_hit else "STOP", current_sl, hold)
        else:
            if not tp1_hit and low <= tp1:
                tp1_hit = True
                current_sl = entry
            if tp1_hit and low <= tp2:
                tp2_hit = True
                return make_result("TP2", tp2, hold)
            if high >= current_sl:
                return make_result("TP1_SL_EXIT" if tp1_hit else "STOP", current_sl, hold)

        if hold > hold_min:
            return make_result("TP1_TIME_EXIT" if tp1_hit else "TIME_EXIT", last_close, hold)

    return make_result("OPEN", last_close, hold_min)


def build_signal_row(symbol: str, sig: dict, ts_ms: int, candles_15m: list, entry_idx: int, forward_5m: list) -> dict:
    entry = float(sig["close"])
    stop = float(sig["sl"])
    risk = abs(entry - stop)
    tp2 = entry + risk * 1.5 if sig["side"] == "buy" else entry - risk * 1.5
    return {
        "symbol": symbol,
        "ts_ms": ts_ms,
        "hour": int(sig["signal_hour"]),
        "regime": sig["regime"],
        "side": sig["side"],
        "entry": entry,
        "sl": stop,
        "tp1": float(sig["tp"]),
        "tp2": tp2,
        "candles_15m": candles_15m,
        "entry_idx": entry_idx,
        "forward_5m": forward_5m,
    }


def collect_signals(cache: dict, regimes: tuple[str, ...]) -> list[dict]:
    signals = []
    for symbol, data in cache.items():
        if not isinstance(data, dict) or "15m" not in data:
            continue
        ts15 = [int(row[0]) for row in data["15m"]]
        ts1 = [int(row[0]) for row in data["1h"]]
        ts4 = [int(row[0]) for row in data["4h"]]
        ts5 = [int(row[0]) for row in data["5m"]]
        fund_hist = data.get("funding_history", [])
        oi_hist = data.get("oi_history", [])
        candles_15m = [row for row in data["15m"] if len(row) < 9 or row[8] == "1"]
        for ts_ms in ts15[96:-1]:
            i15 = bisect.bisect_left(ts15, ts_ms)
            i1 = bisect.bisect_left(ts1, ts_ms)
            i4 = bisect.bisect_left(ts4, ts_ms)
            i5 = bisect.bisect_left(ts5, ts_ms)
            raw_15m = visible(data["15m"], i15, 96)
            raw_5m = visible(data["5m"], i5, 30)
            sig = compute_signal(
                visible(data["4h"], i4, 60),
                visible(data["1h"], i1, 60),
                raw_15m,
                funding=_get_hist_funding(fund_hist, ts_ms),
                symbol=symbol,
                mode="COMBINED",
                oi_delta=_get_oi_delta(oi_hist, ts_ms),
                raw_5m=raw_5m,
                trade_delta_15m=_candle_vol_delta(raw_5m),
            )
            if not sig or sig["entry_signal"] != "ENTRY" or sig["trade_style"] != "FAST" or sig["regime"] not in regimes:
                continue
            entry_idx = i15 - 1
            if entry_idx < 15 or entry_idx >= len(candles_15m):
                continue
            row = build_signal_row(symbol, sig, ts_ms, candles_15m, entry_idx, data["5m"][i5:])
            if always_on_pass(row, candles_15m, entry_idx):
                signals.append(row)
    return sorted(signals, key=lambda row: (row["ts_ms"], row["symbol"]))


def passed_filters(signal: dict, config: dict) -> bool:
    for name in config["filters"]:
        if not FILTER_FUNCS[name](signal["candles_15m"], signal["entry_idx"], signal["side"], config.get(name, {})):
            return False
    return True


def trade_pnl(balance: float, trade: dict, entry: float) -> float:
    price_move = trade["price_move"]
    return price_move / entry * balance * NOTIONAL_RATIO if entry > 0 else 0.0


def summarize(mode: str, name: str, hold_min: int, trades: list[dict], filtered_out: int, total_signals: int) -> dict:
    wins = [trade for trade in trades if trade["exit_r"] > 0]
    losses = [trade for trade in trades if trade["exit_r"] < 0]
    gross_w = sum(trade["exit_r"] for trade in wins)
    gross_l = abs(sum(trade["exit_r"] for trade in losses))
    balance = 1000.0
    for trade in trades:
        balance += trade_pnl(balance, trade, trade["entry"])
    count = len(trades)
    return {
        "name": f"{name}_hold{hold_min}m",
        "mode": mode,
        "filter_combo": name,
        "hold_min": hold_min,
        "n_trades": count,
        "n_filtered_out": filtered_out,
        "pass_rate_pct": round(count / total_signals * 100, 1) if total_signals else 0.0,
        "wr_pct": round(len(wins) / count * 100, 1) if count else 0.0,
        "pf": round(gross_w / gross_l, 2) if gross_l else 99.0,
        "avg_r": round(sum(trade["exit_r"] for trade in trades) / count, 3) if count else 0.0,
        "sim_pct": round((balance - 1000.0) / 1000.0 * 100, 1),
        "time_exit_pct": round(sum(trade["outcome"] == "TIME_EXIT" for trade in trades) / count * 100, 1) if count else 0.0,
        "avg_mfe_r": round(sum(trade["mfe_r"] for trade in trades) / count, 3) if count else 0.0,
        "avg_mae_r": round(sum(trade["mae_r"] for trade in trades) / count, 3) if count else 0.0,
        "tp1_hit_pct": round(sum(trade["tp1_hit"] for trade in trades) / count * 100, 1) if count else 0.0,
        "tp2_hit_pct": round(sum(trade["outcome"] == "TP2" for trade in trades) / count * 100, 1) if count else 0.0,
        "sl_hit_pct": round(sum(trade["outcome"] == "STOP" for trade in trades) / count * 100, 1) if count else 0.0,
        "stop_pct": round(sum(trade["outcome"] == "STOP" for trade in trades) / count * 100, 1) if count else 0.0,
        "tp1_sl_exit_pct": round(sum(trade["outcome"] == "TP1_SL_EXIT" for trade in trades) / count * 100, 1) if count else 0.0,
        "tp1_time_exit_pct": round(sum(trade["outcome"] == "TP1_TIME_EXIT" for trade in trades) / count * 100, 1) if count else 0.0,
        "tp2_pct": round(sum(trade["outcome"] == "TP2" for trade in trades) / count * 100, 1) if count else 0.0,
    }


def print_heatmap(mode: str, rows: list[dict]) -> None:
    print(f"\n{mode} heatmap: sim_pct")
    print("combo".ljust(22) + "".join(f"{hold:>9}m" for hold in HOLD_TIMES))
    for combo in [cfg["name"] for cfg in FILTER_CONFIGS]:
        parts = [combo.ljust(22)]
        for hold in HOLD_TIMES:
            row = next(item for item in rows if item["filter_combo"] == combo and item["hold_min"] == hold)
            parts.append(f"{row['sim_pct']:>10.1f}")
        print("".join(parts))


def print_answers(mode: str, rows: list[dict]) -> None:
    baseline = [row for row in rows if row["filter_combo"] == "baseline"]
    below_10 = next((row for row in sorted(baseline, key=lambda item: item["hold_min"]) if row["time_exit_pct"] < 10), None)
    print(f"\n{mode} answers")
    print(f"baseline time_exit_pct < 10%: {below_10['hold_min']}m" if below_10 else "baseline time_exit_pct < 10%: not reached")
    cands = [row for row in rows if row["hold_min"] == 75 and row["filter_combo"] != "baseline" and row["pass_rate_pct"] > 50]
    best_wr = max(cands, key=lambda row: row["wr_pct"], default=None)
    if best_wr:
        print(f"best WR at hold=75m with pass_rate>50%: {best_wr['filter_combo']} WR={best_wr['wr_pct']}% pass={best_wr['pass_rate_pct']}%")
    obfvg = [row for row in rows if any(tag in row["filter_combo"] for tag in ("ob", "fvg")) and row["pf"] > 3.51]
    print(f"OB/FVG combo with PF > 3.51: {'yes' if obfvg else 'no'}")
    by_hold = {hold: round(np.mean([row["avg_mfe_r"] for row in rows if row["hold_min"] == hold]), 3) for hold in HOLD_TIMES}
    print("avg_mfe_r by hold: " + ", ".join(f"{hold}m={value}" for hold, value in by_hold.items()))


def run_mode(mode: str, cache: dict, regimes: tuple[str, ...]) -> tuple[list[dict], int]:
    signals = collect_signals(cache, regimes)
    rows: list[dict] = []
    for config in FILTER_CONFIGS:
        passed = [signal for signal in signals if passed_filters(signal, config)]
        filtered_out = len(signals) - len(passed)
        for hold_min in HOLD_TIMES:
            trades = []
            for signal in passed:
                result = simulate_trade(signal, hold_min)
                direction = 1 if signal["side"] == "buy" else -1
                price_move = direction * (result["exit_price"] - signal["entry"])
                risk = abs(signal["entry"] - signal["sl"])
                trades.append(
                    {
                        **result,
                        "entry": signal["entry"],
                        "price_move": price_move,
                        "exit_r": round(price_move / risk, 3) if risk > 0 else 0.0,
                    }
                )
            rows.append(summarize(mode, config["name"], hold_min, trades, filtered_out, len(signals)))
    return rows, len(signals)


def print_mode(mode: str, rows: list[dict], signal_count: int) -> None:
    print(f"\nCollected {mode} FAST signals after D2/B3: {signal_count}")
    top10 = sorted((row for row in rows if row["n_trades"] >= 30), key=lambda row: (row["pf"], row["sim_pct"]), reverse=True)[:10]
    print(f"\n{mode} Top-10 by PF (n>=30)")
    for row in top10:
        print(
            f"{row['name']:<28} n={row['n_trades']:3} PF={row['pf']:4.2f} WR={row['wr_pct']:5.1f}% "
            f"sim={row['sim_pct']:6.1f}% TIME={row['time_exit_pct']:5.1f}% TP2={row['tp2_pct']:5.1f}%"
        )
    print_heatmap(mode, rows)
    print_answers(mode, rows)


def main() -> None:
    cache = load_cache()
    drift_rows, drift_count = run_mode("DRIFT", cache, ("DRIFT", "WEAK_TREND"))
    trending_rows, trending_count = run_mode("TRENDING", cache, ("TRENDING",))
    payload = {
        "drift_signals": drift_count,
        "trending_signals": trending_count,
        "drift_results": drift_rows,
        "trending_results": trending_rows,
    }
    RESULT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print_mode("DRIFT", drift_rows, drift_count)
    print_mode("TRENDING", trending_rows, trending_count)
    print(f"\nSaved JSON -> {RESULT_JSON.name}")


if __name__ == "__main__":
    main()
