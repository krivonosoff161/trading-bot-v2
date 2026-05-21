from __future__ import annotations

import json
import math
import pickle
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import requests


ROOT = Path(__file__).resolve().parents[3]
SIGNALS_PATH = ROOT / "logs" / "signals" / "main_signals.jsonl"
LABELS_PATH = ROOT / "logs" / "signals" / "main_signals_labels.jsonl"
SNAPSHOTS_PATH = ROOT / "logs" / "signals" / "signal_snapshot.jsonl"
CACHE_DIR = ROOT / "scripts" / "backtest" / "cache" / "screener"
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
CASE_DIR = OUT_DIR / "main_entry_timing_cases_21_05_2026"
REPORT_MD = OUT_DIR / "main_entry_timing_report_21_05_2026.md"
SUMMARY_JSON = OUT_DIR / "main_entry_timing_summary_21_05_2026.json"
RUN_LOG = OUT_DIR / "main_entry_timing_run_21_05_2026.log"

CONFIG = {
    "fee_pct": 0.20,
    "bar_ms": 5 * 60 * 1000,
    "lookback_bars": 18,
    "forward_extra_bars": 12,
    "case_limit_per_regime": 8,
    "pullback_search_bars_after_trigger": 8,
    "breakout_search_bars_after_signal": 8,
    "breakout_atr_buffer_k": 0.05,
    "okx_sleep_sec": 0.11,
    "okx_timeout_sec": 12,
}


@dataclass(slots=True)
class Candle:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def log(message: str) -> None:
    print(message)
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if math.isfinite(out) else float("nan")


def average(values: Iterable[float]) -> float:
    vals = [v for v in values if math.isfinite(v)]
    return sum(vals) / len(vals) if vals else float("nan")


def percentile(values: Iterable[float], q: float) -> float:
    vals = sorted(v for v in values if math.isfinite(v))
    if not vals:
        return float("nan")
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q / 100
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def pct(part: int | float, total: int | float) -> float:
    return part / total * 100 if total else float("nan")


def fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(val):
        return "n/a"
    return f"{val:.2f}{suffix}"


def iso_to_ms(value: str) -> int:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def iso_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def direction(side: str) -> str:
    return "long" if side == "buy" else "short"


def directional_return(entry: float, price: float, side: str) -> float:
    if entry <= 0 or price <= 0:
        return float("nan")
    if side == "buy":
        return (price - entry) / entry * 100
    return (entry - price) / entry * 100


def directional_move(start: float, end: float, side: str) -> float:
    if side == "buy":
        return end - start
    return start - end


def candle_from_raw(raw: list[Any]) -> Candle:
    return Candle(
        ts_ms=int(float(raw[0])),
        open=float(raw[1]),
        high=float(raw[2]),
        low=float(raw[3]),
        close=float(raw[4]),
        volume=float(raw[5]) if len(raw) > 5 else 0.0,
    )


def normalize_candles(raw: list[list[Any]]) -> list[Candle]:
    candles = [candle_from_raw(row) for row in raw]
    candles.sort(key=lambda c: c.ts_ms)
    out = []
    seen = set()
    for candle in candles:
        if candle.ts_ms in seen:
            continue
        seen.add(candle.ts_ms)
        out.append(candle)
    return out


def load_cached_candles(symbol: str) -> list[Candle]:
    path = CACHE_DIR / f"{symbol}_5m_60d.pkl"
    if not path.exists():
        return []
    with path.open("rb") as fh:
        raw = pickle.load(fh)
    return normalize_candles(raw)


def fetch_okx_window(symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
    params = {
        "instId": symbol,
        "bar": "5m",
        "limit": "300",
        "after": str(end_ms + CONFIG["bar_ms"]),
        "before": str(max(0, start_ms - CONFIG["bar_ms"])),
    }
    response = requests.get(
        "https://www.okx.com/api/v5/market/history-candles",
        params=params,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=CONFIG["okx_timeout_sec"],
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX code={payload.get('code')} msg={payload.get('msg')}")
    time.sleep(CONFIG["okx_sleep_sec"])
    return normalize_candles(payload.get("data", []))


def slice_window(candles: list[Candle], start_ms: int, end_ms: int) -> list[Candle]:
    return [c for c in candles if start_ms <= c.ts_ms <= end_ms]


def merge_candles(existing: list[Candle], extra: list[Candle]) -> list[Candle]:
    by_ts = {c.ts_ms: c for c in existing}
    for candle in extra:
        by_ts[candle.ts_ms] = candle
    return [by_ts[ts] for ts in sorted(by_ts)]


def completed_candle_index(candles: list[Candle], ts_ms: int) -> int | None:
    bar_ms = CONFIG["bar_ms"]
    idx = None
    for i, candle in enumerate(candles):
        if candle.ts_ms + bar_ms <= ts_ms:
            idx = i
        elif candle.ts_ms > ts_ms:
            break
    if idx is not None:
        return idx
    for i, candle in enumerate(candles):
        if candle.ts_ms <= ts_ms:
            idx = i
    return idx


def signal_window_bounds(signal: dict[str, Any]) -> tuple[int, int]:
    ts_ms = iso_to_ms(signal["ts"])
    start = ts_ms - (CONFIG["lookback_bars"] + 30) * CONFIG["bar_ms"]
    hold_min = int(signal.get("hold_min") or 0)
    end = ts_ms + hold_min * 60 * 1000 + CONFIG["forward_extra_bars"] * CONFIG["bar_ms"]
    return start, end


def has_signal_window(candles: list[Candle], signal: dict[str, Any]) -> bool:
    if not candles:
        return False
    ts_ms = iso_to_ms(signal["ts"])
    entry_idx = completed_candle_index(candles, ts_ms)
    if entry_idx is None:
        return False
    _, end = signal_window_bounds(signal)
    return candles[-1].ts_ms >= end - CONFIG["bar_ms"]


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(value * alpha + out[-1] * (1 - alpha))
    return out


def atr(candles: list[Candle], idx: int, period: int = 14) -> float:
    start = max(1, idx - period + 1)
    ranges = []
    for i in range(start, idx + 1):
        prev_close = candles[i - 1].close
        c = candles[i]
        ranges.append(max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close)))
    return average(ranges)


def find_trigger(candles: list[Candle], entry_idx: int, side: str) -> dict[str, Any]:
    start = max(0, entry_idx - CONFIG["lookback_bars"] + 1)
    candidates = []
    median_body = median(
        [
            abs(c.close - c.open) / c.open * 100
            for c in candles[start : entry_idx + 1]
            if c.open > 0
        ]
    )
    for idx in range(start, entry_idx + 1):
        c = candles[idx]
        move = directional_move(c.open, c.close, side)
        if move <= 0 or c.open <= 0:
            continue
        body_pct = abs(c.close - c.open) / c.open * 100
        range_pct = (c.high - c.low) / c.open * 100 if c.open > 0 else 0.0
        body_ratio = body_pct / median_body if median_body > 0 else 1.0
        score = body_pct * 1.5 + range_pct * 0.4 + body_ratio * 0.15
        candidates.append((score, idx, body_pct, range_pct, body_ratio))
    if not candidates:
        idx = entry_idx
        c = candles[idx]
        return {
            "idx": idx,
            "strong_idx": idx,
            "price": c.open,
            "body_pct": abs(c.close - c.open) / c.open * 100 if c.open > 0 else float("nan"),
            "range_pct": (c.high - c.low) / c.open * 100 if c.open > 0 else float("nan"),
            "body_ratio": float("nan"),
            "fallback": True,
        }
    _, strong_idx, body_pct, range_pct, body_ratio = max(candidates, key=lambda item: item[0])
    trigger_idx = strong_idx
    for idx in range(strong_idx - 1, max(start, strong_idx - 4) - 1, -1):
        prev = candles[idx]
        if directional_move(prev.open, prev.close, side) <= 0:
            break
        trigger_idx = idx
    return {
        "idx": trigger_idx,
        "strong_idx": strong_idx,
        "price": candles[trigger_idx].open,
        "body_pct": body_pct,
        "range_pct": range_pct,
        "body_ratio": body_ratio,
        "fallback": False,
    }


def mfe_mae_after_entry(candles: list[Candle], entry_idx: int, signal: dict[str, Any]) -> dict[str, Any]:
    side = signal["side"]
    entry = safe_float(signal["entry"])
    end_ms = iso_to_ms(signal["ts"]) + int(signal.get("hold_min") or 0) * 60 * 1000
    best = 0.0
    worst = 0.0
    best_ts = None
    last_close = entry
    for candle in candles[entry_idx + 1 :]:
        if candle.ts_ms > end_ms:
            break
        favorable_price = candle.high if side == "buy" else candle.low
        adverse_price = candle.low if side == "buy" else candle.high
        favorable = directional_return(entry, favorable_price, side)
        adverse = directional_return(entry, adverse_price, side)
        if favorable > best:
            best = favorable
            best_ts = candle.ts_ms
        worst = min(worst, adverse)
        last_close = candle.close
    return {
        "mfe_after_entry_pct": best,
        "mae_after_entry_pct": worst,
        "time_to_mfe_min": (best_ts - iso_to_ms(signal["ts"])) / 60000 if best_ts else None,
        "time_exit_return_pct": directional_return(entry, last_close, side),
    }


def simulate_levels(
    candles: list[Candle],
    start_ts_ms: int,
    entry_price: float,
    signal: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    side = signal["side"]
    sl = safe_float(signal["sl"])
    tp1 = safe_float(signal["tp1"])
    tp2 = safe_float(signal.get("tp2"))
    hold_min = int(signal.get("hold_min") or 0)
    end_ms = start_ts_ms + hold_min * 60 * 1000
    future = [c for c in candles if c.ts_ms >= start_ts_ms and c.ts_ms <= end_ms]
    if not future or not math.isfinite(entry_price):
        return {"mode": mode, "filled": False, "reason": "no_forward_candles"}
    exit_price = future[-1].close
    outcome = "TIME"
    for candle in future:
        if side == "buy":
            if math.isfinite(sl) and candle.low <= sl:
                exit_price = sl
                outcome = "SL"
                break
            if math.isfinite(tp2) and candle.high >= tp2:
                exit_price = tp2
                outcome = "TP2"
                break
            if math.isfinite(tp1) and candle.high >= tp1:
                exit_price = tp1
                outcome = "TP1"
                break
        else:
            if math.isfinite(sl) and candle.high >= sl:
                exit_price = sl
                outcome = "SL"
                break
            if math.isfinite(tp2) and candle.low <= tp2:
                exit_price = tp2
                outcome = "TP2"
                break
            if math.isfinite(tp1) and candle.low <= tp1:
                exit_price = tp1
                outcome = "TP1"
                break
    gross = directional_return(entry_price, exit_price, side)
    return {
        "mode": mode,
        "filled": True,
        "entry_ts": iso_from_ms(start_ts_ms),
        "entry_price": entry_price,
        "outcome": outcome,
        "exit_price": exit_price,
        "gross_pct": gross,
        "net_pct": gross - CONFIG["fee_pct"],
    }


def first_touch(
    candles: list[Candle],
    start_idx: int,
    end_idx: int,
    side: str,
    level: float,
) -> tuple[int, float] | None:
    if not math.isfinite(level):
        return None
    for idx in range(max(0, start_idx), min(len(candles), end_idx + 1)):
        c = candles[idx]
        if c.low <= level <= c.high:
            return c.ts_ms, level
    return None


def alt_entries(candles: list[Candle], entry_idx: int, trigger: dict[str, Any], signal: dict[str, Any]) -> list[dict[str, Any]]:
    side = signal["side"]
    closes = [c.close for c in candles]
    ema20 = ema(closes, 20)
    signal_ts_ms = iso_to_ms(signal["ts"])
    rows = [
        simulate_levels(candles, signal_ts_ms, safe_float(signal["entry"]), signal, "immediate"),
    ]

    trigger_idx = int(trigger["idx"])
    strong_idx = int(trigger["strong_idx"])
    pull_start = min(len(candles) - 1, trigger_idx + 1)
    pull_end = min(len(candles) - 1, trigger_idx + CONFIG["pullback_search_bars_after_trigger"])
    ema_level = ema20[strong_idx] if strong_idx < len(ema20) else float("nan")
    trig = candles[strong_idx]
    retrace = trig.close - (trig.close - trig.open) * 0.382 if side == "buy" else trig.close + (trig.open - trig.close) * 0.382
    original_entry = safe_float(signal["entry"])
    levels = []
    if side == "buy":
        if math.isfinite(ema_level) and ema_level < original_entry:
            levels.append(("ema20", ema_level))
        if retrace < original_entry:
            levels.append(("retrace38", retrace))
    else:
        if math.isfinite(ema_level) and ema_level > original_entry:
            levels.append(("ema20", ema_level))
        if retrace > original_entry:
            levels.append(("retrace38", retrace))
    pull = None
    for _, level in levels:
        pull = first_touch(candles, pull_start, pull_end, side, level)
        if pull:
            break
    if pull:
        rows.append(simulate_levels(candles, pull[0], pull[1], signal, "pullback"))
    else:
        rows.append({"mode": "pullback", "filled": False, "reason": "no_ema20_or_38_touch"})

    atr_val = atr(candles, entry_idx)
    sig = candles[entry_idx]
    if side == "buy":
        breakout_level = sig.high + CONFIG["breakout_atr_buffer_k"] * atr_val
    else:
        breakout_level = sig.low - CONFIG["breakout_atr_buffer_k"] * atr_val
    br_start = min(len(candles) - 1, entry_idx + 1)
    br_end = min(len(candles) - 1, entry_idx + CONFIG["breakout_search_bars_after_signal"])
    breakout = first_touch(candles, br_start, br_end, side, breakout_level)
    if breakout:
        rows.append(simulate_levels(candles, breakout[0], breakout[1], signal, "breakout_confirmation"))
    else:
        rows.append({"mode": "breakout_confirmation", "filled": False, "reason": "no_breakout"})
    return rows


def load_joined() -> list[dict[str, Any]]:
    signals = {row["id"]: row for row in load_jsonl(SIGNALS_PATH)}
    labels = {
        row["signal_id"]: row
        for row in load_jsonl(LABELS_PATH)
        if row.get("valid") is True
    }
    snapshots = {row.get("signal_id"): row for row in load_jsonl(SNAPSHOTS_PATH) if row.get("source") == "ws_main_screener"}
    joined = []
    for signal_id, label in labels.items():
        signal = signals.get(signal_id)
        if not signal:
            continue
        row = {**signal, "label": label, "snapshot": snapshots.get(signal_id)}
        joined.append(row)
    joined.sort(key=lambda r: r["ts"])
    return joined


def analyze_case(signal: dict[str, Any], candles: list[Candle]) -> dict[str, Any]:
    ts_ms = iso_to_ms(signal["ts"])
    entry_idx = completed_candle_index(candles, ts_ms)
    if entry_idx is None or entry_idx >= len(candles):
        return {"id": signal["id"], "error": "no_entry_candle"}
    trigger = find_trigger(candles, entry_idx, signal["side"])
    entry = safe_float(signal["entry"])
    tp1 = safe_float(signal["tp1"])
    target_dist = directional_move(entry, tp1, signal["side"])
    pre_move = directional_move(trigger["price"], entry, signal["side"])
    move_consumed_pct = pre_move / target_dist * 100 if target_dist > 0 else float("nan")
    timing = {
        "move_consumed_pct": move_consumed_pct,
        "bars_since_trigger": entry_idx - int(trigger["idx"]),
        "pre_move_pct": directional_return(trigger["price"], entry, signal["side"]),
        "target_to_tp1_pct": directional_return(entry, tp1, signal["side"]),
        "trigger_body_pct": trigger["body_pct"],
        "trigger_range_pct": trigger["range_pct"],
        "trigger_body_ratio": trigger["body_ratio"],
        "trigger_ts": iso_from_ms(candles[int(trigger["idx"])].ts_ms),
        "trigger_fallback": trigger["fallback"],
    }
    timing.update(mfe_mae_after_entry(candles, entry_idx, signal))
    alternatives = alt_entries(candles, entry_idx, trigger, signal)
    label = signal["label"]
    snapshot = signal.get("snapshot") or {}
    context = snapshot.get("context") or {}
    return {
        "id": signal["id"],
        "ts": signal["ts"],
        "symbol": signal["symbol"],
        "side": signal["side"],
        "direction": direction(signal["side"]),
        "regime": signal.get("regime") or "UNKNOWN",
        "trade_style": signal.get("trade_style") or "UNKNOWN",
        "outcome": label.get("outcome"),
        "label_hold_min": label.get("hold_min"),
        "entry": entry,
        "sl": safe_float(signal["sl"]),
        "tp1": tp1,
        "tp2": safe_float(signal.get("tp2")),
        "hold_min": int(signal.get("hold_min") or 0),
        "detected_on": signal.get("detected_on"),
        "vol_ratio": safe_float(signal.get("vol_ratio")),
        "adx_1h": safe_float(signal.get("adx_1h")),
        "fvg_confirmed": bool(signal.get("fvg_confirmed")),
        "hour_utc": int(signal["ts"][11:13]),
        "context": {
            "adx_4h": safe_float(context.get("adx_4h")),
            "rsi_15m": safe_float((snapshot.get("indicators") or {}).get("15m", {}).get("rsi")),
            "bb_pct_b": safe_float((snapshot.get("indicators") or {}).get("15m", {}).get("bb_pct_b")),
            "day_position": safe_float(context.get("day_position")),
        },
        "timing": timing,
        "alternatives": alternatives,
    }


def group_key(row: dict[str, Any]) -> tuple[str, str]:
    return row["regime"], row["trade_style"]


def summarize_timing(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if not r.get("error")]
    outcomes = Counter(r["outcome"] for r in valid)
    decisive = outcomes["TP1"] + outcomes["TP2"] + outcomes["SL"]
    tp = outcomes["TP1"] + outcomes["TP2"]
    consumed = [safe_float(r["timing"]["move_consumed_pct"]) for r in valid]
    bars = [safe_float(r["timing"]["bars_since_trigger"]) for r in valid]
    mfe = [safe_float(r["timing"]["mfe_after_entry_pct"]) for r in valid]
    mae = [safe_float(r["timing"]["mae_after_entry_pct"]) for r in valid]
    time_rows = [r for r in valid if r["outcome"] == "TIME"]
    tp_rows = [r for r in valid if r["outcome"] in {"TP1", "TP2"}]
    return {
        "n": len(valid),
        "outcomes": dict(outcomes),
        "decisive_wr": pct(tp, decisive),
        "time_rate": pct(outcomes["TIME"], len(valid)),
        "avg_consumed": average(consumed),
        "p50_consumed": median([v for v in consumed if math.isfinite(v)]) if consumed else None,
        "p75_consumed": percentile(consumed, 75),
        "avg_bars_since_trigger": average(bars),
        "avg_mfe": average(mfe),
        "avg_mae": average(mae),
        "time_avg_consumed": average([safe_float(r["timing"]["move_consumed_pct"]) for r in time_rows]),
        "tp_avg_consumed": average([safe_float(r["timing"]["move_consumed_pct"]) for r in tp_rows]),
        "late_ge_100_rate": pct(sum(1 for v in consumed if math.isfinite(v) and v >= 100), len([v for v in consumed if math.isfinite(v)])),
    }


def summarize_alt(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("error"):
            continue
        for alt in row["alternatives"]:
            buckets[(row["regime"], row["trade_style"], alt["mode"])].append(alt)
    out = []
    for (regime, style, mode), sims in sorted(buckets.items()):
        filled = [s for s in sims if s.get("filled")]
        outcomes = Counter(s.get("outcome") for s in filled)
        wins = outcomes["TP1"] + outcomes["TP2"]
        nets = [safe_float(s.get("net_pct")) for s in filled]
        out.append(
            {
                "regime": regime,
                "trade_style": style,
                "mode": mode,
                "signals": len(sims),
                "filled": len(filled),
                "fill_rate": pct(len(filled), len(sims)),
                "avg_net_pct": average(nets),
                "median_net_pct": median([v for v in nets if math.isfinite(v)]) if nets else None,
                "wr": pct(wins, len(filled)),
                "time_rate": pct(outcomes["TIME"], len(filled)),
                "sl_rate": pct(outcomes["SL"], len(filled)),
                "outcomes": dict(outcomes),
            }
        )
    return out


def session(hour: int) -> str:
    if 0 <= hour <= 6:
        return "Asia 00-06"
    if 7 <= hour <= 15:
        return "EU 07-15"
    return "US 16-23"


def adx_bucket(value: float) -> str:
    if not math.isfinite(value):
        return "unknown"
    if value < 20:
        return "<20"
    if value < 25:
        return "20-25"
    if value < 35:
        return "25-35"
    return "35+"


def vol_bucket(value: float) -> str:
    if not math.isfinite(value):
        return "unknown"
    if value < 1:
        return "<1"
    if value < 2:
        return "1-2"
    return "2+"


def simple_split(rows: list[dict[str, Any]], name: str, getter) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if not row.get("error"):
            buckets[str(getter(row))].append(row)
    out = []
    for key, vals in sorted(buckets.items()):
        outcomes = Counter(v["outcome"] for v in vals)
        consumed = [safe_float(v["timing"]["move_consumed_pct"]) for v in vals]
        out.append(
            {
                "split": name,
                "bucket": key,
                "n": len(vals),
                "tp_rate": pct(outcomes["TP1"] + outcomes["TP2"], len(vals)),
                "time_rate": pct(outcomes["TIME"], len(vals)),
                "sl_rate": pct(outcomes["SL"], len(vals)),
                "avg_consumed": average(consumed),
            }
        )
    return out


def render_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" if i == 0 else "---:" for i in range(len(headers))) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return lines


def recommendation_for(regime: str, style: str, alt_rows: list[dict[str, Any]], timing: dict[str, Any]) -> str:
    candidates = [
        r for r in alt_rows
        if r["regime"] == regime and r["trade_style"] == style and r["filled"] >= max(2, min(8, r["signals"] // 2))
    ]
    if not candidates:
        return "no recommendation: too few filled alternative entries"
    ranked = sorted(
        candidates,
        key=lambda r: (
            safe_float(r["avg_net_pct"]),
            -safe_float(r["time_rate"]),
            safe_float(r["wr"]),
        ),
        reverse=True,
    )
    best = ranked[0]
    n = timing.get("n", 0)
    avg_net = safe_float(best["avg_net_pct"])
    wr = safe_float(best["wr"])
    time_rate = safe_float(best["time_rate"])
    note = "small sample" if n < 15 else "normal live sample"
    if avg_net <= 0:
        return (
            f"inconclusive for {regime} x {style}: best filled mode is {best['mode']} but avg net is "
            f"{fmt(avg_net, '%')} with fill {fmt(best['fill_rate'], '%')} ({note})"
        )
    if n < 15 and avg_net < 0.20:
        return (
            f"inconclusive for {regime} x {style}: {best['mode']} is only weakly positive "
            f"({fmt(avg_net, '%')} avg net) on {n} signals"
        )
    if wr < 40 and time_rate > 50:
        return (
            f"{best['mode']} is a research candidate for {regime} x {style}, not production-ready: "
            f"avg net {fmt(avg_net, '%')} but WR {fmt(wr, '%')} and TIME {fmt(time_rate, '%')}"
        )
    return (
        f"{best['mode']} for {regime} x {style}: avg net {fmt(best['avg_net_pct'], '%')}, "
        f"WR {fmt(best['wr'], '%')}, TIME {fmt(best['time_rate'], '%')}, fill {fmt(best['fill_rate'], '%')} ({note})"
    )


def render_report(rows: list[dict[str, Any]], alt_rows: list[dict[str, Any]], split_rows: list[dict[str, Any]]) -> str:
    valid = [r for r in rows if not r.get("error")]
    errors = [r for r in rows if r.get("error")]
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        by_group[group_key(row)].append(row)
    timing_rows = []
    timing_summary = {}
    for key, vals in sorted(by_group.items()):
        summary = summarize_timing(vals)
        timing_summary[key] = summary
        timing_rows.append(
            [
                key[0],
                key[1],
                summary["n"],
                fmt(summary["decisive_wr"], "%"),
                fmt(summary["time_rate"], "%"),
                fmt(summary["avg_consumed"], "%"),
                fmt(summary["p50_consumed"], "%"),
                fmt(summary["time_avg_consumed"], "%"),
                fmt(summary["tp_avg_consumed"], "%"),
                fmt(summary["avg_bars_since_trigger"], ""),
                fmt(summary["avg_mfe"], "%"),
                fmt(summary["avg_mae"], "%"),
            ]
        )

    outcome_rows = []
    for outcome in ["TP1", "TP2", "SL", "TIME"]:
        vals = [r for r in valid if r["outcome"] == outcome]
        s = summarize_timing(vals)
        outcome_rows.append(
            [
                outcome,
                s["n"],
                fmt(s["avg_consumed"], "%"),
                fmt(s["p50_consumed"], "%"),
                fmt(s["avg_bars_since_trigger"], ""),
                fmt(s["avg_mfe"], "%"),
                fmt(s["avg_mae"], "%"),
            ]
        )

    alt_table = []
    for row in alt_rows:
        alt_table.append(
            [
                row["regime"],
                row["trade_style"],
                row["mode"],
                row["signals"],
                row["filled"],
                fmt(row["fill_rate"], "%"),
                fmt(row["avg_net_pct"], "%"),
                fmt(row["wr"], "%"),
                fmt(row["time_rate"], "%"),
                fmt(row["sl_rate"], "%"),
            ]
        )

    rec_lines = []
    for key, summary in sorted(timing_summary.items()):
        rec_lines.append(f"- {recommendation_for(key[0], key[1], alt_rows, summary)}.")

    split_table = [
        [
            row["split"],
            row["bucket"],
            row["n"],
            fmt(row["tp_rate"], "%"),
            fmt(row["time_rate"], "%"),
            fmt(row["sl_rate"], "%"),
            fmt(row["avg_consumed"], "%"),
        ]
        for row in split_rows
    ]

    late_time = average([safe_float(r["timing"]["move_consumed_pct"]) for r in valid if r["outcome"] == "TIME"])
    late_tp = average([safe_float(r["timing"]["move_consumed_pct"]) for r in valid if r["outcome"] in {"TP1", "TP2"}])
    lines = [
        "# Main Screener Entry Timing - 21.05.2026",
        "",
        "Scope: `logs/signals/main_signals.jsonl` joined to `main_signals_labels.jsonl`, only `valid: true`. Price replay uses 5m candles from local screener cache when available, otherwise public OKX history candles. Alternative-entry results are approximate 5m OHLC replays with original SL/TP levels and 0.20% round-trip taker fee.",
        "",
        f"- valid joined signals analyzed: `{len(valid)}`",
        f"- signals skipped: `{len(errors)}`",
        f"- TIME avg consumed: `{fmt(late_time, '%')}`; TP avg consumed: `{fmt(late_tp, '%')}`",
        "",
        "## Entry Timing By Regime x Style",
        "",
    ]
    lines.extend(
        render_table(
            ["regime", "style", "n", "decisive WR", "TIME", "avg consumed", "p50 consumed", "TIME consumed", "TP consumed", "bars", "MFE", "MAE"],
            timing_rows,
        )
    )
    lines.extend(["", "## Entry Timing By Outcome", ""])
    lines.extend(
        render_table(
            ["outcome", "n", "avg consumed", "p50 consumed", "bars", "MFE", "MAE"],
            outcome_rows,
        )
    )
    lines.extend(["", "## Alternative Entries", ""])
    lines.extend(
        render_table(
            ["regime", "style", "mode", "signals", "filled", "fill", "avg net", "WR", "TIME", "SL"],
            alt_table,
        )
    )
    lines.extend(["", "## Recommended Entry Style", ""])
    lines.extend(rec_lines)
    lines.extend(
        [
            "",
            "Interpretation notes:",
            "",
            "- `move_consumed_pct` is measured from detected trigger-start open to the live entry, divided by live entry-to-TP1 distance. Values above 100% mean the pre-entry move was already larger than the remaining TP1 path.",
            "- `pullback` waits for EMA20 or 38.2% impulse retrace after the detected trigger. If there is no touch, it is counted as no-fill and the fill rate matters.",
            "- `breakout_confirmation` waits for a small ATR-buffer break beyond the live signal candle. It is intentionally stricter and can reduce fills.",
            "",
            "## GPT Hypotheses",
            "",
        ]
    )
    lines.extend(render_table(["split", "bucket", "n", "TP", "TIME", "SL", "avg consumed"], split_table))
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- This is a small live sample. RANGING is especially thin, so treat its recommendation as hypothesis-grade.",
            "- 5m OHLC cannot know the true intrabar order when both SL and TP are inside one candle; the simulator uses conservative SL-first ordering.",
            "- Missing local candles were fetched from OKX public history during this run; the saved summary does not include raw candle dumps.",
        ]
    )
    return "\n".join(lines) + "\n"


def chart_window(candles: list[Candle], row: dict[str, Any]) -> list[Candle]:
    ts_ms = iso_to_ms(row["ts"])
    start = ts_ms - 18 * CONFIG["bar_ms"]
    end = ts_ms + min(48, max(18, row["hold_min"] // 5 + 6)) * CONFIG["bar_ms"]
    return [c for c in candles if start <= c.ts_ms <= end]


def render_case_png(path: Path, row: dict[str, Any], candles: list[Candle]) -> None:
    subset = chart_window(candles, row)
    if len(subset) < 8:
        return
    idx_by_ts = {c.ts_ms: i for i, c in enumerate(subset)}
    trigger_ts = iso_to_ms(row["timing"]["trigger_ts"])
    signal_ts = iso_to_ms(row["ts"])
    trigger_idx = min(idx_by_ts, key=lambda ts: abs(ts - trigger_ts))
    signal_idx = min(idx_by_ts, key=lambda ts: abs((ts + CONFIG["bar_ms"]) - signal_ts))
    x_trigger = idx_by_ts[trigger_idx]
    x_signal = idx_by_ts[signal_idx]
    side = row["side"]

    fig, ax = plt.subplots(figsize=(12, 6.5))
    for i, c in enumerate(subset):
        color = "#15936b" if c.close >= c.open else "#c23b3b"
        ax.vlines(i, c.low, c.high, color=color, linewidth=0.9)
        body_low = min(c.open, c.close)
        body_h = max(abs(c.close - c.open), (c.high - c.low) * 0.02)
        ax.add_patch(patches.Rectangle((i - 0.35, body_low), 0.7, body_h, color=color, alpha=0.78))

    ax.axvspan(x_trigger - 0.5, x_trigger + 0.5, color="#f4c542", alpha=0.18, label="trigger start")
    ax.axvline(x_signal, color="#1f4e99", linestyle=":", linewidth=1.1, label="bot entry time")
    ax.scatter([x_signal], [row["entry"]], s=58, color="#0b5bd3", zorder=8, label="bot entry")

    peak_slice = subset[x_trigger : min(len(subset), x_signal + 10)]
    if peak_slice:
        if side == "buy":
            peak = max(peak_slice, key=lambda c: c.high)
            peak_price = peak.high
        else:
            peak = min(peak_slice, key=lambda c: c.low)
            peak_price = peak.low
        ax.annotate(
            "trigger -> peak",
            xy=(idx_by_ts[peak.ts_ms], peak_price),
            xytext=(x_trigger, row["timing"]["pre_move_pct"] and row["entry"]),
            arrowprops={"arrowstyle": "->", "color": "#6f42c1", "lw": 1.2},
            color="#6f42c1",
            fontsize=8,
        )

    for key, color, linestyle in [
        ("sl", "#d62728", "--"),
        ("tp1", "#2ca02c", "--"),
        ("tp2", "#17a2b8", ":"),
    ]:
        price = row.get(key)
        if math.isfinite(safe_float(price)):
            ax.axhline(price, color=color, linestyle=linestyle, linewidth=0.9, alpha=0.9)
            ax.text(len(subset) - 1, price, f" {key.upper()}", color=color, fontsize=8, va="bottom", ha="right")

    title = (
        f"{row['regime']} {row['trade_style']} {row['symbol']} {row['direction']} "
        f"{row['outcome']} | consumed {fmt(row['timing']['move_consumed_pct'], '%')} "
        f"bars {fmt(row['timing']['bars_since_trigger'])}"
    )
    ax.set_title(title, fontsize=10, loc="left")
    step = max(1, len(subset) // 9)
    ticks = list(range(0, len(subset), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([iso_from_ms(subset[i].ts_ms)[11:16] for i in ticks], fontsize=8)
    ax.grid(alpha=0.18)
    ax.legend(loc="best", fontsize=8)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_cases(rows: list[dict[str, Any]], candle_cache: dict[str, list[Candle]]) -> None:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    for old in CASE_DIR.glob("*_21_05_2026.png"):
        old.unlink()
    valid = [r for r in rows if not r.get("error")]
    for regime in sorted({r["regime"] for r in valid}):
        regime_rows = [r for r in valid if r["regime"] == regime]
        ranked = sorted(
            regime_rows,
            key=lambda r: (
                0 if r["outcome"] == "TIME" else 1 if r["outcome"] == "SL" else 2,
                -safe_float(r["timing"]["move_consumed_pct"]),
            ),
        )
        for idx, row in enumerate(ranked[: CONFIG["case_limit_per_regime"]], start=1):
            candles = candle_cache.get(row["symbol"], [])
            safe_ts = row["ts"].replace(":", "").replace("-", "").replace("Z", "")
            path = CASE_DIR / f"{regime.lower()}_{idx:02d}_{row['symbol']}_{row['outcome']}_{safe_ts}_21_05_2026.png"
            render_case_png(path, row, candles)


def prepare_candles(joined: list[dict[str, Any]]) -> dict[str, list[Candle]]:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in joined:
        by_symbol[row["symbol"]].append(row)
    candles_by_symbol = {}
    for symbol, rows in sorted(by_symbol.items()):
        candles = load_cached_candles(symbol)
        source_notes = []
        if candles:
            source_notes.append(f"local={len(candles)}")
        fetch_count = 0
        for signal in sorted(rows, key=lambda r: r["ts"]):
            if has_signal_window(candles, signal):
                continue
            start, end = signal_window_bounds(signal)
            try:
                fetched = fetch_okx_window(symbol, start, end)
                candles = merge_candles(candles, fetched)
                fetch_count += 1
            except Exception as exc:
                log(f"{symbol}: OKX window failed for {signal['ts']}: {exc}")
        if fetch_count:
            source_notes.append(f"okx_windows={fetch_count}")
        candles_by_symbol[symbol] = candles
        covered = sum(1 for signal in rows if has_signal_window(candles, signal))
        log(f"{symbol}: candles ready ({len(candles)}), covered {covered}/{len(rows)}; {', '.join(source_notes) or 'empty'}")
    return candles_by_symbol


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")

    joined = load_joined()
    log(f"joined valid signals: {len(joined)}")
    candles_by_symbol = prepare_candles(joined)

    rows = []
    for signal in joined:
        candles = candles_by_symbol.get(signal["symbol"], [])
        if not candles:
            rows.append({"id": signal["id"], "symbol": signal["symbol"], "error": "no_candles"})
            continue
        rows.append(analyze_case(signal, candles))

    valid_rows = [r for r in rows if not r.get("error")]
    alt_rows = summarize_alt(valid_rows)
    split_rows = []
    split_rows.extend(simple_split(valid_rows, "session", lambda r: session(r["hour_utc"])))
    split_rows.extend(simple_split(valid_rows, "adx_1h", lambda r: adx_bucket(r["adx_1h"])))
    split_rows.extend(simple_split(valid_rows, "vol_ratio", lambda r: vol_bucket(r["vol_ratio"])))
    split_rows.extend(simple_split(valid_rows, "fvg", lambda r: "yes" if r["fvg_confirmed"] else "no"))
    split_rows.extend(simple_split(valid_rows, "side", lambda r: r["direction"]))

    REPORT_MD.write_text(render_report(rows, alt_rows, split_rows), encoding="utf-8")
    summary = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": CONFIG,
        "valid_analyzed": len(valid_rows),
        "skipped": [r for r in rows if r.get("error")],
        "timing_by_regime_style": {
            f"{regime}|{style}": summarize_timing([r for r in valid_rows if (r["regime"], r["trade_style"]) == (regime, style)])
            for regime, style in sorted({(r["regime"], r["trade_style"]) for r in valid_rows})
        },
        "alternative_entries": alt_rows,
        "hypothesis_splits": split_rows,
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    generate_cases(valid_rows, candles_by_symbol)

    log(f"saved {REPORT_MD}")
    log(f"saved {SUMMARY_JSON}")
    log(f"saved cases to {CASE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
