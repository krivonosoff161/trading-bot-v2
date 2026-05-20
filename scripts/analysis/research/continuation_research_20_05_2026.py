from __future__ import annotations

import csv
import gzip
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[3]
TICK_ROOT = Path(r"E:\trading-data\ticks")
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
CASE_DIR = OUT_DIR / "continuation_cases_20_05_2026"

PATTERNS_JSON = OUT_DIR / "continuation_patterns_20_05_2026.json"
PATTERNS_MD = OUT_DIR / "continuation_patterns_20_05_2026.md"
MFE_MD = OUT_DIR / "continuation_mfe_distribution_20_05_2026.md"
EXIT_MD = OUT_DIR / "continuation_exit_grid_20_05_2026.md"
HYP_MD = OUT_DIR / "continuation_hypotheses_20_05_2026.md"

FEE_PCT = 0.20
EXPLOSION_PCT = 0.8
SLIPPAGES = [0.00, 0.03, 0.05, 0.10]
HOLDS = [3, 5, 10, 15, 20]
TP_GRID = [0.4, 0.5, 0.7, 0.8, 1.0, 1.2, 1.5]
SL_GRID = [0.5, 0.8, 1.0]
WICK_MINS = [0.10, 0.15, 0.20]
MFE_BUCKETS = [0.3, 0.5, 0.7, 1.0, 1.2, 1.5, 2.0]

PRIMARY_SLIPPAGE = 0.05
PRIMARY_HOLD = 10
PRIMARY_WICK_MIN = 0.15

PAIRS = [
    "BILL-USDT-SWAP",
    "EDEN-USDT-SWAP",
    "TRUTH-USDT-SWAP",
    "RLS-USDT-SWAP",
    "UB-USDT-SWAP",
    "AI-USDT-SWAP",
    "BSB-USDT-SWAP",
    "SPACE-USDT-SWAP",
    "SAHARA-USDT-SWAP",
    "JELLYJELLY-USDT-SWAP",
    "NOT-USDT-SWAP",
    "BOME-USDT-SWAP",
    "OFC-USDT-SWAP",
    "USELESS-USDT-SWAP",
    "LAYER-USDT-SWAP",
    "CHIP-USDT-SWAP",
    "ONT-USDT-SWAP",
    "FOGO-USDT-SWAP",
    "HOME-USDT-SWAP",
]


@dataclass(slots=True)
class Bar:
    minute_ms: int
    ts: str
    date: str
    hour: int
    open: float
    high: float
    low: float
    close: float
    close_ts_ms: int
    volume: float

    @property
    def change_pct(self) -> float:
        return (self.close - self.open) / self.open * 100 if self.open > 0 else float("nan")

    @property
    def range_pct(self) -> float:
        return (self.high - self.low) / self.open * 100 if self.open > 0 else float("nan")

    @property
    def close_location(self) -> float:
        rng = self.high - self.low
        return (self.close - self.low) / rng if rng > 0 else 0.5


@dataclass(slots=True)
class Event:
    pair: str
    pattern: str
    minute_ms: int
    ts: str
    date: str
    hour: int
    direction: str
    entry_price_raw: float
    entry_ts_ms: int
    signal_size_pct: float
    repeated_5m: int
    meta: dict[str, Any]


def safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if math.isfinite(out) else float("nan")


def iso_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def date_from_file(path: Path) -> str:
    return path.name.removesuffix(".csv.gz").removesuffix(".csv")


def tick_files(pair_dir: Path) -> list[Path]:
    return sorted([*pair_dir.glob("*.csv"), *pair_dir.glob("*.csv.gz")], key=lambda p: date_from_file(p))


def open_tick_file(path: Path):
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


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


def directional_return(entry: float, price: float, direction: str) -> float:
    if entry <= 0 or price <= 0:
        return float("nan")
    if direction == "long":
        return (price - entry) / entry * 100
    return (entry - price) / entry * 100


def slipped_entry(price: float, direction: str, slippage_pct: float) -> float:
    if direction == "long":
        return price * (1 + slippage_pct / 100)
    return price * (1 - slippage_pct / 100)


def aggregate_pair(pair: str, keep_ticks: bool) -> tuple[list[Bar], dict[int, list[tuple[int, float]]]]:
    pair_dir = TICK_ROOT / pair
    bars: list[Bar] = []
    ticks_by_minute: dict[int, list[tuple[int, float]]] = {} if keep_ticks else {}
    for path in tick_files(pair_dir):
        minutes: dict[int, dict[str, Any]] = {}
        date = date_from_file(path)
        with open_tick_file(path) as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                side = (row.get("side") or "").lower()
                if side == "gap" or side not in {"buy", "sell"}:
                    continue
                ts_ms = int(float(row["ts_ms"]))
                price = safe_float(row.get("price"))
                size = safe_float(row.get("size"))
                if not math.isfinite(price) or not math.isfinite(size):
                    continue
                minute_ms = ts_ms - ts_ms % 60000
                bucket = minutes.get(minute_ms)
                if bucket is None:
                    bucket = {
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        "close_ts_ms": ts_ms,
                        "volume": 0.0,
                        "ticks": [] if keep_ticks else None,
                    }
                    minutes[minute_ms] = bucket
                bucket["high"] = max(bucket["high"], price)
                bucket["low"] = min(bucket["low"], price)
                bucket["close"] = price
                bucket["close_ts_ms"] = ts_ms
                bucket["volume"] += size
                if keep_ticks:
                    bucket["ticks"].append((ts_ms, price))
        for minute_ms in sorted(minutes):
            b = minutes[minute_ms]
            dt = datetime.fromtimestamp(minute_ms / 1000, tz=timezone.utc)
            bars.append(
                Bar(
                    minute_ms=minute_ms,
                    ts=iso_from_ms(minute_ms),
                    date=date,
                    hour=dt.hour,
                    open=b["open"],
                    high=b["high"],
                    low=b["low"],
                    close=b["close"],
                    close_ts_ms=b["close_ts_ms"],
                    volume=b["volume"],
                )
            )
            if keep_ticks:
                ticks_by_minute[minute_ms] = b["ticks"]
    bars.sort(key=lambda b: b.minute_ms)
    return bars, ticks_by_minute


def prior_explosions(minute_ms: int, explosion_minutes: list[int]) -> int:
    return sum(1 for m in explosion_minutes if 0 < minute_ms - m <= 5 * 60000)


def detect_single_impulse(pair: str, bars: list[Bar]) -> list[Event]:
    explosion_minutes = [b.minute_ms for b in bars if abs(b.change_pct) >= EXPLOSION_PCT]
    events: list[Event] = []
    for b in bars:
        move = b.change_pct
        if not math.isfinite(move) or abs(move) < EXPLOSION_PCT:
            continue
        direction = "long" if move > 0 else "short"
        close_ok = b.close_location >= 0.75 if direction == "long" else b.close_location <= 0.25
        size_bucket = "small" if abs(move) < 1.1 else "medium" if abs(move) < 1.5 else "large"
        events.append(
            Event(
                pair=pair,
                pattern="single_impulse",
                minute_ms=b.minute_ms,
                ts=b.ts,
                date=b.date,
                hour=b.hour,
                direction=direction,
                entry_price_raw=b.close,
                entry_ts_ms=b.close_ts_ms,
                signal_size_pct=move,
                repeated_5m=prior_explosions(b.minute_ms, explosion_minutes),
                meta={"size_bucket": size_bucket, "close_location": b.close_location, "close_location_ok": close_ok},
            )
        )
    return events


def staircase_pass(window: list[Bar], direction: str, cum_threshold: float, min_same: int, max_opposite: float) -> bool:
    first = window[0]
    last = window[-1]
    cum = (last.close - first.open) / first.open * 100 if first.open > 0 else float("nan")
    moves = [b.change_pct for b in window]
    if direction == "long":
        same = sum(1 for m in moves if m > 0)
        bad_opp = any(m <= -max_opposite for m in moves)
        close_ok = last.close_location >= 0.60
        return same >= min_same and cum >= cum_threshold and not bad_opp and close_ok
    same = sum(1 for m in moves if m < 0)
    bad_opp = any(m >= max_opposite for m in moves)
    close_ok = last.close_location <= 0.40
    return same >= min_same and cum <= -cum_threshold and not bad_opp and close_ok


def detect_staircase(pair: str, bars: list[Bar], n: int = 3, min_same: int = 2, cum_threshold: float = 1.2, max_opposite: float = 0.5) -> list[Event]:
    explosion_minutes = [b.minute_ms for b in bars if abs(b.change_pct) >= EXPLOSION_PCT]
    events: list[Event] = []
    seen: set[tuple[int, str]] = set()
    for i in range(n - 1, len(bars)):
        window = bars[i - n + 1 : i + 1]
        if any(window[j].minute_ms - window[j - 1].minute_ms != 60000 for j in range(1, len(window))):
            continue
        for direction in ("long", "short"):
            if not staircase_pass(window, direction, cum_threshold, min_same, max_opposite):
                continue
            last = window[-1]
            key = (last.minute_ms, direction)
            if key in seen:
                continue
            seen.add(key)
            cum = (last.close - window[0].open) / window[0].open * 100
            events.append(
                Event(
                    pair=pair,
                    pattern="staircase",
                    minute_ms=last.minute_ms,
                    ts=last.ts,
                    date=last.date,
                    hour=last.hour,
                    direction=direction,
                    entry_price_raw=last.close,
                    entry_ts_ms=last.close_ts_ms,
                    signal_size_pct=cum,
                    repeated_5m=prior_explosions(last.minute_ms, explosion_minutes),
                    meta={"window": n, "min_same": min_same, "cum_threshold": cum_threshold, "max_opposite": max_opposite},
                )
            )
    return events


def staircase_variant_counts(bars: list[Bar]) -> dict[str, int]:
    out: dict[str, int] = {}
    variants = [(3, 2), (4, 3), (5, 4)]
    for n, min_same in variants:
        for cum in [1.0, 1.2, 1.5]:
            for opp in [0.4, 0.5, 0.7]:
                key = f"{min_same}_of_{n}_cum_{cum}_opp_{opp}"
                out[key] = len(detect_staircase("_", bars, n=n, min_same=min_same, cum_threshold=cum, max_opposite=opp))
    return out


def ticks_for_event(ticks: dict[int, list[tuple[int, float]]], entry_ts_ms: int, hold_min: int) -> list[tuple[int, float]]:
    end = entry_ts_ms + hold_min * 60000
    minute = entry_ts_ms - entry_ts_ms % 60000
    out: list[tuple[int, float]] = []
    while minute <= end:
        for ts_ms, price in ticks.get(minute, []):
            if entry_ts_ms < ts_ms <= end:
                out.append((ts_ms, price))
        minute += 60000
    return out


def mfe_stats(event: Event, event_ticks: list[tuple[int, float]], slippage: float, hold_min: int) -> dict[str, Any]:
    entry = slipped_entry(event.entry_price_raw, event.direction, slippage)
    best = 0.0
    worst = 0.0
    best_time_ms = 0
    last_ret = 0.0
    end_ts_ms = event.entry_ts_ms + hold_min * 60000
    for ts_ms, price in event_ticks:
        if ts_ms > end_ts_ms:
            break
        ret = directional_return(entry, price, event.direction)
        last_ret = ret
        if ret > best:
            best = ret
            best_time_ms = ts_ms - event.entry_ts_ms
        worst = min(worst, ret)
    return {
        "mfe_pct": best,
        "mae_pct": worst,
        "time_to_mfe_sec": best_time_ms / 1000,
        "horizon_return_pct": last_ret,
        "horizon_net_pct": last_ret - FEE_PCT,
    }


def initial_stop_price(event: Event, bar: Bar, entry: float, direction: str, sl: float | str) -> float:
    if isinstance(sl, str):
        if direction == "long":
            return min(bar.low, entry * 0.999)
        return max(bar.high, entry * 1.001)
    if direction == "long":
        return entry * (1 - sl / 100)
    return entry * (1 + sl / 100)


def stop_return(entry: float, stop: float, direction: str) -> float:
    return directional_return(entry, stop, direction)


def structural_candidate(bar: Bar, direction: str, wick_min_pct: float) -> float | None:
    if direction == "long":
        lower_wick = min(bar.open, bar.close) - bar.low
        if bar.open <= 0 or lower_wick / bar.open * 100 < wick_min_pct:
            return None
        if bar.close < bar.open:
            return None
        return bar.low + 0.5 * lower_wick
    upper_wick = bar.high - max(bar.open, bar.close)
    if bar.open <= 0 or upper_wick / bar.open * 100 < wick_min_pct:
        return None
    if bar.close > bar.open:
        return None
    return bar.high - 0.5 * upper_wick


def simulate_exit(
    event: Event,
    bars_by_minute: dict[int, Bar],
    event_ticks: list[tuple[int, float]],
    mode: str,
    sl: float | str,
    tp: float | None,
    hold_min: int,
    slippage: float,
    wick_min_pct: float = PRIMARY_WICK_MIN,
) -> dict[str, Any]:
    entry = slipped_entry(event.entry_price_raw, event.direction, slippage)
    signal_bar = bars_by_minute[event.minute_ms]
    active_stop = initial_stop_price(event, signal_bar, entry, event.direction, sl)
    completed_minute = event.minute_ms
    max_hold_ms = event.entry_ts_ms + hold_min * 60000
    best = 0.0
    worst = 0.0
    last_price = event.entry_price_raw
    stop_points: list[tuple[int, float]] = [(event.entry_ts_ms, active_stop)]
    outcome = "timeout"
    gross = 0.0
    exit_ts_ms = max_hold_ms
    for ts_ms, price in event_ticks:
        if ts_ms > max_hold_ms:
            break
        last_price = price
        while completed_minute + 60000 <= ts_ms - ts_ms % 60000:
            completed_minute += 60000
            bar = bars_by_minute.get(completed_minute)
            if bar is None:
                continue
            candidate = structural_candidate(bar, event.direction, wick_min_pct)
            if candidate is None:
                continue
            if event.direction == "long" and candidate > active_stop:
                active_stop = candidate
                stop_points.append((ts_ms, active_stop))
            elif event.direction == "short" and candidate < active_stop:
                active_stop = candidate
                stop_points.append((ts_ms, active_stop))

        ret = directional_return(entry, price, event.direction)
        best = max(best, ret)
        worst = min(worst, ret)
        if mode in {"tp", "hybrid"} and tp is not None and ret >= tp:
            outcome = "tp"
            gross = tp
            exit_ts_ms = ts_ms
            break
        hit_stop = price <= active_stop if event.direction == "long" else price >= active_stop
        if hit_stop:
            outcome = "trail" if mode in {"trail", "hybrid"} else "sl"
            gross = stop_return(entry, active_stop, event.direction)
            exit_ts_ms = ts_ms
            break
    else:
        gross = directional_return(entry, last_price, event.direction)
    return {
        "outcome": outcome,
        "gross_pct": gross,
        "net_pct": gross - FEE_PCT,
        "mfe_pct": best,
        "mae_pct": worst,
        "capture_ratio": gross / best if best > 0 else float("nan"),
        "exit_ts_ms": exit_ts_ms,
        "stop_points": stop_points,
    }


def summarize_returns(rows: list[dict[str, Any]]) -> dict[str, Any]:
    vals = [safe_float(r["net_pct"]) for r in rows]
    gross = [safe_float(r["gross_pct"]) for r in rows]
    mfe = [safe_float(r.get("mfe_pct")) for r in rows]
    capture = [safe_float(r.get("capture_ratio")) for r in rows if math.isfinite(safe_float(r.get("capture_ratio")))]
    outcomes = Counter(r["outcome"] for r in rows)
    n = len(rows)
    return {
        "n": n,
        "avg_gross_pct": average(gross),
        "avg_net_pct": average(vals),
        "median_net_pct": median([v for v in vals if math.isfinite(v)]) if vals else None,
        "win_net_rate": pct(sum(1 for v in vals if v > 0), n),
        "tp_rate": pct(outcomes["tp"], n),
        "sl_rate": pct(outcomes["sl"], n),
        "trail_rate": pct(outcomes["trail"], n),
        "timeout_rate": pct(outcomes["timeout"], n),
        "avg_mfe_pct": average(mfe),
        "capture_ratio_avg": average(capture),
    }


def empty_accum() -> dict[str, Any]:
    return {
        "n": 0,
        "gross_sum": 0.0,
        "net_sum": 0.0,
        "net_values": [],
        "net_wins": 0,
        "mfe_sum": 0.0,
        "capture_sum": 0.0,
        "capture_n": 0,
        "outcomes": Counter(),
    }


def update_accum(acc: dict[str, Any], sim: dict[str, Any]) -> None:
    net = safe_float(sim["net_pct"])
    gross = safe_float(sim["gross_pct"])
    mfe = safe_float(sim.get("mfe_pct"))
    capture = safe_float(sim.get("capture_ratio"))
    acc["n"] += 1
    acc["gross_sum"] += gross
    acc["net_sum"] += net
    acc["net_values"].append(net)
    if net > 0:
        acc["net_wins"] += 1
    acc["mfe_sum"] += mfe
    if math.isfinite(capture):
        acc["capture_sum"] += capture
        acc["capture_n"] += 1
    acc["outcomes"][sim["outcome"]] += 1


def finalize_accum(acc: dict[str, Any]) -> dict[str, Any]:
    n = acc["n"]
    outcomes = acc["outcomes"]
    net_values = acc["net_values"]
    return {
        "n": n,
        "avg_gross_pct": acc["gross_sum"] / n if n else float("nan"),
        "avg_net_pct": acc["net_sum"] / n if n else float("nan"),
        "median_net_pct": median(net_values) if net_values else None,
        "win_net_rate": pct(acc["net_wins"], n),
        "tp_rate": pct(outcomes["tp"], n),
        "sl_rate": pct(outcomes["sl"], n),
        "trail_rate": pct(outcomes["trail"], n),
        "timeout_rate": pct(outcomes["timeout"], n),
        "avg_mfe_pct": acc["mfe_sum"] / n if n else float("nan"),
        "capture_ratio_avg": acc["capture_sum"] / acc["capture_n"] if acc["capture_n"] else float("nan"),
    }


def mfe_distribution(stats: list[dict[str, Any]]) -> dict[str, Any]:
    mfes = [safe_float(s["mfe_pct"]) for s in stats]
    times = [safe_float(s["time_to_mfe_sec"]) for s in stats if safe_float(s["mfe_pct"]) > 0]
    n = len(mfes)
    return {
        "n": n,
        "avg_mfe_pct": average(mfes),
        "p25_mfe_pct": percentile(mfes, 25),
        "p50_mfe_pct": percentile(mfes, 50),
        "p75_mfe_pct": percentile(mfes, 75),
        "p90_mfe_pct": percentile(mfes, 90),
        "avg_time_to_mfe_sec": average(times),
        "p50_time_to_mfe_sec": median(times) if times else None,
        "buckets": {str(b): pct(sum(1 for v in mfes if v >= b), n) for b in MFE_BUCKETS},
    }


def event_to_json(event: Event, mfe: dict[str, Any]) -> dict[str, Any]:
    return {
        "pair": event.pair,
        "pattern": event.pattern,
        "ts": event.ts,
        "minute_ms": event.minute_ms,
        "direction": event.direction,
        "signal_size_pct": event.signal_size_pct,
        "repeated_5m": event.repeated_5m,
        "meta": event.meta,
        "mfe": mfe,
    }


def session_name(hour: int) -> str:
    if 0 <= hour <= 6:
        return "Asia 00-06"
    if 7 <= hour <= 15:
        return "EU 07-15"
    return "US 16-23"


def analyze_pair(pair: str) -> dict[str, Any]:
    print(f"continuation analyze {pair}")
    bars, ticks = aggregate_pair(pair, keep_ticks=True)
    if not bars:
        return {"pair": pair, "error": "no bars"}
    bars_by_minute = {b.minute_ms: b for b in bars}
    single = detect_single_impulse(pair, bars)
    staircase = detect_staircase(pair, bars)
    events = single + staircase
    event_mfe: list[dict[str, Any]] = []
    exit_accum: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(empty_accum)
    case_candidates: list[dict[str, Any]] = []

    for idx, event in enumerate(events, start=1):
        if idx % 50 == 0:
            print(f"{pair}: simulated {idx}/{len(events)} events")
        event_ticks = ticks_for_event(ticks, event.entry_ts_ms, 20)
        mfe = mfe_stats(event, event_ticks, PRIMARY_SLIPPAGE, 20)
        event_mfe.append(event_to_json(event, mfe))
        primary_trail = simulate_exit(event, bars_by_minute, event_ticks, "trail", 0.8, None, 15, PRIMARY_SLIPPAGE, PRIMARY_WICK_MIN)
        case_candidates.append({"event": event, "mfe": mfe, "exit": primary_trail})
        for slip in SLIPPAGES:
            for hold in HOLDS:
                for sl in SL_GRID:
                    trail = simulate_exit(event, bars_by_minute, event_ticks, "trail", sl, None, hold, slip, PRIMARY_WICK_MIN)
                    update_accum(exit_accum[(event.pattern, "trail", slip, hold, sl, None)], trail)
                    for tp in TP_GRID:
                        fixed = simulate_exit(event, bars_by_minute, event_ticks, "tp", sl, tp, hold, slip, PRIMARY_WICK_MIN)
                        hybrid = simulate_exit(event, bars_by_minute, event_ticks, "hybrid", sl, tp, hold, slip, PRIMARY_WICK_MIN)
                        update_accum(exit_accum[(event.pattern, "tp", slip, hold, sl, tp)], fixed)
                        update_accum(exit_accum[(event.pattern, "hybrid", slip, hold, sl, tp)], hybrid)

    grouped_mfe: dict[str, dict[str, Any]] = {}
    for pattern in ["single_impulse", "staircase"]:
        grouped_mfe[pattern] = mfe_distribution([r["mfe"] for r in event_mfe if r["pattern"] == pattern])

    exit_summary: dict[str, dict[str, Any]] = {}
    for key, acc in exit_accum.items():
        pattern, mode, slip, hold, sl, tp = key
        name = f"{pattern}|{mode}|slip={slip}|hold={hold}|sl={sl}|tp={tp}"
        exit_summary[name] = {
            "pattern": pattern,
            "mode": mode,
            "slippage": slip,
            "hold": hold,
            "sl": sl,
            "tp": tp,
            **finalize_accum(acc),
        }

    return {
        "pair": pair,
        "days": len({b.date for b in bars}),
        "bars": len(bars),
        "single_events": len(single),
        "staircase_events": len(staircase),
        "staircase_variant_counts": staircase_variant_counts(bars),
        "mfe_distribution": grouped_mfe,
        "events": event_mfe,
        "exit_summary": exit_summary,
        "case_candidates": case_candidates[:],
    }


def render_mfe_report(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Continuation MFE Distribution - 20.05.2026",
        "",
        f"Entry uses continuation direction with `{PRIMARY_SLIPPAGE:.2f}%` entry slippage. MFE horizon is `20m`; fee hurdle is `{FEE_PCT:.2f}%`.",
        "",
        "| pair | pattern | n | avg MFE | p50 | p75 | p90 | >=0.7 | >=1.0 | >=1.5 | avg time |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in results:
        if result.get("error"):
            continue
        for pattern, dist in result["mfe_distribution"].items():
            lines.append(
                f"| {result['pair']} | {pattern} | {dist['n']} | {fmt(dist['avg_mfe_pct'], '%')} | "
                f"{fmt(dist['p50_mfe_pct'], '%')} | {fmt(dist['p75_mfe_pct'], '%')} | {fmt(dist['p90_mfe_pct'], '%')} | "
                f"{fmt(dist['buckets'].get('0.7'), '%')} | {fmt(dist['buckets'].get('1.0'), '%')} | {fmt(dist['buckets'].get('1.5'), '%')} | "
                f"{fmt(dist['avg_time_to_mfe_sec'], 's')} |"
            )
    lines.extend(["", "## Conclusion", "", "Continuation is viable only where MFE clears the fee/slippage hurdle often enough. Prioritize pairs/patterns with high `>=0.7%` and `>=1.0%` rates; ignore high WR without MFE depth."])
    return "\n".join(lines) + "\n"


def top_exit_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        if result.get("error"):
            continue
        for payload in result["exit_summary"].values():
            if payload["n"] >= 20:
                rows.append({"pair": result["pair"], **payload})
    rows.sort(key=lambda r: safe_float(r["avg_net_pct"]), reverse=True)
    return rows


def render_exit_report(results: list[dict[str, Any]]) -> str:
    rows = top_exit_rows(results)
    lines = [
        "# Continuation Exit Grid - 20.05.2026",
        "",
        f"All rows include `{FEE_PCT:.2f}%` taker round trip and configured entry slippage.",
        "",
        "## Top 30 Net Rows",
        "",
        "| rank | pair | pattern | mode | slip | hold | SL | TP | n | avg net | med net | net win | TP | SL | trail | timeout | capture |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for idx, row in enumerate(rows[:30], start=1):
        lines.append(
            f"| {idx} | {row['pair']} | {row['pattern']} | {row['mode']} | {fmt(row['slippage'], '%')} | {row['hold']}m | "
            f"{fmt(row['sl'], '%')} | {fmt(row['tp'], '%') if row['tp'] is not None else 'n/a'} | {row['n']} | "
            f"**{fmt(row['avg_net_pct'], '%')}** | {fmt(row['median_net_pct'], '%')} | {fmt(row['win_net_rate'], '%')} | "
            f"{fmt(row['tp_rate'], '%')} | {fmt(row['sl_rate'], '%')} | {fmt(row['trail_rate'], '%')} | {fmt(row['timeout_rate'], '%')} | "
            f"{fmt(row['capture_ratio_avg'] * 100 if math.isfinite(safe_float(row['capture_ratio_avg'])) else None, '%')} |"
        )
    by_mode = defaultdict(list)
    for row in rows:
        if row["slippage"] == PRIMARY_SLIPPAGE:
            by_mode[row["mode"]].append(row)
    lines.extend(["", "## Best By Mode At 0.05% Slippage", "", "| mode | best pair | pattern | hold | SL | TP | avg net | n |", "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"])
    for mode in ["trail", "tp", "hybrid"]:
        best = max(by_mode.get(mode, []), key=lambda r: safe_float(r["avg_net_pct"]), default=None)
        if best:
            lines.append(
                f"| {mode} | {best['pair']} | {best['pattern']} | {best['hold']}m | {fmt(best['sl'], '%')} | "
                f"{fmt(best['tp'], '%') if best['tp'] is not None else 'n/a'} | {fmt(best['avg_net_pct'], '%')} | {best['n']} |"
            )
    positives = [r for r in rows if safe_float(r["avg_net_pct"]) > 0]
    lines.extend(["", "## Conclusion", "", f"Positive grid rows with n>=20: `{len(positives)}`.", "Paper candidates should come only from positive rows that survive slippage; if top rows are only low-sample/watch pairs, keep them out of `config.yaml`."])
    return "\n".join(lines) + "\n"


def render_patterns_report(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Continuation Patterns - 20.05.2026",
        "",
        "Patterns tested: single impulse (`abs 1m open->close >= 0.8%`) and staircase (`2 of 3`, cumulative `>=1.2%`, max opposite body `0.5%`, final close in directional 40%).",
        f"Net returns subtract `{FEE_PCT:.2f}%` taker round trip; exit grid reports include entry slippage.",
        "",
        "## Event Counts And Main MFE",
        "",
        "| pair | days | single n | staircase n | single avg MFE | staircase avg MFE | eligible note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for result in results:
        if result.get("error"):
            lines.append(f"| {result['pair']} | n/a | n/a | n/a | n/a | n/a | {result['error']} |")
            continue
        single = result["mfe_distribution"]["single_impulse"]
        stair = result["mfe_distribution"]["staircase"]
        eligible = "eligible" if result["days"] >= 3 and (result["single_events"] >= 20 or result["staircase_events"] >= 20) else "watch/excluded"
        lines.append(
            f"| {result['pair']} | {result['days']} | {result['single_events']} | {result['staircase_events']} | "
            f"{fmt(single['avg_mfe_pct'], '%')} | {fmt(stair['avg_mfe_pct'], '%')} | {eligible} |"
        )
    lines.extend(["", "## Staircase Variant Counts", "", "| pair | top variant | count |", "| --- | --- | ---: |"])
    for result in results:
        if result.get("error"):
            continue
        counts = result["staircase_variant_counts"]
        top = max(counts.items(), key=lambda kv: kv[1], default=("n/a", 0))
        lines.append(f"| {result['pair']} | {top[0]} | {top[1]} |")
    top_rows = top_exit_rows(results)
    candidates = []
    for row in top_rows:
        if row["slippage"] == PRIMARY_SLIPPAGE and safe_float(row["avg_net_pct"]) > 0 and row["n"] >= 20:
            candidates.append(row)
    pair_candidates = []
    seen = set()
    for row in candidates:
        if row["pair"] not in seen:
            seen.add(row["pair"])
            pair_candidates.append(row["pair"])
    lines.extend(["", "## Conclusion", "", f"Pairs with at least one positive net exit-grid row at `{PRIMARY_SLIPPAGE:.2f}%` slippage: `{', '.join(pair_candidates) or 'none'}`.", "Do not add pairs to `config.yaml` unless their positive rows are stable across slippage and not just one overfit exit setting."])
    return "\n".join(lines) + "\n"


def summarize_numeric(vals: list[float]) -> dict[str, Any]:
    clean = [v for v in vals if math.isfinite(v)]
    return {
        "n": len(clean),
        "avg": average(clean),
        "p50": median(clean) if clean else None,
        "ge_0p7": pct(sum(1 for v in clean if v >= 0.7), len(clean)),
        "ge_1p0": pct(sum(1 for v in clean if v >= 1.0), len(clean)),
        "net_horizon": average(clean) - FEE_PCT if clean else float("nan"),
    }


def render_hypotheses_report(results: list[dict[str, Any]]) -> str:
    events: list[dict[str, Any]] = []
    for result in results:
        if result.get("error"):
            continue
        events.extend(result["events"])

    def add_table(lines: list[str], title: str, groups: dict[str, list[float]]) -> None:
        lines.extend(["", f"## {title}", "", "| group | n | avg MFE | p50 MFE | >=0.7 | >=1.0 | MFE-fee |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
        for name, vals in groups.items():
            s = summarize_numeric(vals)
            lines.append(
                f"| {name} | {s['n']} | {fmt(s['avg'], '%')} | {fmt(s['p50'], '%')} | {fmt(s['ge_0p7'], '%')} | {fmt(s['ge_1p0'], '%')} | {fmt(s['net_horizon'], '%')} |"
            )

    lines = [
        "# Continuation Hypotheses - 20.05.2026",
        "",
        f"Uses saved continuation event MFE at `{PRIMARY_SLIPPAGE:.2f}%` entry slippage and `20m` horizon. `MFE-fee` is not executable PnL; it is a hurdle check against `{FEE_PCT:.2f}%` taker fees.",
    ]

    add_table(
        lines,
        "C1/C2: Repeated Explosions As Continuation Regime",
        {
            "0-1 prior explosions in 5m": [safe_float(e["mfe"]["mfe_pct"]) for e in events if e["repeated_5m"] < 2],
            "2+ prior explosions in 5m": [safe_float(e["mfe"]["mfe_pct"]) for e in events if e["repeated_5m"] >= 2],
        },
    )

    add_table(
        lines,
        "C3: Explosion Size",
        {
            "small <1.1%": [safe_float(e["mfe"]["mfe_pct"]) for e in events if abs(safe_float(e["signal_size_pct"])) < 1.1],
            "medium 1.1-1.5%": [safe_float(e["mfe"]["mfe_pct"]) for e in events if 1.1 <= abs(safe_float(e["signal_size_pct"])) < 1.5],
            "large >=1.5%": [safe_float(e["mfe"]["mfe_pct"]) for e in events if abs(safe_float(e["signal_size_pct"])) >= 1.5],
        },
    )

    single = [e for e in events if e["pattern"] == "single_impulse"]
    add_table(
        lines,
        "C4: Single Impulse Close Location",
        {
            "directional close ok": [safe_float(e["mfe"]["mfe_pct"]) for e in single if e.get("meta", {}).get("close_location_ok")],
            "rejection close": [safe_float(e["mfe"]["mfe_pct"]) for e in single if not e.get("meta", {}).get("close_location_ok")],
        },
    )

    add_table(
        lines,
        "C5: Staircase vs Single Spike",
        {
            "single impulse": [safe_float(e["mfe"]["mfe_pct"]) for e in events if e["pattern"] == "single_impulse"],
            "staircase": [safe_float(e["mfe"]["mfe_pct"]) for e in events if e["pattern"] == "staircase"],
        },
    )

    session_groups = {"Asia 00-06": [], "EU 07-15": [], "US 16-23": []}
    for e in events:
        hour = int(str(e["ts"])[11:13])
        session_groups[session_name(hour)].append(safe_float(e["mfe"]["mfe_pct"]))
    add_table(lines, "C6: Session Dependence", session_groups)

    btc_bars, _ = aggregate_pair("BTC-USDT-SWAP", keep_ticks=False)
    sol_bars, _ = aggregate_pair("SOL-USDT-SWAP", keep_ticks=False)
    btc = {b.minute_ms: b.change_pct for b in btc_bars}
    sol = {b.minute_ms: b.change_pct for b in sol_bars}
    for ctx_name, ctx in [("BTC", btc), ("SOL", sol)]:
        groups = {"isolated": [], "aligned": [], "opposite": []}
        for e in events:
            ctx_move = safe_float(ctx.get(int(e["minute_ms"])))
            pair_move = safe_float(e["signal_size_pct"])
            if not math.isfinite(ctx_move) or abs(ctx_move) < 0.3:
                group = "isolated"
            else:
                same = (pair_move > 0 and ctx_move > 0) or (pair_move < 0 and ctx_move < 0)
                group = "aligned" if same else "opposite"
            groups[group].append(safe_float(e["mfe"]["mfe_pct"]))
        add_table(lines, f"C7: Network Alignment ({ctx_name})", groups)

    lines.extend(
        [
            "",
            "## C8: Entry Delay",
            "",
            "Entry-delay replay was not re-run tick-by-tick in this pass. The actionable proxy is that MFE is large while executable exit grids are negative, so the next test should focus on pullback/confirmation entries rather than more exit tuning at signal close.",
            "",
            "## Conclusion",
            "",
            "Continuation has deeper MFE than reversal, but the tested signal-close entries and exit modes still fail after fee/slippage. The next research branch should test delayed/pullback entries, not immediate config changes.",
        ]
    )
    return "\n".join(lines) + "\n"


def candles_for_chart(bars: list[Bar], event: Event) -> list[Bar]:
    start = event.minute_ms - 10 * 60000
    end = event.minute_ms + 20 * 60000
    return [b for b in bars if start <= b.minute_ms <= end]


def plot_case(path: Path, bars: list[Bar], case: dict[str, Any]) -> None:
    event: Event = case["event"]
    exit_payload = case["exit"]
    subset = candles_for_chart(bars, event)
    if len(subset) < 5:
        return
    x = list(range(len(subset)))
    idx_by_minute = {b.minute_ms: i for i, b in enumerate(subset)}
    fig, ax = plt.subplots(figsize=(12, 6))
    for i, b in zip(x, subset):
        color = "#16875f" if b.close >= b.open else "#b33a3a"
        ax.vlines(i, b.low, b.high, color=color, linewidth=1)
        y = min(b.open, b.close)
        h = abs(b.close - b.open)
        ax.add_patch(plt.Rectangle((i - 0.35, y), 0.7, h if h > 0 else b.open * 0.0002, color=color, alpha=0.75))
    sig_idx = idx_by_minute.get(event.minute_ms)
    if sig_idx is not None:
        ax.axvspan(sig_idx - 0.5, sig_idx + 0.5, color="#f0c34e", alpha=0.25)
    ax.axhline(event.entry_price_raw, color="#225ea8", linestyle="--", linewidth=1, label="entry raw")
    for ts_ms, stop in exit_payload.get("stop_points", []):
        minute = ts_ms - ts_ms % 60000
        i = idx_by_minute.get(minute)
        if i is not None:
            ax.scatter(i, stop, color="#7b3294", s=12)
    exit_minute = exit_payload["exit_ts_ms"] - exit_payload["exit_ts_ms"] % 60000
    exit_idx = idx_by_minute.get(exit_minute)
    if exit_idx is not None:
        ax.axvline(exit_idx, color="#111111", linestyle=":", linewidth=1, label="exit")
    ax.set_title(
        f"{event.pair} {event.pattern} {event.direction} {event.ts} | MFE {case['mfe']['mfe_pct']:.2f}% net {exit_payload['net_pct']:.2f}%"
    )
    ax.set_xticks(x[::5])
    ax.set_xticklabels([subset[i].ts[11:16] for i in x[::5]], rotation=0)
    ax.grid(alpha=0.2)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_cases(results: list[dict[str, Any]]) -> None:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    selected: list[dict[str, Any]] = []
    for result in results:
        if result.get("error"):
            continue
        cases = result["case_candidates"]
        if not cases:
            continue
        high_mfe = sorted(cases, key=lambda c: safe_float(c["mfe"]["mfe_pct"]), reverse=True)[:1]
        win = sorted(cases, key=lambda c: safe_float(c["exit"]["net_pct"]), reverse=True)[:1]
        loss = sorted(cases, key=lambda c: safe_float(c["exit"]["net_pct"]))[:1]
        selected.extend(high_mfe + win + loss)
        if len(selected) >= 15:
            break
    selected = selected[:15]
    bars_cache: dict[str, list[Bar]] = {}
    for idx, case in enumerate(selected, start=1):
        event: Event = case["event"]
        if event.pair not in bars_cache:
            bars_cache[event.pair], _ = aggregate_pair(event.pair, keep_ticks=False)
        safe_ts = event.ts.replace(":", "").replace("-", "").replace("Z", "")
        path = CASE_DIR / f"case_{idx:02d}_{event.pair}_{event.pattern}_{safe_ts}.png"
        plot_case(path, bars_cache[event.pair], case)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for pair in PAIRS:
        if not (TICK_ROOT / pair).exists():
            results.append({"pair": pair, "error": "missing pair dir"})
            continue
        results.append(analyze_pair(pair))

    json_payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "fee_pct": FEE_PCT,
        "primary_slippage_pct": PRIMARY_SLIPPAGE,
        "pairs": [
            {k: v for k, v in r.items() if k != "case_candidates"}
            for r in results
        ],
    }
    PATTERNS_JSON.write_text(json.dumps(json_payload, ensure_ascii=True, indent=2), encoding="utf-8")
    PATTERNS_MD.write_text(render_patterns_report(results), encoding="utf-8")
    MFE_MD.write_text(render_mfe_report(results), encoding="utf-8")
    EXIT_MD.write_text(render_exit_report(results), encoding="utf-8")
    HYP_MD.write_text(render_hypotheses_report(results), encoding="utf-8")
    generate_cases(results)
    print(f"saved {PATTERNS_JSON}")
    print(f"saved {PATTERNS_MD}")
    print(f"saved {MFE_MD}")
    print(f"saved {EXIT_MD}")
    print(f"saved {HYP_MD}")
    print(f"saved cases to {CASE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
