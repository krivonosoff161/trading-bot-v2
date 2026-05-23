from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt

import continuation_research_20_05_2026 as cont
import regime_coverage_research_21_05_2026 as phase_a
import regime_exit_rerun_22_05_2026 as exit_rerun
import regime_model_phaseB_21_05_2026 as phase_b
import three_engines_22_05_2026 as three_base


SUFFIX = "polish_22_05_2026"
ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
CASE_DIR = OUT_DIR / f"three_engines_cases_{SUFFIX}"
REPORT_MD = OUT_DIR / f"three_engines_report_{SUFFIX}.md"
SUMMARY_JSON = OUT_DIR / f"three_engines_summary_{SUFFIX}.json"
RUN_LOG = OUT_DIR / f"three_engines_run_{SUFFIX}.log"

CONFIG = {
    "fee_pct": 0.20,
    "entry_slippage_pct": 0.03,
    "universe_symbols": exit_rerun.CONFIG["universe_symbols"],
    "impulse_pairs": [
        "BSB-USDT-SWAP",
        "EDEN-USDT-SWAP",
        "RLS-USDT-SWAP",
        "CHZ-USDT-SWAP",
        "SPACE-USDT-SWAP",
        "NOT-USDT-SWAP",
        "TURBO-USDT-SWAP",
        "BOME-USDT-SWAP",
    ],
    "impulse_entry_windows_sec": [10, 20, 60, 120, 300],
    "impulse_min_move_pct": [0.6, 0.8, 1.0],
    "impulse_body_ratio": [1.2, 1.5, 2.0],
    "impulse_volume_ratio": [1.0, 1.5, 2.0],
    "impulse_trigger_pct": 0.30,
    "impulse_hold_min": 60,
    "impulse_cooldown_min": 5,
    "trend_exits": ["structure_k1", "structure_k2", "structure_k3", "atr2", "atr3", "ema20_break"],
    "trend_hold_bars": 32,
    "trend_edge_pct": 1.4,
    "fade_touch_tolerance_pct": [0.05, 0.10, 0.20],
    "fade_targets": ["middle", "opposite", "giveback_40"],
    "fade_adx_max": [22.0, 26.0, 30.0],
    "fade_bb_width_max": [2.5, 3.5, 5.0],
    "fade_hold_bars": 8,
    "case_limit_per_engine": 8,
}


def log(message: str) -> None:
    print(message)
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def safe_float(value: Any) -> float:
    return phase_a.safe_float(value)


def average(values: Iterable[float]) -> float:
    return phase_a.average(values)


def pct(part: int | float, total: int | float) -> float:
    return phase_a.pct(part, total)


def fmt(value: Any, suffix: str = "") -> str:
    return phase_a.fmt(value, suffix)


def iso_from_ms(ts_ms: int) -> str:
    return phase_a.iso_from_ms(ts_ms)


def json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [json_safe(v) for v in value]
    return value


def render_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" if i == 0 else "---:" for i in range(len(headers))) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return lines


def positive_capture(gross_pct: float, mfe_pct: float) -> float:
    if mfe_pct <= 0:
        return float("nan")
    return min(100.0, max(gross_pct, 0.0) / mfe_pct * 100)


def slipped_entry(price: float, side: str) -> float:
    return exit_rerun.slipped_entry(price, side)


def dir_return(entry: float, price: float, side: str) -> float:
    return phase_b.dir_return(entry, price, side)


def load_replay() -> tuple[dict[str, phase_a.CandleSet], list[dict[str, Any]], list[dict[str, Any]], int, int]:
    universe = list(CONFIG["universe_symbols"])
    strategy_cfg = phase_a.load_strategy_config()
    start_close_ms, end_close_ms = phase_a.cached_reference_window(universe)
    candle_sets: dict[str, phase_a.CandleSet] = {}
    decisions_all: list[dict[str, Any]] = []
    events_all: list[dict[str, Any]] = []
    log(f"polish trend/fade replay {iso_from_ms(start_close_ms)} -> {iso_from_ms(end_close_ms)}")
    for symbol in universe:
        candle_set = phase_a.load_symbol_candles(symbol, start_close_ms, end_close_ms)
        if candle_set is None:
            log(f"{symbol}: skipped candles")
            continue
        candle_sets[symbol] = candle_set
        decisions, events = phase_a.replay_symbol(symbol, candle_set, strategy_cfg, start_close_ms, end_close_ms)
        for row in decisions:
            regime, reason = phase_b.corrected_regime(row)
            row["corrected_regime"] = regime
            row["corrected_reason"] = reason
        for event in events:
            start_row = event.get("start_engine") or event.get("engine") or {}
            regime, reason = phase_b.corrected_regime(start_row)
            event["old_regime"] = event["regime"]
            event["corrected_regime"] = regime
            event["corrected_reason"] = reason
        decisions_all.extend(decisions)
        events_all.extend(events)
        log(f"{symbol}: decisions={len(decisions)} moves={len(events)}")
    return candle_sets, decisions_all, events_all, start_close_ms, end_close_ms


def candle_coverage(candle_sets: dict[str, phase_a.CandleSet]) -> dict[str, Any]:
    return {
        "loaded_symbols": len(candle_sets),
        "symbols": [
            {
                "symbol": symbol,
                "source": candle_set.source,
                "bars": {tf: len(candle_set.rows.get(tf) or []) for tf in ["5m", "15m", "1H", "4H"]},
            }
            for symbol, candle_set in sorted(candle_sets.items())
        ],
    }


def tick_coverage_for_pairs(pairs: list[str]) -> dict[str, Any]:
    rows = []
    for pair in pairs:
        files = three_base.tick_files_for_symbol(pair)
        dates = [three_base.tick_date_from_name(path) for path in files]
        dates = [d for d in dates if d]
        rows.append(
            {
                "symbol": pair,
                "dir_exists": (cont.TICK_ROOT / pair).exists(),
                "files": len(files),
                "first": min(dates) if dates else None,
                "last": max(dates) if dates else None,
                "dates": sorted(dates),
            }
        )
    common = sorted(set.intersection(*(set(r["dates"]) for r in rows if r["dates"]))) if all(r["dates"] for r in rows) else []
    return {"tick_root": str(cont.TICK_ROOT), "symbols": rows, "common_dates": common}


def side_from_engine(row: dict[str, Any]) -> str | None:
    side = row.get("side")
    if side == "buy":
        return "long"
    if side == "sell":
        return "short"
    ev = row.get("engine_vars") or {}
    bias = ev.get("bias_1h")
    slope = safe_float(row.get("slope_1h", row.get("slope_15m")))
    di = safe_float(ev.get("di_spread_1h"))
    if bias == "UP" and (not math.isfinite(di) or di >= 0) and (not math.isfinite(slope) or slope >= 0):
        return "long"
    if bias == "DOWN" and (not math.isfinite(di) or di <= 0) and (not math.isfinite(slope) or slope <= 0):
        return "short"
    if math.isfinite(slope) and abs(slope) >= 25:
        return "long" if slope > 0 else "short"
    if math.isfinite(di) and abs(di) >= 8:
        return "long" if di > 0 else "short"
    return None


def stop_from_bar(rows: list[list[Any]], idx: int, side: str, buffer_pct: float = 0.10) -> float:
    entry = safe_float(rows[idx][4])
    buf = entry * buffer_pct / 100
    if side == "long":
        return safe_float(rows[idx][3]) - buf
    return safe_float(rows[idx][2]) + buf


def atr_price(rows: list[list[Any]], idx: int, period: int = 14) -> float:
    return phase_b.atr_price(rows, idx, period)


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(value * k + out[-1] * (1 - k))
    return out


def simulate_trend_exit(rows: list[list[Any]], idx: int, side: str, exit_name: str) -> dict[str, Any]:
    entry_raw = safe_float(rows[idx][4])
    entry = slipped_entry(entry_raw, side)
    stop = stop_from_bar(rows, idx, side)
    end = min(len(rows) - 1, idx + CONFIG["trend_hold_bars"])
    closes = [safe_float(r[4]) for r in rows[: end + 1]]
    ema20 = ema(closes, 20)
    atr = atr_price(rows, idx)
    trail = stop
    best = 0.0
    worst = 0.0
    outcome = "TIME"
    exit_idx = end
    exit_price = safe_float(rows[end][4])
    k = int(exit_name[-1]) if exit_name.startswith("structure_k") else 0
    atr_mult = float(exit_name.removeprefix("atr")) if exit_name.startswith("atr") else None
    for j in range(idx + 1, end + 1):
        row = rows[j]
        high = safe_float(row[2])
        low = safe_float(row[3])
        close = safe_float(row[4])
        best = max(best, dir_return(entry, high if side == "long" else low, side))
        worst = min(worst, dir_return(entry, low if side == "long" else high, side))
        if atr_mult is not None:
            if side == "long":
                trail = max(trail, close - atr_mult * atr)
            else:
                trail = min(trail, close + atr_mult * atr)
        hit_stop = low <= trail if side == "long" else high >= trail
        if hit_stop:
            outcome = "SL" if trail == stop else f"ATR{int(atr_mult or 0)}"
            exit_idx = j
            exit_price = trail
            break
        if k and exit_rerun.structure_break(rows, j, side, k):
            outcome = f"STRUCT_K{k}"
            exit_idx = j
            exit_price = close
            break
        if exit_name == "ema20_break" and j < len(ema20):
            if (side == "long" and close < ema20[j]) or (side == "short" and close > ema20[j]):
                outcome = "EMA20_BREAK"
                exit_idx = j
                exit_price = close
                break
    gross = dir_return(entry, exit_price, side)
    return {
        "entry": entry,
        "entry_raw": entry_raw,
        "stop": stop,
        "exit_idx": exit_idx,
        "exit_price": exit_price,
        "outcome": outcome,
        "gross_pct": gross,
        "net_pct": gross - CONFIG["fee_pct"],
        "mfe_pct": best,
        "mae_pct": worst,
        "capture_pct": positive_capture(gross, best),
        "hold_min": (exit_idx - idx) * 15,
    }


def movement_available(rows: list[list[Any]], idx: int, side: str, hold_bars: int, basis: float) -> dict[str, Any]:
    end = min(len(rows) - 1, idx + hold_bars)
    best = 0.0
    worst = 0.0
    best_idx = idx
    for j in range(idx, end + 1):
        fav = exit_rerun.favorable_price(rows[j], side)
        adv = exit_rerun.adverse_price(rows[j], side)
        fav_ret = dir_return(basis, fav, side)
        adv_ret = dir_return(basis, adv, side)
        if fav_ret > best:
            best = fav_ret
            best_idx = j
        worst = min(worst, adv_ret)
    return {"available_pct": best, "adverse_pct": worst, "best_idx": best_idx}


def event_period_from_start(event: dict[str, Any], start_ms: int, end_ms: int) -> str:
    mid = start_ms + (end_ms - start_ms) // 2
    return "early" if int(event["start_open_ms"]) + phase_a.TF_MS["15m"] < mid else "late"


def build_trend_rows(events: list[dict[str, Any]], candle_sets: dict[str, phase_a.CandleSet], start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        cell = phase_b.event_cell(event)
        if cell not in {"TRENDING_SWING", "TRENDING_GRIND"} and not (event.get("corrected_regime") == "DRIFT" and event.get("move_type") == "FAST"):
            continue
        candle_set = candle_sets.get(event["symbol"])
        if not candle_set:
            continue
        idx = phase_b.candle_idx(candle_set, "15m", int(event["start_open_ms"]))
        if idx is None:
            continue
        rows15 = candle_set.rows["15m"]
        old_side = phase_b.side_from_structure(rows15, idx, "trend_impulse")
        engine_row = event.get("start_engine") or event.get("engine") or {}
        new_side = side_from_engine(engine_row) or old_side
        if not new_side:
            continue
        for exit_name in CONFIG["trend_exits"]:
            sim = simulate_trend_exit(rows15, idx, new_side, exit_name)
            avail = movement_available(rows15, idx, new_side, CONFIG["trend_hold_bars"], sim["entry_raw"])
            rows.append(
                {
                    "engine": "trend",
                    "symbol": event["symbol"],
                    "ts": event["start_ts"],
                    "idx": idx,
                    "cell": cell,
                    "tier": phase_b.volatility_tier(event["symbol"], candle_set),
                    "period": event_period_from_start(event, start_ms, end_ms),
                    "side": new_side,
                    "old_side": old_side,
                    "event_direction": event["direction"],
                    "exit": exit_name,
                    "old_dir_match": old_side == event["direction"] if old_side else None,
                    "new_dir_match": new_side == event["direction"],
                    "sim": sim,
                    "available": avail,
                    "edge_exists": avail["available_pct"] >= CONFIG["trend_edge_pct"],
                }
            )
    return rows


def bar_body_pct(bar: cont.Bar) -> float:
    return abs(bar.change_pct)


def body_ratio(bars: list[cont.Bar], idx: int) -> float:
    prev = [bar_body_pct(bars[j]) for j in range(max(0, idx - 4), idx)]
    avg_prev = average(prev)
    return bar_body_pct(bars[idx]) / avg_prev if avg_prev > 0 else float("nan")


def volume_ratio(bars: list[cont.Bar], idx: int) -> float:
    prev = [bars[j].volume for j in range(max(0, idx - 20), idx)]
    avg_prev = average(prev)
    return bars[idx].volume / avg_prev if avg_prev > 0 else float("nan")


def ticks_for_window(ticks: dict[int, list[tuple[int, float]]], start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    out = []
    minute = start_ms - start_ms % 60000
    while minute <= end_ms:
        out.extend((ts, price) for ts, price in ticks.get(minute, []) if start_ms <= ts <= end_ms)
        minute += 60000
    return sorted(out)


def find_tick_entry(bar: cont.Bar, ticks: dict[int, list[tuple[int, float]]], direction: str, window_sec: int) -> tuple[int, float] | None:
    for ts_ms, price in ticks_for_window(ticks, bar.minute_ms, bar.minute_ms + window_sec * 1000):
        if dir_return(bar.open, price, direction) >= CONFIG["impulse_trigger_pct"]:
            return ts_ms, price
    return None


def impulse_stop(bar: cont.Bar, direction: str) -> float:
    buf = bar.open * 0.001
    return bar.low - buf if direction == "long" else bar.high + buf


def simulate_impulse_exit(
    entry_ts: int,
    entry_price_raw: float,
    direction: str,
    signal_bar: cont.Bar,
    bars_by_minute: dict[int, cont.Bar],
    ticks: dict[int, list[tuple[int, float]]],
    exit_name: str,
) -> dict[str, Any]:
    entry = slipped_entry(entry_price_raw, direction)
    stop = impulse_stop(signal_bar, direction)
    end_ms = entry_ts + CONFIG["impulse_hold_min"] * 60000
    event_ticks = ticks_for_window(ticks, entry_ts, end_ms)
    best = 0.0
    worst = 0.0
    best_seen = 0.0
    outcome = "TIME"
    exit_ts = end_ms
    exit_price = entry_price_raw
    k = int(exit_name[-1]) if exit_name.startswith("structure_k") else 0
    giveback = int(exit_name.removeprefix("giveback_")) if exit_name.startswith("giveback_") else None
    for ts_ms, price in event_ticks:
        ret = dir_return(entry, price, direction)
        best = max(best, ret)
        worst = min(worst, ret)
        best_seen = max(best_seen, ret)
        hit_stop = price <= stop if direction == "long" else price >= stop
        if hit_stop:
            outcome = "SL"
            exit_ts = ts_ms
            exit_price = stop
            break
        if giveback is not None and best_seen > 0 and best_seen - ret >= best_seen * giveback / 100:
            outcome = f"GIVEBACK_{giveback}"
            exit_ts = ts_ms
            exit_price = price
            break
        if k:
            minute = ts_ms - ts_ms % 60000
            closes = []
            for back in range(k + 1):
                bar = bars_by_minute.get(minute - back * 60000)
                if bar:
                    closes.append(bar)
            if len(closes) >= k + 1:
                cur = closes[0].close
                prev = closes[1:]
                if direction == "long" and cur < min(b.low for b in prev):
                    outcome = f"STRUCT_K{k}"
                    exit_ts = ts_ms
                    exit_price = price
                    break
                if direction == "short" and cur > max(b.high for b in prev):
                    outcome = f"STRUCT_K{k}"
                    exit_ts = ts_ms
                    exit_price = price
                    break
        exit_price = price
    gross = dir_return(entry, exit_price, direction)
    return {
        "entry": entry,
        "entry_raw": entry_price_raw,
        "stop": stop,
        "exit_ts": exit_ts,
        "exit_price": exit_price,
        "outcome": outcome,
        "gross_pct": gross,
        "net_pct": gross - CONFIG["fee_pct"],
        "mfe_pct": best,
        "mae_pct": worst,
        "capture_pct": positive_capture(gross, best),
        "hold_min": (exit_ts - entry_ts) / 60000,
    }


def build_impulse_rows(coverage: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    pair_stats = {}
    exit_names = ["structure_k1", "structure_k2", "structure_k3", "giveback_30", "giveback_40", "giveback_50"]
    for pair in CONFIG["impulse_pairs"]:
        if not (cont.TICK_ROOT / pair).exists():
            continue
        log(f"impulse aggregate {pair}")
        bars, ticks = cont.aggregate_pair(pair, keep_ticks=True)
        bars_by_minute = {bar.minute_ms: bar for bar in bars}
        pair_stats[pair] = {"bars": len(bars), "tick_minutes": len(ticks)}
        last_event_ms = -10**18
        for idx, bar in enumerate(bars):
            if idx < 20 or bar.minute_ms - last_event_ms < CONFIG["impulse_cooldown_min"] * 60000:
                continue
            br = body_ratio(bars, idx)
            vr = volume_ratio(bars, idx)
            move = abs(bar.change_pct)
            if not all(math.isfinite(v) for v in [br, vr, move]):
                continue
            direction = "long" if bar.change_pct > 0 else "short"
            if move < min(CONFIG["impulse_min_move_pct"]) or br < min(CONFIG["impulse_body_ratio"]) or vr < min(CONFIG["impulse_volume_ratio"]):
                continue
            event_ticks = ticks_for_window(ticks, bar.minute_ms, bar.minute_ms + CONFIG["impulse_hold_min"] * 60000)
            if not event_ticks:
                continue
            available = max([dir_return(bar.open, price, direction) for _, price in event_ticks] or [0.0])
            tick_entries = {
                window_sec: find_tick_entry(bar, ticks, direction, window_sec)
                for window_sec in CONFIG["impulse_entry_windows_sec"]
            }
            if not any(tick_entries.values()):
                continue
            sims_by_entry_exit: dict[tuple[int, str], dict[str, Any]] = {}
            for min_move in [v for v in CONFIG["impulse_min_move_pct"] if move >= v]:
                if move < min_move:
                    continue
                for min_body_ratio in [v for v in CONFIG["impulse_body_ratio"] if br >= v]:
                    for min_vol_ratio in [v for v in CONFIG["impulse_volume_ratio"] if vr >= v]:
                        for window_sec, tick_entry in tick_entries.items():
                            if tick_entry is None:
                                continue
                            entry_ts, entry_raw = tick_entry
                            lag = dir_return(bar.open, entry_raw, direction)
                            for exit_name in exit_names:
                                sim_key = (entry_ts, exit_name)
                                sim = sims_by_entry_exit.get(sim_key)
                                if sim is None:
                                    sim = simulate_impulse_exit(entry_ts, entry_raw, direction, bar, bars_by_minute, ticks, exit_name)
                                    sims_by_entry_exit[sim_key] = sim
                                rows.append(
                                    {
                                        "engine": "impulse",
                                        "symbol": pair,
                                        "ts": bar.ts,
                                        "minute_ms": bar.minute_ms,
                                        "side": direction,
                                        "tier": "tick_vol_alt",
                                        "date": bar.date,
                                        "window_sec": window_sec,
                                        "min_move": min_move,
                                        "body_ratio_min": min_body_ratio,
                                        "volume_ratio_min": min_vol_ratio,
                                        "body_ratio": br,
                                        "volume_ratio": vr,
                                        "signal_move_pct": bar.change_pct,
                                        "entry_lag_pct": lag,
                                        "exit": exit_name,
                                        "available": {"available_pct": available},
                                        "edge_exists": available >= CONFIG["impulse_trigger_pct"],
                                        "sim": sim,
                                    }
                                )
            last_event_ms = bar.minute_ms
        log(f"impulse {pair}: rows_so_far={len(rows)}")
    return rows, pair_stats


def bb_values(row: dict[str, Any]) -> tuple[float, float, float, float]:
    h15 = ((row.get("indicators") or {}).get("15m") or {})
    upper = safe_float(h15.get("bb_upper"))
    middle = safe_float(h15.get("bb_middle"))
    lower = safe_float(h15.get("bb_lower"))
    close = safe_float(h15.get("close", (row.get("engine_vars") or {}).get("close")))
    return upper, middle, lower, close


def simulate_fade(rows: list[list[Any]], idx: int, side: str, engine_row: dict[str, Any], target_name: str) -> dict[str, Any] | None:
    upper, middle, lower, _ = bb_values(engine_row)
    if not all(math.isfinite(v) and v > 0 for v in [upper, middle, lower]):
        return None
    entry_raw = safe_float(rows[idx][4])
    entry = slipped_entry(entry_raw, side)
    stop = lower - entry_raw * 0.0012 if side == "long" else upper + entry_raw * 0.0012
    if target_name == "middle":
        target = middle
    elif target_name == "opposite":
        target = upper if side == "long" else lower
    else:
        target = None
    end = min(len(rows) - 1, idx + CONFIG["fade_hold_bars"])
    best = 0.0
    worst = 0.0
    best_seen = 0.0
    outcome = "TIME"
    exit_idx = end
    exit_price = safe_float(rows[end][4])
    for j in range(idx + 1, end + 1):
        row = rows[j]
        high = safe_float(row[2])
        low = safe_float(row[3])
        close = safe_float(row[4])
        fav = high if side == "long" else low
        adv = low if side == "long" else high
        ret_close = dir_return(entry, close, side)
        best = max(best, dir_return(entry, fav, side))
        worst = min(worst, dir_return(entry, adv, side))
        best_seen = max(best_seen, dir_return(entry, fav, side))
        if (side == "long" and low <= stop) or (side == "short" and high >= stop):
            outcome = "SL"
            exit_idx = j
            exit_price = stop
            break
        if target is not None:
            if (side == "long" and high >= target) or (side == "short" and low <= target):
                outcome = target_name.upper()
                exit_idx = j
                exit_price = target
                break
        elif best_seen > 0 and best_seen - ret_close >= best_seen * 0.4:
            outcome = "GIVEBACK_40"
            exit_idx = j
            exit_price = close
            break
    gross = dir_return(entry, exit_price, side)
    risk = abs(entry - stop)
    r_mid = abs(middle - entry) / risk if risk > 0 else float("nan")
    return {
        "entry": entry,
        "entry_raw": entry_raw,
        "stop": stop,
        "target": target,
        "exit_idx": exit_idx,
        "exit_price": exit_price,
        "outcome": outcome,
        "gross_pct": gross,
        "net_pct": gross - CONFIG["fee_pct"],
        "mfe_pct": best,
        "mae_pct": worst,
        "capture_pct": positive_capture(gross, best),
        "r_to_mid": r_mid,
        "hold_min": (exit_idx - idx) * 15,
    }


def build_fade_rows(events: list[dict[str, Any]], candle_sets: dict[str, phase_a.CandleSet], start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        if phase_b.event_cell(event) != "RANGING":
            continue
        candle_set = candle_sets.get(event["symbol"])
        if not candle_set:
            continue
        idx = phase_b.candle_idx(candle_set, "15m", int(event["start_open_ms"]))
        if idx is None:
            continue
        engine_row = event.get("start_engine") or event.get("engine") or {}
        upper, middle, lower, close = bb_values(engine_row)
        if not all(math.isfinite(v) and v > 0 for v in [upper, middle, lower, close]) or upper <= lower:
            continue
        bb_pos = (close - lower) / (upper - lower)
        adx = safe_float(engine_row.get("adx_1h", (engine_row.get("engine_vars") or {}).get("adx_1h")))
        width = (upper - lower) / middle * 100 if middle > 0 else float("nan")
        for tol in CONFIG["fade_touch_tolerance_pct"]:
            side = "short" if bb_pos >= 1 - tol else "long" if bb_pos <= tol else None
            if side is None:
                continue
            for adx_max in CONFIG["fade_adx_max"]:
                if math.isfinite(adx) and adx > adx_max:
                    continue
                for width_max in CONFIG["fade_bb_width_max"]:
                    if math.isfinite(width) and width > width_max:
                        continue
                    for target in CONFIG["fade_targets"]:
                        sim = simulate_fade(candle_set.rows["15m"], idx, side, engine_row, target)
                        if sim is None:
                            continue
                        avail = movement_available(candle_set.rows["15m"], idx, side, CONFIG["fade_hold_bars"], sim["entry_raw"])
                        rows.append(
                            {
                                "engine": "fade",
                                "symbol": event["symbol"],
                                "ts": event["start_ts"],
                                "idx": idx,
                                "side": side,
                                "tier": phase_b.volatility_tier(event["symbol"], candle_set),
                                "period": event_period_from_start(event, start_ms, end_ms),
                                "tol": tol,
                                "adx_max": adx_max,
                                "bb_width_max": width_max,
                                "target": target,
                                "exit": f"tol{tol}_adx{adx_max}_bb{width_max}_{target}",
                                "bb_pos": bb_pos,
                                "bb_width": width,
                                "adx_1h": adx,
                                "sim": sim,
                                "available": avail,
                                "edge_exists": sim["outcome"] in {"MIDDLE", "OPPOSITE", "GIVEBACK_40"},
                            }
                        )
    return rows


def empty_accum() -> dict[str, Any]:
    return {
        "n": 0,
        "wins": 0,
        "net": [],
        "capture": [],
        "available": [],
        "hold": [],
        "entry_lag": [],
        "r_to_mid": [],
        "edge": 0,
        "old_dir_known": 0,
        "old_dir_match": 0,
        "new_dir_known": 0,
        "new_dir_match": 0,
        "outcomes": Counter(),
    }


def update_accum(acc: dict[str, Any], row: dict[str, Any]) -> None:
    sim = row.get("sim") or {}
    avail = row.get("available") or {}
    acc["n"] += 1
    net = safe_float(sim.get("net_pct"))
    acc["wins"] += 1 if net > 0 else 0
    acc["net"].append(net)
    acc["capture"].append(safe_float(sim.get("capture_pct")))
    acc["available"].append(safe_float(avail.get("available_pct")))
    acc["hold"].append(safe_float(sim.get("hold_min")))
    acc["entry_lag"].append(safe_float(row.get("entry_lag_pct")))
    acc["r_to_mid"].append(safe_float(sim.get("r_to_mid")))
    acc["edge"] += 1 if row.get("edge_exists") else 0
    acc["outcomes"][sim.get("outcome") or "UNKNOWN"] += 1
    if row.get("old_dir_match") is not None:
        acc["old_dir_known"] += 1
        acc["old_dir_match"] += 1 if row["old_dir_match"] else 0
    if row.get("new_dir_match") is not None:
        acc["new_dir_known"] += 1
        acc["new_dir_match"] += 1 if row["new_dir_match"] else 0


def finalize_accum(acc: dict[str, Any]) -> dict[str, Any]:
    n = len([v for v in acc["net"] if math.isfinite(v)])
    return {
        "n": n,
        "avg_net_pct": average(acc["net"]),
        "win_rate": pct(acc["wins"], n),
        "avg_capture_pct": average(acc["capture"]),
        "avg_available_pct": average(acc["available"]),
        "avg_hold_min": average(acc["hold"]),
        "avg_entry_lag_pct": average(acc["entry_lag"]),
        "avg_r_to_mid": average(acc["r_to_mid"]),
        "edge_exists_rate": pct(acc["edge"], n),
        "old_dir_match_rate": pct(acc["old_dir_match"], acc["old_dir_known"]),
        "new_dir_match_rate": pct(acc["new_dir_match"], acc["new_dir_known"]),
        "outcomes": dict(acc["outcomes"]),
    }


def summarize(rows: list[dict[str, Any]], group_keys: list[str]) -> list[dict[str, Any]]:
    accum: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(empty_accum)
    for row in rows:
        key = tuple(row.get(k, "all") for k in group_keys)
        update_accum(accum[key], row)
    out = []
    for key, acc in accum.items():
        payload = {group_keys[i]: key[i] for i in range(len(group_keys))}
        payload.update(finalize_accum(acc))
        out.append(payload)
    out.sort(key=lambda r: tuple(str(r.get(k)) for k in group_keys) + (-safe_float(r["avg_net_pct"]),))
    return out


def best_rows(summary: list[dict[str, Any]], sort_key: str = "avg_net_pct", limit: int = 15, min_n: int = 20) -> list[dict[str, Any]]:
    rows = [r for r in summary if r["n"] >= min_n]
    rows.sort(key=lambda r: safe_float(r.get(sort_key)), reverse=True)
    return rows[:limit]


def go_no_go(row: dict[str, Any]) -> str:
    if row["n"] < 20:
        return "NO-GO: sample<20"
    if safe_float(row["avg_net_pct"]) <= 0:
        return "NO-GO: net<=0"
    return "RESEARCH: split-check needed"


def render_case(path: Path, row: dict[str, Any], candle_set: phase_a.CandleSet | None = None) -> None:
    if row["engine"] == "impulse":
        return
    rows = candle_set.rows["15m"] if candle_set else []
    idx = int(row["idx"])
    sim = row["sim"]
    avail = row.get("available") or {}
    start = max(0, idx - 10)
    end = min(len(rows) - 1, max(int(sim.get("exit_idx", idx)), int(avail.get("best_idx", idx))) + 8)
    subset = rows[start : end + 1]
    x0 = idx - start
    fig, ax = plt.subplots(figsize=(13, 6))
    for i, cndl in enumerate(subset):
        o, h, l, c = map(safe_float, cndl[1:5])
        color = "#15936b" if c >= o else "#c23b3b"
        ax.vlines(i, l, h, color=color, linewidth=0.9)
        ax.add_patch(patches.Rectangle((i - 0.35, min(o, c)), 0.7, max(abs(c - o), (h - l) * 0.02), color=color, alpha=0.78))
    ax.scatter([x0], [sim["entry_raw"]], color="#0b5bd3", s=42, label="entry", zorder=8)
    ax.axhline(sim["stop"], color="#d62728", linestyle="--", linewidth=0.8, label="stop")
    if sim.get("target"):
        ax.axhline(sim["target"], color="#2ca02c", linestyle="--", linewidth=0.8, label="target")
    ax.axvline(int(sim.get("exit_idx", idx)) - start, color="#111111", linestyle=":", linewidth=1.0, label="exit")
    ax.set_title(f"{row['engine']} {row['symbol']} {row['ts']} {row.get('side')} | {row.get('exit')} net {fmt(sim.get('net_pct'), '%')} cap {fmt(sim.get('capture_pct'), '%')}", fontsize=9, loc="left")
    ax.grid(alpha=0.18)
    ax.legend(loc="best", fontsize=8)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def render_impulse_case(path: Path, row: dict[str, Any]) -> None:
    # Tick impulse cases are summarized as compact 1m charts from the tape-derived bars.
    pair = row["symbol"]
    bars, _ = cont.aggregate_pair(pair, keep_ticks=False)
    idx_by_ts = {bar.minute_ms: i for i, bar in enumerate(bars)}
    idx = idx_by_ts.get(int(row["minute_ms"]))
    if idx is None:
        return
    start = max(0, idx - 8)
    end = min(len(bars) - 1, idx + 30)
    subset = bars[start : end + 1]
    x0 = idx - start
    sim = row["sim"]
    fig, ax = plt.subplots(figsize=(13, 6))
    for i, bar in enumerate(subset):
        color = "#15936b" if bar.close >= bar.open else "#c23b3b"
        ax.vlines(i, bar.low, bar.high, color=color, linewidth=0.9)
        ax.add_patch(patches.Rectangle((i - 0.35, min(bar.open, bar.close)), 0.7, max(abs(bar.close - bar.open), (bar.high - bar.low) * 0.02), color=color, alpha=0.78))
    ax.axvline(x0, color="#6f42c1", linestyle=":", linewidth=1.0, label="impulse minute")
    ax.axhline(sim["entry_raw"], color="#0b5bd3", linestyle="--", linewidth=0.8, label="tick entry")
    ax.axhline(sim["stop"], color="#d62728", linestyle="--", linewidth=0.8, label="stop")
    ax.set_title(f"impulse {pair} {row['ts']} {row['side']} win {row['window_sec']}s | net {fmt(sim.get('net_pct'), '%')} cap {fmt(sim.get('capture_pct'), '%')}", fontsize=9, loc="left")
    ax.grid(alpha=0.18)
    ax.legend(loc="best", fontsize=8)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_cases(trend_rows: list[dict[str, Any]], impulse_rows: list[dict[str, Any]], fade_rows: list[dict[str, Any]], candle_sets: dict[str, phase_a.CandleSet]) -> None:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    for old in CASE_DIR.glob(f"*_{SUFFIX}.png"):
        old.unlink()
    for engine, rows in [("trend", trend_rows), ("impulse", impulse_rows), ("fade", fade_rows)]:
        rows = sorted(rows, key=lambda r: safe_float((r.get("sim") or {}).get("net_pct")), reverse=True)
        seen = set()
        picked = []
        for row in rows:
            key = (row["symbol"], row["ts"])
            if key in seen:
                continue
            seen.add(key)
            picked.append(row)
            if len(picked) >= CONFIG["case_limit_per_engine"]:
                break
        for i, row in enumerate(picked, start=1):
            safe_ts = row["ts"].replace(":", "").replace("-", "").replace("Z", "")
            path = CASE_DIR / f"{engine}_{i:02d}_{row['symbol']}_{safe_ts}_{SUFFIX}.png"
            if engine == "impulse":
                render_impulse_case(path, row)
            else:
                render_case(path, row, candle_sets.get(row["symbol"]))


def report_section(title: str, rows: list[dict[str, Any]], keys: list[str]) -> list[str]:
    table = []
    for row in rows:
        table.append(
            [row.get(k, "") for k in keys]
            + [
                row["n"],
                fmt(row["avg_net_pct"], "%"),
                fmt(row["win_rate"], "%"),
                fmt(row["avg_capture_pct"], "%"),
                fmt(row["avg_available_pct"], "%"),
                fmt(row["edge_exists_rate"], "%"),
                fmt(row["avg_hold_min"], "m"),
                go_no_go(row),
            ]
        )
    lines = [f"## {title}", ""]
    lines.extend(render_table(keys + ["n", "net", "WR", "capture", "available", "edge", "hold", "verdict"], table))
    return lines


def split_section(title: str, rows: list[dict[str, Any]], keys: list[str], limit: int = 30) -> list[str]:
    table = []
    for row in rows[:limit]:
        table.append(
            [row.get(k, "") for k in keys]
            + [
                row["n"],
                fmt(row["avg_net_pct"], "%"),
                fmt(row["win_rate"], "%"),
                fmt(row["avg_capture_pct"], "%"),
                fmt(row["avg_available_pct"], "%"),
            ]
        )
    lines = [f"## {title}", ""]
    lines.extend(render_table(keys + ["n", "net", "WR", "capture", "available"], table))
    return lines


def render_report(
    impulse_summary: list[dict[str, Any]],
    trend_summary: list[dict[str, Any]],
    fade_summary: list[dict[str, Any]],
    split_summaries: dict[str, list[dict[str, Any]]],
    trend_dir_summary: dict[str, Any],
    coverage: dict[str, Any],
    candle_cov: dict[str, Any],
    pair_stats: dict[str, Any],
) -> str:
    impulse_top = best_rows(impulse_summary, limit=20, min_n=10)
    trend_top = best_rows(trend_summary, limit=20, min_n=20)
    fade_top = best_rows(fade_summary, limit=20, min_n=10)
    lines = [
        "# Three Engines Polish - 22.05.2026",
        "",
        "This is a separate polish sweep. It does not overwrite the previous three-engine report and does not touch production code.",
        "",
        "## Coverage",
        "",
        f"- candle symbols loaded for trend/fade: `{candle_cov['loaded_symbols']}`",
        f"- impulse tick root: `{coverage['tick_root']}`",
        f"- impulse pairs: `{', '.join(CONFIG['impulse_pairs'])}`",
        f"- common tick dates across impulse pairs: `{', '.join(coverage['common_dates']) or 'none'}`",
        f"- tick bars loaded by pair: `{pair_stats}`",
        "",
        "## Direction Fix Check",
        "",
        f"- old structural trend direction match: `{fmt(trend_dir_summary['old_dir_match_rate'], '%')}`",
        f"- new compute-signal-derived trend direction match: `{fmt(trend_dir_summary['new_dir_match_rate'], '%')}`",
        "",
        "## Impulse Detection Conditions",
        "",
        "- Tape-only, selected volatile alts with real tick files; no candle proxy.",
        "- Sweep: trigger windows 10/20/60/120/300 sec, min 1m move 0.6/0.8/1.0%, body ratio 1.2/1.5/2.0x, volume ratio 1.0/1.5/2.0x, exits structure k1/k2/k3 and giveback 30/40/50.",
        "",
    ]
    lines.extend(report_section("Impulse Sweep Top", impulse_top, ["window_sec", "min_move", "body_ratio_min", "volume_ratio_min", "exit"]))
    lines.extend([""])
    lines.extend(split_section("Impulse Side Split Top", split_summaries["impulse_side"], ["side", "window_sec", "exit"], limit=16))
    lines.extend([""])
    lines.extend(split_section("Impulse Pair Split Top", split_summaries["impulse_pair"], ["symbol", "window_sec", "exit"], limit=16))
    lines.extend(["", "## Trend Detection Conditions", ""])
    lines.extend(
        [
            "- Corrected TRENDING swing/grind plus DRIFT FAST routed to trend.",
            "- Direction is taken from replayed `compute_signal` side/bias/DI/slope, not from the previous structural reimplementation.",
            "- Exit sweep: structure k1/k2/k3, ATR2/ATR3 wide trail, EMA20 break.",
            "",
        ]
    )
    lines.extend(report_section("Trend Sweep Top", trend_top, ["exit"]))
    lines.extend([""])
    lines.extend(split_section("Trend Side Split", split_summaries["trend_side"], ["side", "exit"], limit=16))
    lines.extend([""])
    lines.extend(split_section("Trend Volatility Tier Split", split_summaries["trend_tier"], ["tier", "exit"], limit=16))
    lines.extend([""])
    lines.extend(split_section("Trend Early/Late Split", split_summaries["trend_period"], ["period", "exit"], limit=16))
    lines.extend(["", "## Fade Detection Conditions", ""])
    lines.extend(
        [
            "- Corrected RANGING only; BB boundary touch tolerance sweep; target middle/opposite/giveback.",
            "- Uses raw float levels and significant-value rendering; no fixed-decimal rounding on cheap coins.",
            "- Activation sweep: ADX max, BB width max, boundary tolerance.",
            "",
        ]
    )
    lines.extend(report_section("Fade Sweep Top", fade_top, ["tol", "adx_max", "bb_width_max", "target"]))
    lines.extend([""])
    lines.extend(split_section("Fade Side Split", split_summaries["fade_side"], ["side", "tol", "target"], limit=16))
    lines.extend([""])
    lines.extend(split_section("Fade Volatility Tier Split", split_summaries["fade_tier"], ["tier", "tol", "target"], limit=16))
    lines.extend([""])
    lines.extend(split_section("Fade Early/Late Split", split_summaries["fade_period"], ["period", "tol", "target"], limit=16))
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            "- Impulse is now measured on the tick period where data exists. Treat rows with small n as research only even if net is positive.",
            "- Trend direction improves only if the compute-signal-derived side beats the old structural side; the report shows both rates explicitly.",
            "- Fade is the closest branch, but it still needs side/time stability and enough sample after tightening activation.",
            "",
            "## GPT Hypotheses",
            "",
            "- The profitable impulse rows should concentrate in shorter trigger windows and short side if the trader's hypothesis is right.",
            "- Trend ride can have large winners, but direction quality is the binding constraint; exit tuning cannot fix wrong side.",
            "- Fade should prefer mid-vol ranges with BB-middle targets; opposite-band targets likely raise capture but may lower win rate.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")
    candle_sets, decisions, events, start_ms, end_ms = load_replay()
    candle_cov = candle_coverage(candle_sets)
    coverage = tick_coverage_for_pairs(CONFIG["impulse_pairs"])
    log("running impulse tick sweep")
    impulse_rows, pair_stats = build_impulse_rows(coverage)
    log(f"impulse rows={len(impulse_rows)}")
    log("running trend sweep")
    trend_rows = build_trend_rows(events, candle_sets, start_ms, end_ms)
    log(f"trend rows={len(trend_rows)}")
    log("running fade sweep")
    fade_rows = build_fade_rows(events, candle_sets, start_ms, end_ms)
    log(f"fade rows={len(fade_rows)}")
    impulse_summary = summarize(impulse_rows, ["window_sec", "min_move", "body_ratio_min", "volume_ratio_min", "exit"])
    trend_summary = summarize(trend_rows, ["exit"])
    fade_summary = summarize(fade_rows, ["tol", "adx_max", "bb_width_max", "target"])
    split_summaries = {
        "impulse_side": best_rows(summarize(impulse_rows, ["side", "window_sec", "exit"]), limit=40, min_n=20),
        "impulse_pair": best_rows(summarize(impulse_rows, ["symbol", "window_sec", "exit"]), limit=40, min_n=20),
        "trend_side": best_rows(summarize(trend_rows, ["side", "exit"]), limit=40, min_n=20),
        "trend_tier": best_rows(summarize(trend_rows, ["tier", "exit"]), limit=40, min_n=20),
        "trend_period": best_rows(summarize(trend_rows, ["period", "exit"]), limit=40, min_n=20),
        "fade_side": best_rows(summarize(fade_rows, ["side", "tol", "target"]), limit=40, min_n=10),
        "fade_tier": best_rows(summarize(fade_rows, ["tier", "tol", "target"]), limit=40, min_n=10),
        "fade_period": best_rows(summarize(fade_rows, ["period", "tol", "target"]), limit=40, min_n=10),
    }
    trend_dir = summarize(trend_rows, ["engine"])[0] if trend_rows else {}
    generate_cases(trend_rows, impulse_rows, fade_rows, candle_sets)
    REPORT_MD.write_text(render_report(impulse_summary, trend_summary, fade_summary, split_summaries, trend_dir, coverage, candle_cov, pair_stats), encoding="utf-8")
    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": CONFIG,
        "trend_fade_period": {"start": iso_from_ms(start_ms), "end": iso_from_ms(end_ms)},
        "candle_coverage": candle_cov,
        "tick_coverage": coverage,
        "pair_stats": pair_stats,
        "row_counts": {"impulse": len(impulse_rows), "trend": len(trend_rows), "fade": len(fade_rows)},
        "trend_direction_summary": trend_dir,
        "impulse_summary": impulse_summary,
        "trend_summary": trend_summary,
        "fade_summary": fade_summary,
        "split_summaries": split_summaries,
        "case_dir": str(CASE_DIR),
    }
    SUMMARY_JSON.write_text(json.dumps(json_safe(payload), ensure_ascii=True, indent=2, allow_nan=False), encoding="utf-8")
    log(f"saved {REPORT_MD}")
    log(f"saved {SUMMARY_JSON}")
    log(f"saved cases to {CASE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
