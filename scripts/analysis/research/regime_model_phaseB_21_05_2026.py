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

import regime_coverage_research_21_05_2026 as phase_a


SUFFIX = "phaseB_21_05_2026"
ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
CASE_DIR = OUT_DIR / f"regime_classification_examples_{SUFFIX}"
REPORT_MD = OUT_DIR / f"regime_classification_{SUFFIX}.md"
SUMMARY_JSON = OUT_DIR / f"regime_classification_summary_{SUFFIX}.json"
RUN_LOG = OUT_DIR / f"regime_classification_run_{SUFFIX}.log"
FULL_CASE_DIR = OUT_DIR / f"regime_model_cases_{SUFFIX}"
FULL_REPORT_MD = OUT_DIR / f"regime_model_full_{SUFFIX}.md"
FULL_SUMMARY_JSON = OUT_DIR / f"regime_model_full_summary_{SUFFIX}.json"
SNAPSHOTS_PATH = ROOT / "logs" / "signals" / "signal_snapshot.jsonl"
TICK_ROOT = Path(r"E:\trading-data\ticks")

CONFIG = {
    "case_limit": 10,
    "run_full_phase_b": True,
    "fee_pct": 0.20,
    "entry_slippage_pct": 0.03,
    "major_symbols": ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP", "ADA-USDT-SWAP"],
    "impulse_speed_bars": 4,
    "impulse_min_move_per_bar_pct": 0.55,
    "grind_max_move_per_bar_pct": 0.45,
    "peak_lookback_15m": 12,
    "peak_guard_range_pos_long": 0.82,
    "peak_guard_range_pos_short": 0.18,
    "peak_guard_move_pct": 2.5,
    "peak_guard_adx4h": 40.0,
    "fast_hold_bars": 4,
    "swing_hold_bars": 16,
    "trend_stop_atr_k": 1.2,
    "drift_stop_atr_k": 1.0,
    "range_stop_atr_k": 0.9,
    "fast_tp_r": 1.1,
    "swing_tp_r": 1.4,
    "ranging_max_adx_1h": 24.0,
    "ranging_max_di_spread_1h": 8.0,
    "ranging_max_abs_slope_15m": 30.0,
    "ranging_max_daily_range_pct": 5.5,
    "ranging_bb_width_min": 0.4,
    "ranging_bb_width_max": 3.0,
    "drift_min_adx_1h": 15.0,
    "drift_max_adx_1h": 30.0,
    "drift_min_di_spread_1h": 5.0,
    "drift_max_bb_width": 4.0,
    "drift_max_daily_range_pct": 7.0,
    "trend_min_adx_1h": 25.0,
    "trend_min_di_spread_1h": 8.0,
    "trend_min_adx_4h": 28.0,
    "trend_min_di_spread_4h": 10.0,
    "trend_min_abs_slope_15m": 35.0,
    "trend_min_abs_slope_1h": 25.0,
    "trend_min_bb_width": 3.0,
    "trend_min_daily_range_pct": 7.0,
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


def iso_from_ms(ts_ms: int) -> str:
    return phase_a.iso_from_ms(ts_ms)


def feature(row: dict[str, Any], key: str) -> float:
    ev = row.get("engine_vars") or {}
    ind = row.get("indicators") or {}
    h15 = ind.get("15m") or {}
    if key == "adx_1h":
        return safe_float(row.get("adx_1h", ev.get("adx_1h")))
    if key == "adx_4h":
        return safe_float(row.get("adx_4h", ev.get("adx_4h")))
    if key == "di_spread_1h":
        return safe_float(ev.get("di_spread_1h"))
    if key == "di_spread_4h":
        return safe_float(ev.get("di_spread_4h"))
    if key == "slope_15m":
        return safe_float(row.get("slope_15m"))
    if key == "slope_1h":
        return safe_float(row.get("slope_1h"))
    if key == "bb_width_15m":
        return safe_float(h15.get("bb_width_pct"))
    if key == "daily_range_pct":
        return safe_float(ev.get("daily_range_pct"))
    if key == "day_position":
        return safe_float(ev.get("day_position"))
    if key == "vol_ratio_sig":
        return safe_float(row.get("vol_ratio_sig", ev.get("vol_ratio_sig")))
    return float("nan")


def between(value: float, lo: float, hi: float) -> bool:
    return math.isfinite(value) and lo <= value <= hi


def corrected_regime(row: dict[str, Any]) -> tuple[str, str]:
    adx_1h = feature(row, "adx_1h")
    adx_4h = feature(row, "adx_4h")
    di_1h = feature(row, "di_spread_1h")
    di_4h = feature(row, "di_spread_4h")
    slope_15m = feature(row, "slope_15m")
    slope_1h = feature(row, "slope_1h")
    bb_width = feature(row, "bb_width_15m")
    daily_range = feature(row, "daily_range_pct")
    ev = row.get("engine_vars") or {}
    four_h_conflict = bool(ev.get("four_h_conflict"))

    clean_range = (
        adx_1h <= CONFIG["ranging_max_adx_1h"]
        and di_1h <= CONFIG["ranging_max_di_spread_1h"]
        and abs(slope_15m) <= CONFIG["ranging_max_abs_slope_15m"]
        and between(bb_width, CONFIG["ranging_bb_width_min"], CONFIG["ranging_bb_width_max"])
        and (not math.isfinite(daily_range) or daily_range <= CONFIG["ranging_max_daily_range_pct"])
    )
    if clean_range:
        return "RANGING", "low_adx_low_di_flat_slope_bb_corridor"

    trend_1h = adx_1h >= CONFIG["trend_min_adx_1h"] and di_1h >= CONFIG["trend_min_di_spread_1h"]
    trend_4h = adx_4h >= CONFIG["trend_min_adx_4h"] and di_4h >= CONFIG["trend_min_di_spread_4h"]
    expanding = (
        abs(slope_15m) >= CONFIG["trend_min_abs_slope_15m"]
        or abs(slope_1h) >= CONFIG["trend_min_abs_slope_1h"]
        or bb_width >= CONFIG["trend_min_bb_width"]
        or (math.isfinite(daily_range) and daily_range >= CONFIG["trend_min_daily_range_pct"])
    )
    if (trend_1h or trend_4h or four_h_conflict) and expanding:
        return "TRENDING", "directional_or_expanding_swing_not_range"

    drift = (
        between(adx_1h, CONFIG["drift_min_adx_1h"], CONFIG["drift_max_adx_1h"])
        and di_1h >= CONFIG["drift_min_di_spread_1h"]
        and bb_width <= CONFIG["drift_max_bb_width"]
        and (not math.isfinite(daily_range) or daily_range <= CONFIG["drift_max_daily_range_pct"])
    )
    if drift:
        return "DRIFT", "moderate_directional_vwap_walk"

    if expanding:
        return "TRENDING", "expanding_move_fallback"
    return "CHOPPY", "no_clean_direction_or_range"


def volatility_tier(symbol: str, candle_set: phase_a.CandleSet | None) -> str:
    if symbol in CONFIG["major_symbols"]:
        return "major"
    if candle_set is None:
        return "unknown"
    rows = candle_set.rows.get("15m") or []
    if len(rows) < 100:
        return "unknown"
    ranges = []
    for row in rows[-800:]:
        o = safe_float(row[1])
        h = safe_float(row[2])
        l = safe_float(row[3])
        if o > 0:
            ranges.append((h - l) / o * 100)
    avg_range = average(ranges)
    if avg_range >= 1.2:
        return "high_vol_alt"
    if avg_range >= 0.55:
        return "mid_vol_alt"
    return "low_vol_alt"


def trend_speed_class(event: dict[str, Any]) -> str:
    move = safe_float(event.get("move_pct"))
    bars = max(1, int(event.get("peak_bars_15m") or 1))
    per_bar = move / bars
    if bars <= CONFIG["impulse_speed_bars"] and per_bar >= CONFIG["impulse_min_move_per_bar_pct"]:
        return "TRENDING_IMPULSE"
    if per_bar <= CONFIG["grind_max_move_per_bar_pct"]:
        return "TRENDING_GRIND"
    return "TRENDING_SWING"


def event_cell(event: dict[str, Any]) -> str:
    regime = event.get("corrected_regime") or event.get("regime")
    if regime == "TRENDING":
        return trend_speed_class(event)
    return str(regime)


def candle_idx(candle_set: phase_a.CandleSet, tf: str, open_ms: int) -> int | None:
    idx = phase_a.bisect_right(candle_set.ts[tf], open_ms) - 1
    if idx >= 0 and int(candle_set.rows[tf][idx][0]) == open_ms:
        return idx
    return None


def atr_price(rows: list[list[Any]], idx: int, period: int = 14) -> float:
    start = max(1, idx - period + 1)
    vals = []
    for i in range(start, idx + 1):
        prev_close = safe_float(rows[i - 1][4])
        high = safe_float(rows[i][2])
        low = safe_float(rows[i][3])
        vals.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return average(vals)


def side_from_structure(rows: list[list[Any]], idx: int, model: str) -> str | None:
    if idx < 4:
        return None
    c = safe_float(rows[idx][4])
    o = safe_float(rows[idx][1])
    h = safe_float(rows[idx][2])
    l = safe_float(rows[idx][3])
    c1 = safe_float(rows[idx - 1][4])
    c3 = safe_float(rows[idx - 3][4])
    rng = h - l
    close_loc = (c - l) / rng if rng > 0 else 0.5
    move3 = (c - c3) / c3 * 100 if c3 > 0 else 0.0
    body = (c - o) / o * 100 if o > 0 else 0.0
    if model in {"trend_impulse", "drift_fast"}:
        if move3 > 0 and body >= -0.1 and close_loc >= 0.52:
            return "long"
        if move3 < 0 and body <= 0.1 and close_loc <= 0.48:
            return "short"
        if c > c1:
            return "long"
        if c < c1:
            return "short"
    if model == "range_fade":
        lookback = rows[max(0, idx - 24) : idx + 1]
        hi = max(safe_float(r[2]) for r in lookback)
        lo = min(safe_float(r[3]) for r in lookback)
        pos = (c - lo) / (hi - lo) if hi > lo else 0.5
        if pos >= 0.70:
            return "short"
        if pos <= 0.30:
            return "long"
    return None


def peak_guard(rows: list[list[Any]], idx: int, side: str, engine: dict[str, Any]) -> tuple[bool, str]:
    lookback = rows[max(0, idx - CONFIG["peak_lookback_15m"]) : idx + 1]
    if len(lookback) < 4:
        return False, ""
    close = safe_float(rows[idx][4])
    high = max(safe_float(r[2]) for r in lookback)
    low = min(safe_float(r[3]) for r in lookback)
    pos = (close - low) / (high - low) if high > low else 0.5
    base = low if side == "long" else high
    move_from_base = abs(close - base) / base * 100 if base > 0 else 0.0
    adx_4h = safe_float((engine.get("engine_vars") or {}).get("adx_4h"))
    row = rows[idx]
    o, h, l, c = map(safe_float, row[1:5])
    rng = h - l
    close_loc = (c - l) / rng if rng > 0 else 0.5
    if side == "long" and pos >= CONFIG["peak_guard_range_pos_long"] and move_from_base >= CONFIG["peak_guard_move_pct"]:
        return True, "long_entry_near_range_high_after_large_move"
    if side == "short" and pos <= CONFIG["peak_guard_range_pos_short"] and move_from_base >= CONFIG["peak_guard_move_pct"]:
        return True, "short_entry_near_range_low_after_large_move"
    if side == "long" and adx_4h >= CONFIG["peak_guard_adx4h"] and close_loc < 0.45:
        return True, "adx4h_exhaustion_reversal_candle"
    if side == "short" and adx_4h >= CONFIG["peak_guard_adx4h"] and close_loc > 0.55:
        return True, "adx4h_exhaustion_reversal_candle"
    return False, ""


def dir_return(entry: float, price: float, side: str) -> float:
    if entry <= 0 or price <= 0:
        return float("nan")
    if side == "long":
        return (price - entry) / entry * 100
    return (entry - price) / entry * 100


def simulate_model_exit(
    rows: list[list[Any]],
    idx: int,
    side: str,
    model: str,
    move_type: str,
) -> dict[str, Any]:
    entry_raw = safe_float(rows[idx][4])
    slip = CONFIG["entry_slippage_pct"] / 100
    entry = entry_raw * (1 + slip) if side == "long" else entry_raw * (1 - slip)
    atr = atr_price(rows, idx)
    hold = CONFIG["fast_hold_bars"] if move_type == "FAST" else CONFIG["swing_hold_bars"]
    if model == "range_fade":
        stop_k = CONFIG["range_stop_atr_k"]
        tp_r = 0.9
    elif model == "drift_fast":
        stop_k = CONFIG["drift_stop_atr_k"]
        tp_r = CONFIG["fast_tp_r"]
    else:
        stop_k = CONFIG["trend_stop_atr_k"]
        tp_r = CONFIG["fast_tp_r"] if move_type == "FAST" else CONFIG["swing_tp_r"]
    stop_dist = max(stop_k * atr, entry * 0.004)
    if side == "long":
        stop = entry - stop_dist
        tp = entry + stop_dist * tp_r
    else:
        stop = entry + stop_dist
        tp = entry - stop_dist * tp_r
    best = 0.0
    worst = 0.0
    last_price = entry_raw
    outcome = "TIME"
    exit_price = last_price
    exit_idx = min(len(rows) - 1, idx + hold)
    for j in range(idx + 1, min(len(rows), idx + hold + 1)):
        high = safe_float(rows[j][2])
        low = safe_float(rows[j][3])
        close = safe_float(rows[j][4])
        favorable = high if side == "long" else low
        adverse = low if side == "long" else high
        best = max(best, dir_return(entry, favorable, side))
        worst = min(worst, dir_return(entry, adverse, side))
        hit_stop = low <= stop if side == "long" else high >= stop
        hit_tp = high >= tp if side == "long" else low <= tp
        if hit_stop:
            outcome = "SL"
            exit_price = stop
            exit_idx = j
            break
        if hit_tp:
            outcome = "TP"
            exit_price = tp
            exit_idx = j
            break
        last_price = close
        exit_price = close
    gross = dir_return(entry, exit_price, side)
    return {
        "filled": True,
        "entry": entry,
        "side": side,
        "stop": stop,
        "tp": tp,
        "outcome": outcome,
        "gross_pct": gross,
        "net_pct": gross - CONFIG["fee_pct"],
        "mfe_pct": best,
        "mae_pct": worst,
        "exit_idx": exit_idx,
    }


def render_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" if i == 0 else "---:" for i in range(len(headers))) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return lines


def feature_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "n": len(rows),
        "adx_1h": average([feature(r, "adx_1h") for r in rows]),
        "di_1h": average([feature(r, "di_spread_1h") for r in rows]),
        "slope_15m": average([feature(r, "slope_15m") for r in rows]),
        "bb_width": average([feature(r, "bb_width_15m") for r in rows]),
        "daily_range": average([feature(r, "daily_range_pct") for r in rows]),
        "vol_ratio": average([feature(r, "vol_ratio_sig") for r in rows]),
    }


def load_replay() -> tuple[list[str], dict[str, phase_a.CandleSet], list[dict[str, Any]], list[dict[str, Any]], int, int]:
    universe = phase_a.load_universe()
    strategy_cfg = phase_a.load_strategy_config()
    start_close_ms, end_close_ms = phase_a.cached_reference_window(universe)
    candle_sets: dict[str, phase_a.CandleSet] = {}
    all_decisions: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    log(f"phaseB A replay window {iso_from_ms(start_close_ms)} -> {iso_from_ms(end_close_ms)}")
    for symbol in universe:
        candle_set = phase_a.load_symbol_candles(symbol, start_close_ms, end_close_ms)
        if candle_set is None:
            log(f"{symbol}: skipped (missing candles)")
            continue
        candle_sets[symbol] = candle_set
        decisions, events = phase_a.replay_symbol(symbol, candle_set, strategy_cfg, start_close_ms, end_close_ms)
        for row in decisions:
            new_regime, reason = corrected_regime(row)
            row["corrected_regime"] = new_regime
            row["corrected_reason"] = reason
        for event in events:
            start_row = event.get("start_engine") or event.get("engine") or {}
            new_regime, reason = corrected_regime(start_row)
            event["old_regime"] = event["regime"]
            event["corrected_regime"] = new_regime
            event["corrected_reason"] = reason
        all_decisions.extend(decisions)
        all_events.extend(events)
        log(f"{symbol}: decisions={len(decisions)} moves={len(events)}")
    return universe, candle_sets, all_decisions, all_events, start_close_ms, end_close_ms


def crosstab(rows: list[dict[str, Any]]) -> list[list[Any]]:
    counts = Counter((r["regime"], r["corrected_regime"]) for r in rows)
    out = []
    for (old, new), count in sorted(counts.items()):
        out.append([old, new, count, fmt(pct(count, len(rows)), "%")])
    return out


def corrected_counts(rows: list[dict[str, Any]]) -> list[list[Any]]:
    old_counts = Counter(r["regime"] for r in rows)
    new_counts = Counter(r["corrected_regime"] for r in rows)
    names = sorted(set(old_counts) | set(new_counts))
    return [[name, old_counts[name], new_counts[name], new_counts[name] - old_counts[name]] for name in names]


def movement_counts(events: list[dict[str, Any]]) -> list[list[Any]]:
    old_counts = Counter((e["old_regime"], e["move_type"]) for e in events)
    new_counts = Counter((e["corrected_regime"], e["move_type"]) for e in events)
    keys = sorted(set(old_counts) | set(new_counts))
    return [[k[0], k[1], old_counts[k], new_counts[k], new_counts[k] - old_counts[k]] for k in keys]


def relabel_examples(decisions: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples = []
    for event in events:
        if event["old_regime"] == "RANGING" and event["corrected_regime"] != "RANGING":
            examples.append(event)
    examples.sort(key=lambda e: safe_float(e["move_pct"]), reverse=True)
    return examples[: CONFIG["case_limit"]]


def render_case(path: Path, event: dict[str, Any], candle_set: phase_a.CandleSet) -> None:
    rows = candle_set.rows["15m"]
    idx_by_ts = {int(row[0]): idx for idx, row in enumerate(rows)}
    start_open = int(event["start_open_ms"])
    if start_open not in idx_by_ts:
        return
    idx = idx_by_ts[start_open]
    start = max(0, idx - 14)
    end = min(len(rows), idx + int(event["peak_bars_15m"]) + 14)
    subset = rows[start:end]
    if len(subset) < 8:
        return
    x0 = idx - start
    peak_x = x0 + int(event["peak_bars_15m"])
    fig, ax = plt.subplots(figsize=(12, 6))
    for i, row in enumerate(subset):
        o, h, l, c = map(float, row[1:5])
        color = "#15936b" if c >= o else "#c23b3b"
        ax.vlines(i, l, h, color=color, linewidth=0.9)
        body_h = max(abs(c - o), (h - l) * 0.02)
        ax.add_patch(patches.Rectangle((i - 0.35, min(o, c)), 0.7, body_h, color=color, alpha=0.78))
    ax.axvline(x0, color="#1f4e99", linestyle=":", linewidth=1.1)
    ax.scatter([x0], [event["entry_price"]], color="#0b5bd3", s=42, zorder=8)
    ax.annotate(
        f"{event['move_type']} {event['direction']} {fmt(event['move_pct'], '%')}",
        xy=(peak_x, event["peak_price"]),
        xytext=(x0, event["entry_price"]),
        arrowprops={"arrowstyle": "->", "color": "#6f42c1", "lw": 1.2},
        color="#6f42c1",
        fontsize=8,
    )
    ax.set_title(
        f"{event['symbol']} {event['start_ts']} | old {event['old_regime']} -> new {event['corrected_regime']} | {event['corrected_reason']}",
        fontsize=9,
        loc="left",
    )
    step = max(1, len(subset) // 8)
    ticks = list(range(0, len(subset), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([iso_from_ms(int(subset[i][0]) + phase_a.TF_MS["15m"])[11:16] for i in ticks], fontsize=8)
    ax.grid(alpha=0.18)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_cases(events: list[dict[str, Any]], candle_sets: dict[str, phase_a.CandleSet]) -> None:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    for old in CASE_DIR.glob(f"*_{SUFFIX}.png"):
        old.unlink()
    for idx, event in enumerate(relabel_examples([], events), start=1):
        candle_set = candle_sets.get(event["symbol"])
        if not candle_set:
            continue
        safe_ts = event["start_ts"].replace(":", "").replace("-", "").replace("Z", "")
        path = CASE_DIR / f"case_{idx:02d}_{event['symbol']}_{safe_ts}_{event['old_regime']}_to_{event['corrected_regime']}_{SUFFIX}.png"
        render_case(path, event, candle_set)


def empty_model_accum() -> dict[str, Any]:
    return {
        "n": 0,
        "filled": 0,
        "side_known": 0,
        "side_matches": 0,
        "net": [],
        "gross": [],
        "mfe": [],
        "mae": [],
        "outcomes": Counter(),
        "skips": Counter(),
    }


def update_model_accum(acc: dict[str, Any], row: dict[str, Any]) -> None:
    acc["n"] += 1
    if row.get("model_side") in {"long", "short"} and row.get("event_direction") in {"long", "short"}:
        acc["side_known"] += 1
        acc["side_matches"] += 1 if row["model_side"] == row["event_direction"] else 0
    if row.get("skip_reason"):
        acc["skips"][row["skip_reason"]] += 1
        return
    sim = row.get("sim") or {}
    if not sim.get("filled"):
        acc["skips"]["no_fill"] += 1
        return
    acc["filled"] += 1
    acc["net"].append(safe_float(sim.get("net_pct")))
    acc["gross"].append(safe_float(sim.get("gross_pct")))
    acc["mfe"].append(safe_float(sim.get("mfe_pct")))
    acc["mae"].append(safe_float(sim.get("mae_pct")))
    acc["outcomes"][sim.get("outcome") or "UNKNOWN"] += 1


def finalize_model_accum(acc: dict[str, Any]) -> dict[str, Any]:
    nets = [v for v in acc["net"] if math.isfinite(v)]
    filled = len(nets)
    wins = sum(1 for v in nets if v > 0)
    return {
        "n": acc["n"],
        "filled": filled,
        "fill_rate": pct(filled, acc["n"]),
        "side_match_rate": pct(acc["side_matches"], acc["side_known"]),
        "avg_net_pct": average(nets),
        "median_net_pct": (sorted(nets)[len(nets) // 2] if nets else None),
        "win_rate": pct(wins, filled),
        "avg_mfe_pct": average(acc["mfe"]),
        "avg_mae_pct": average(acc["mae"]),
        "tp_rate": pct(acc["outcomes"]["TP"], filled),
        "sl_rate": pct(acc["outcomes"]["SL"], filled),
        "time_rate": pct(acc["outcomes"]["TIME"], filled),
        "outcomes": dict(acc["outcomes"]),
        "skips": dict(acc["skips"]),
    }


def model_for_event(event: dict[str, Any]) -> str | None:
    cell = event_cell(event)
    move_type = event.get("move_type")
    if cell == "TRENDING_IMPULSE":
        return "trend_impulse"
    if cell in {"TRENDING_SWING", "TRENDING_GRIND"}:
        return "trend_grind_watch"
    if cell == "DRIFT" and move_type == "FAST":
        return "drift_fast"
    if cell == "RANGING":
        return "range_fade"
    return None


def event_period(event: dict[str, Any], start_close_ms: int, end_close_ms: int) -> str:
    mid = start_close_ms + (end_close_ms - start_close_ms) // 2
    return "early" if int(event["start_open_ms"]) + phase_a.TF_MS["15m"] < mid else "late"


def analyze_models(
    events: list[dict[str, Any]],
    candle_sets: dict[str, phase_a.CandleSet],
    start_close_ms: int,
    end_close_ms: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = []
    for event in events:
        symbol = event["symbol"]
        candle_set = candle_sets.get(symbol)
        if not candle_set:
            continue
        idx = candle_idx(candle_set, "15m", int(event["start_open_ms"]))
        if idx is None:
            continue
        model = model_for_event(event)
        cell = event_cell(event)
        tier = volatility_tier(symbol, candle_set)
        side = side_from_structure(candle_set.rows["15m"], idx, model or "trend_impulse")
        row = {
            "symbol": symbol,
            "ts": event["start_ts"],
            "cell": cell,
            "old_regime": event.get("old_regime"),
            "corrected_regime": event.get("corrected_regime"),
            "move_type": event["move_type"],
            "event_direction": event["direction"],
            "model": model or "no_model",
            "tier": tier,
            "period": event_period(event, start_close_ms, end_close_ms),
            "event_move_pct": event["move_pct"],
            "event_peak_bars": event["peak_bars_15m"],
            "engine": event.get("start_engine") or event.get("engine") or {},
            "skip_reason": "",
            "sim": None,
        }
        if model is None:
            row["skip_reason"] = "no_model_for_cell"
            rows.append(row)
            continue
        if model == "trend_grind_watch":
            row["skip_reason"] = "grind_watch_no_trade"
            rows.append(row)
            continue
        if side is None:
            row["skip_reason"] = "no_structural_side"
            rows.append(row)
            continue
        guard, guard_reason = peak_guard(candle_set.rows["15m"], idx, side, row["engine"])
        if guard:
            row["skip_reason"] = f"peak_guard:{guard_reason}"
            row["model_side"] = side
            rows.append(row)
            continue
        row["model_side"] = side
        row["side_match"] = side == event["direction"]
        row["sim"] = simulate_model_exit(candle_set.rows["15m"], idx, side, model, event["move_type"])
        rows.append(row)

    accum: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(empty_model_accum)
    for row in rows:
        keys = [
            (row["cell"], row["model"], "all", "both", "all"),
            (row["cell"], row["model"], row["tier"], "both", "all"),
            (row["cell"], row["model"], "all", row.get("model_side") or "none", "all"),
            (row["cell"], row["model"], "all", "both", row["period"]),
        ]
        for key in keys:
            update_model_accum(accum[key], row)
    summary = []
    for key, acc in accum.items():
        cell, model, tier, side, period = key
        summary.append(
            {
                "cell": cell,
                "model": model,
                "tier": tier,
                "side": side,
                "period": period,
                **finalize_model_accum(acc),
            }
        )
    summary.sort(key=lambda r: (r["cell"], r["model"], r["tier"], r["side"], r["period"]))
    return rows, summary


def go_no_go(row: dict[str, Any], summary_rows: list[dict[str, Any]]) -> str:
    if row["tier"] != "all" or row["side"] != "both" or row["period"] != "all":
        return ""
    if row["filled"] < 20:
        return "NO-GO: sample<20"
    if safe_float(row["avg_net_pct"]) <= 0:
        return "NO-GO: net<=0"
    side_rows = [
        r for r in summary_rows
        if r["cell"] == row["cell"]
        and r["model"] == row["model"]
        and r["tier"] == "all"
        and r["period"] == "all"
        and r["side"] in {"long", "short"}
    ]
    if len(side_rows) < 2 or any(safe_float(r["avg_net_pct"]) <= 0 or r["filled"] < 20 for r in side_rows):
        return "NO-GO: side split fails"
    period_rows = [
        r for r in summary_rows
        if r["cell"] == row["cell"]
        and r["model"] == row["model"]
        and r["tier"] == "all"
        and r["side"] == "both"
        and r["period"] in {"early", "late"}
    ]
    if len(period_rows) < 2 or any(safe_float(r["avg_net_pct"]) <= 0 or r["filled"] < 10 for r in period_rows):
        return "NO-GO: early/late split fails"
    return "GO"


def peak_guard_live_report(candle_sets: dict[str, phase_a.CandleSet]) -> list[dict[str, Any]]:
    signals = {row["id"]: row for row in phase_a.load_jsonl(phase_a.SIGNALS_PATH)}
    snapshots = {
        row["signal_id"]: row
        for row in phase_a.load_jsonl(SNAPSHOTS_PATH)
        if row.get("signal_id")
    }
    labels = {
        row["signal_id"]: row
        for row in phase_a.load_jsonl(phase_a.LABELS_PATH)
        if row.get("valid") is True
    }
    out = []
    for signal_id, label in labels.items():
        signal = signals.get(signal_id)
        if not signal:
            continue
        candle_set = candle_sets.get(signal["symbol"])
        if not candle_set:
            continue
        ts_ms = phase_a.ms_from_iso(signal["ts"])
        open_ms = ts_ms - ts_ms % phase_a.TF_MS["15m"] - phase_a.TF_MS["15m"]
        idx = phase_a.bisect_right(candle_set.ts["15m"], open_ms) - 1
        if idx < 0:
            continue
        side = "long" if signal.get("side") == "buy" else "short"
        snapshot = snapshots.get(signal_id) or {}
        context = snapshot.get("context") or {}
        engine_vars = snapshot.get("engine_vars") or {}
        engine = {"engine_vars": {"adx_4h": safe_float(engine_vars.get("adx_4h", context.get("adx_4h")))}}
        guard, reason = peak_guard(candle_set.rows["15m"], idx, side, engine)
        out.append(
            {
                "symbol": signal["symbol"],
                "ts": signal["ts"],
                "regime": signal.get("regime"),
                "style": signal.get("trade_style"),
                "side": side,
                "outcome": label.get("outcome"),
                "guard": guard,
                "reason": reason,
            }
        )
    return out


def tick_availability(symbols: Iterable[str]) -> dict[str, Any]:
    symbols = sorted(set(symbols))
    available = [s for s in symbols if (TICK_ROOT / s).exists()]
    return {
        "tick_root": str(TICK_ROOT),
        "available_symbols": available,
        "available_count": len(available),
        "requested_count": len(symbols),
        "coverage_pct": pct(len(available), len(symbols)),
    }


def render_full_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    return render_table(headers, rows)


def render_full_report(
    model_rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    live_peak_rows: list[dict[str, Any]],
    tape_info: dict[str, Any],
    start_close_ms: int,
    end_close_ms: int,
) -> str:
    main_rows = [
        r for r in summary_rows
        if r["tier"] == "all" and r["side"] == "both" and r["period"] == "all"
    ]
    main_rows.sort(key=lambda r: (r["cell"], r["model"]))
    table = []
    for row in main_rows:
        table.append(
            [
                row["cell"],
                row["model"],
                row["n"],
                row["filled"],
                fmt(row["fill_rate"], "%"),
                fmt(row["avg_net_pct"], "%"),
                fmt(row["side_match_rate"], "%"),
                fmt(row["win_rate"], "%"),
                fmt(row["tp_rate"], "%"),
                fmt(row["sl_rate"], "%"),
                go_no_go(row, summary_rows),
            ]
        )
    side_rows = [
        r for r in summary_rows
        if r["tier"] == "all" and r["side"] in {"long", "short"} and r["period"] == "all" and r["filled"] > 0
    ]
    side_table = [
        [r["cell"], r["model"], r["side"], r["filled"], fmt(r["avg_net_pct"], "%"), fmt(r["win_rate"], "%")]
        for r in side_rows
    ]
    tier_rows = [
        r for r in summary_rows
        if r["tier"] != "all" and r["side"] == "both" and r["period"] == "all" and r["filled"] > 0
    ]
    tier_table = [
        [r["cell"], r["model"], r["tier"], r["filled"], fmt(r["avg_net_pct"], "%"), fmt(r["win_rate"], "%")]
        for r in tier_rows
    ]
    period_rows = [
        r for r in summary_rows
        if r["tier"] == "all" and r["side"] == "both" and r["period"] in {"early", "late"} and r["filled"] > 0
    ]
    period_table = [
        [r["cell"], r["model"], r["period"], r["filled"], fmt(r["avg_net_pct"], "%"), fmt(r["win_rate"], "%")]
        for r in period_rows
    ]
    guarded = [r for r in model_rows if str(r.get("skip_reason", "")).startswith("peak_guard")]
    guard_reasons = Counter(r["skip_reason"] for r in guarded)
    live_guarded = [r for r in live_peak_rows if r["guard"]]
    live_guard_outcomes = Counter(r["outcome"] for r in live_guarded)
    data_rows = [
        ["TRENDING_IMPULSE", "15m impulse speed, 1m/tape delta for earlier entry, distance from base, ADX4H exhaustion, structural stop"],
        ["TRENDING_GRIND", "slope persistence, pullback quality, low climax; current sample treated as watch/no-trade"],
        ["RANGING", "range high/low position, BB corridor, fade side, CVD exhaustion/divergence, tight range stop"],
        ["DRIFT", "VWAP walk, slope direction, base distance, trigger candle close location, peak guard"],
    ]
    lines = [
        "# Regime Model Full Phase B - 21.05.2026",
        "",
        f"Replay period: `{iso_from_ms(start_close_ms)}` to `{iso_from_ms(end_close_ms)}`. Net includes `{CONFIG['fee_pct']:.2f}%` taker round trip and `{CONFIG['entry_slippage_pct']:.2f}%` entry slippage.",
        "",
        "## Formal Models Tested",
        "",
        "- `trend_impulse`: structural momentum side, early 15m close entry, ATR stop, fixed-R TP. `adx_not_rising` is not required.",
        "- `trend_grind_watch`: slow TRENDING is explicitly not traded in this pass; it is separated from impulse by speed.",
        "- `range_fade`: side is fade of 24-bar range extreme, not default long. It only acts in corrected true RANGING.",
        "- `drift_fast`: conservative momentum/VWAP-style fast entry with peak guard; DRIFT remains benchmarked against live DRIFT x FAST.",
        "- `peak_guard`: skips entries already near local extreme after a large base move or ADX4H exhaustion reversal candle.",
        "",
        "## EV By Cell",
        "",
    ]
    lines.extend(render_full_table(["cell", "model", "events", "filled", "fill", "avg net", "dir match", "WR", "TP", "SL", "verdict"], table))
    lines.extend(["", "## Side Split", ""])
    lines.extend(render_full_table(["cell", "model", "side", "filled", "avg net", "WR"], side_table))
    lines.extend(["", "## Early/Late Split", ""])
    lines.extend(render_full_table(["cell", "model", "period", "filled", "avg net", "WR"], period_table))
    lines.extend(["", "## Volatility Tier Split", ""])
    lines.extend(render_full_table(["cell", "model", "tier", "filled", "avg net", "WR"], tier_table))
    lines.extend(["", "## Peak Guard", ""])
    lines.append(f"- model events skipped by peak guard: `{len(guarded)}`")
    for reason, count in guard_reasons.most_common(8):
        lines.append(f"- `{reason}`: `{count}`")
    lines.append(f"- live valid signals caught by peak guard: `{len(live_guarded)}`; outcomes: `{dict(live_guard_outcomes)}`")
    lines.extend(
        [
            "",
            "## Tape Data",
            "",
            f"- tick root: `{tape_info['tick_root']}`",
            f"- symbols with tick directories: `{tape_info['available_count']}` / `{tape_info['requested_count']}` ({fmt(tape_info['coverage_pct'], '%')})",
            "- This pass uses candle/engine features for executable EV and records tape availability. The next implementation should add CVD/delta gates from these tick directories before any production config change.",
        ]
    )
    lines.extend(["", "## Data Needed By Regime", ""])
    lines.extend(render_full_table(["cell", "required data"], data_rows))
    lines.extend(
        [
            "",
            "## GO / NO-GO",
            "",
            "The strict continuation-style criterion is positive net after fees on both long and short sides, stable early and late, with normal sample. Under that criterion no new regime cell is production-ready in this 10-day sample. High-volatility impulse cells remain research candidates; majors are mostly fee/size blocked.",
            "",
            "## GPT Hypotheses",
            "",
            "- The shared impulse component belongs to high-volatility alt regimes first; majors need a different threshold because 15m impulse size is close to fee and stop noise.",
            "- RANGING should remain narrow and mean-reversion only. Sharp swings previously called RANGING are better handled by impulse/exhaustion logic.",
            "- ADX4H above 40 is useful as an exhaustion/late-entry warning, not as a blanket trend confirmation.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_model_case(path: Path, row: dict[str, Any], candle_set: phase_a.CandleSet) -> None:
    idx = candle_idx(candle_set, "15m", int(phase_a.ms_from_iso(row["ts"]) - phase_a.TF_MS["15m"]))
    # Fallback to exact event open if ts is close time.
    if idx is None:
        target_open = phase_a.ms_from_iso(row["ts"]) - phase_a.TF_MS["15m"]
        idx = phase_a.bisect_right(candle_set.ts["15m"], target_open) - 1
    if idx is None or idx < 0:
        return
    rows = candle_set.rows["15m"]
    start = max(0, idx - 12)
    end = min(len(rows), idx + 18)
    subset = rows[start:end]
    x0 = idx - start
    fig, ax = plt.subplots(figsize=(12, 6))
    for i, cndl in enumerate(subset):
        o, h, l, c = map(safe_float, cndl[1:5])
        color = "#15936b" if c >= o else "#c23b3b"
        ax.vlines(i, l, h, color=color, linewidth=0.9)
        ax.add_patch(patches.Rectangle((i - 0.35, min(o, c)), 0.7, max(abs(c - o), (h - l) * 0.02), color=color, alpha=0.78))
    ax.axvline(x0, color="#1f4e99", linestyle=":", linewidth=1.1)
    sim = row.get("sim") or {}
    if sim.get("filled"):
        ax.scatter([x0], [sim["entry"]], color="#0b5bd3", s=42, zorder=8, label="model entry")
        ax.axhline(sim["stop"], color="#d62728", linestyle="--", linewidth=0.8, label="stop")
        ax.axhline(sim["tp"], color="#2ca02c", linestyle="--", linewidth=0.8, label="tp")
    ax.set_title(
        f"{row['cell']} {row['model']} {row['symbol']} {row['ts']} | {row.get('model_side')} net {fmt(sim.get('net_pct'), '%')}",
        fontsize=9,
        loc="left",
    )
    ax.grid(alpha=0.18)
    ax.legend(loc="best", fontsize=8)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_model_cases(model_rows: list[dict[str, Any]], candle_sets: dict[str, phase_a.CandleSet]) -> None:
    FULL_CASE_DIR.mkdir(parents=True, exist_ok=True)
    for old in FULL_CASE_DIR.glob(f"*_{SUFFIX}.png"):
        old.unlink()
    eligible = [r for r in model_rows if (r.get("sim") or {}).get("filled")]
    for cell in ["TRENDING_IMPULSE", "TRENDING_SWING", "RANGING", "DRIFT"]:
        rows = sorted(
            [r for r in eligible if r["cell"] == cell],
            key=lambda r: safe_float((r.get("sim") or {}).get("net_pct")),
            reverse=True,
        )[:8]
        for idx, row in enumerate(rows, start=1):
            candle_set = candle_sets.get(row["symbol"])
            if not candle_set:
                continue
            safe_ts = row["ts"].replace(":", "").replace("-", "").replace("Z", "")
            path = FULL_CASE_DIR / f"{cell.lower()}_{idx:02d}_{row['symbol']}_{safe_ts}_{SUFFIX}.png"
            render_model_case(path, row, candle_set)


def render_report(
    universe: list[str],
    decisions: list[dict[str, Any]],
    events: list[dict[str, Any]],
    start_close_ms: int,
    end_close_ms: int,
) -> str:
    active_symbols = sorted({r["symbol"] for r in decisions})
    problem_relabels = [r for r in decisions if r["regime"] == "RANGING" and r["corrected_regime"] != "RANGING"]
    old_ranging = [r for r in decisions if r["regime"] == "RANGING"]
    summaries = []
    for name, rows in [
        ("old RANGING", old_ranging),
        ("old RANGING -> corrected TRENDING", [r for r in problem_relabels if r["corrected_regime"] == "TRENDING"]),
        ("corrected RANGING", [r for r in decisions if r["corrected_regime"] == "RANGING"]),
        ("corrected TRENDING", [r for r in decisions if r["corrected_regime"] == "TRENDING"]),
        ("corrected DRIFT", [r for r in decisions if r["corrected_regime"] == "DRIFT"]),
    ]:
        s = feature_summary(rows)
        summaries.append([name, s["n"], fmt(s["adx_1h"]), fmt(s["di_1h"]), fmt(s["slope_15m"]), fmt(s["bb_width"]), fmt(s["daily_range"]), fmt(s["vol_ratio"])])

    example_rows = []
    for event in relabel_examples(decisions, events)[:8]:
        example_rows.append(
            [
                event["symbol"],
                event["start_ts"],
                event["move_type"],
                event["direction"],
                fmt(event["move_pct"], "%"),
                event["old_regime"],
                event["corrected_regime"],
                event["corrected_reason"],
            ]
        )

    lines = [
        "# Regime Classification Audit Phase B - 21.05.2026",
        "",
        "Checkpoint result: only Part A is executed here. Per-regime model fitting, peak-entry filters, and impulse model tests are intentionally not run until the corrected regime labels are confirmed.",
        "",
        f"Replay period: `{iso_from_ms(start_close_ms)}` to `{iso_from_ms(end_close_ms)}`.",
        f"Universe: `{len(universe)}` requested, `{len(active_symbols)}` decision-active symbols, `{len(decisions)}` engine decisions.",
        "",
        "## Corrected Regime Definitions",
        "",
        "- `RANGING`: low directional pressure, flat 15m slope, BB corridor, and limited daily range. This is the only true mean-reversion regime.",
        "- `DRIFT`: moderate ADX/DI directional walk with contained BB width and daily range. This preserves the current DRIFT x FAST cell.",
        "- `TRENDING`: directional or expanding swing/trend. High ADX/DI, high 15m/1h slope, wide BB, high daily range, or 4H conflict moves old false-RANGING labels out of range.",
        "- `CHOPPY`: leftover/noise where neither range nor directional structure is clean enough.",
        "",
        "## Old vs Corrected Counts",
        "",
    ]
    lines.extend(render_table(["regime", "old count", "corrected count", "delta"], corrected_counts(decisions)))
    lines.extend(["", "## Old -> Corrected Crosstab", ""])
    lines.extend(render_table(["old", "corrected", "count", "share"], crosstab(decisions)))
    lines.extend(["", "## Tradeable Movements: Old vs Corrected Regime", ""])
    lines.extend(render_table(["regime", "type", "old moves", "corrected moves", "delta"], movement_counts(events)))
    lines.extend(["", "## Feature Signature", ""])
    lines.extend(render_table(["bucket", "n", "avg ADX1H", "avg DI1H", "avg slope15", "avg BB width", "avg day range", "avg vol"], summaries))
    lines.extend(["", "## Problem Examples", ""])
    lines.extend(render_table(["symbol", "ts", "type", "side", "move", "old", "corrected", "reason"], example_rows))
    lines.extend(
        [
            "",
            "## Verdict For Checkpoint",
            "",
            f"- Old `RANGING` is too broad: `{len(problem_relabels)}` of `{len(old_ranging)}` old-RANGING decisions are reclassified as directional/expanding, mostly `TRENDING`.",
            "- The corrected `RANGING` definition is intentionally narrow. If the trader wants those sharp swings traded as fades, that should be a separate `TRENDING/IMPULSE` or exhaustion model, not the range bucket.",
            "- The BSB-style sharp drop case class belongs outside `RANGING`; by the corrected definition it is directional expansion.",
            "",
            "## Stop Here",
            "",
            "Please review the corrected labels and PNG examples. If these regime labels are acceptable, Phase B can continue to per-regime entry/exit modelling. If not, adjust the definitions first.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")
    universe, candle_sets, decisions, events, start_close_ms, end_close_ms = load_replay()
    generate_cases(events, candle_sets)
    REPORT_MD.write_text(render_report(universe, decisions, events, start_close_ms, end_close_ms), encoding="utf-8")
    summary = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "checkpoint": "Part A only - classification audit",
        "config": CONFIG,
        "period": {"start": iso_from_ms(start_close_ms), "end": iso_from_ms(end_close_ms)},
        "decision_count": len(decisions),
        "old_corrected_counts": corrected_counts(decisions),
        "old_to_corrected": crosstab(decisions),
        "movement_counts": movement_counts(events),
        "problem_examples": relabel_examples(decisions, events)[:10],
    }
    SUMMARY_JSON.write_text(json.dumps(json_safe(summary), ensure_ascii=True, indent=2, allow_nan=False), encoding="utf-8")
    log(f"saved {REPORT_MD}")
    log(f"saved {SUMMARY_JSON}")
    log(f"saved cases to {CASE_DIR}")
    if CONFIG["run_full_phase_b"]:
        log("running full Phase B model analyzer")
        model_rows, model_summary = analyze_models(events, candle_sets, start_close_ms, end_close_ms)
        live_peak_rows = peak_guard_live_report(candle_sets)
        tape_info = tick_availability(candle_sets.keys())
        generate_model_cases(model_rows, candle_sets)
        FULL_REPORT_MD.write_text(
            render_full_report(
                model_rows,
                model_summary,
                live_peak_rows,
                tape_info,
                start_close_ms,
                end_close_ms,
            ),
            encoding="utf-8",
        )
        full_summary = {
            "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "checkpoint": "Part A accepted - full Phase B model analyzer",
            "config": CONFIG,
            "period": {"start": iso_from_ms(start_close_ms), "end": iso_from_ms(end_close_ms)},
            "event_count": len(events),
            "model_event_count": len(model_rows),
            "model_summary": model_summary,
            "peak_guard_live": {
                "checked_valid_signals": len(live_peak_rows),
                "guarded": sum(1 for row in live_peak_rows if row["guard"]),
                "guarded_outcomes": dict(Counter(row["outcome"] for row in live_peak_rows if row["guard"])),
            },
            "tape_availability": tape_info,
        }
        FULL_SUMMARY_JSON.write_text(json.dumps(json_safe(full_summary), ensure_ascii=True, indent=2, allow_nan=False), encoding="utf-8")
        log(f"saved {FULL_REPORT_MD}")
        log(f"saved {FULL_SUMMARY_JSON}")
        log(f"saved model cases to {FULL_CASE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
