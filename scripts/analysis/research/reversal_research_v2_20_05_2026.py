from __future__ import annotations

import csv
import gzip
import json
import math
import os
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
TICK_ROOT = Path(r"E:\trading-data\ticks")
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"

SUFFIX = "20_05_2026"
UNIVERSE_JSON = OUT_DIR / f"reversal_universe_v2_{SUFFIX}.json"
UNIVERSE_MD = OUT_DIR / f"reversal_universe_v2_{SUFFIX}.md"
NETWORK_MD = OUT_DIR / f"network_context_{SUFFIX}.md"
PARAM_MD = OUT_DIR / f"param_sweep_bill_{SUFFIX}.md"
HYPOTHESES_MD = OUT_DIR / f"hypotheses_{SUFFIX}.md"

EXPLOSION_THRESHOLD_PCT = 0.8
MIN_DAYS = 3
MIN_EXPLOSIONS = 10
FEE_ROUND_TRIP_PCT = 0.20
REVERSAL_HOLDS_MIN = [1, 2, 3, 5, 10]
PARAM_SL = [0.5, 0.8, 1.0, 1.2]
PARAM_TP = [0.7, 0.8, 1.0, 1.2, 1.5]
PARAM_HOLD = [5, 10, 15, 20]
PARAM_BE: list[float | None] = [None, 0.3, 0.5, 0.7]
PRIORITY_PAIRS = [
    "FOGO-USDT-SWAP",
    "HOME-USDT-SWAP",
    "ONT-USDT-SWAP",
    "USELESS-USDT-SWAP",
    "CHIP-USDT-SWAP",
    "LAYER-USDT-SWAP",
    "BOME-USDT-SWAP",
    "BSB-USDT-SWAP",
    "OFC-USDT-SWAP",
    "RLS-USDT-SWAP",
]


@dataclass(slots=True)
class Bar:
    minute_ms: int
    ts: str
    date: str
    hour_utc: int
    open: float
    high: float
    low: float
    close: float
    close_ts_ms: int
    volume: float
    price_change_pct: float


@dataclass(slots=True)
class Explosion:
    minute_ms: int
    ts: str
    date: str
    hour_utc: int
    direction: str
    size_pct: float


def safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if math.isfinite(out) else float("nan")


def iso_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def date_from_file(path: Path) -> str:
    name = path.name
    return name.removesuffix(".csv.gz").removesuffix(".csv")


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
    pos = (len(vals) - 1) * q / 100.0
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


def direction_return(entry: float, price: float, direction: str) -> float:
    if entry <= 0 or price <= 0:
        return float("nan")
    if direction == "long":
        return (price - entry) / entry * 100
    return (entry - price) / entry * 100


def aggregate_pair(pair_dir: Path, keep_ticks: bool = False) -> tuple[list[Bar], dict[int, list[tuple[int, float]]]]:
    all_bars: list[Bar] = []
    all_ticks: dict[int, list[tuple[int, float]]] = {} if keep_ticks else {}
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
                minute_ms = ts_ms - (ts_ms % 60000)
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
            bucket = minutes[minute_ms]
            open_p = bucket["open"]
            close_p = bucket["close"]
            change = (close_p - open_p) / open_p * 100 if open_p > 0 else float("nan")
            dt = datetime.fromtimestamp(minute_ms / 1000, tz=timezone.utc)
            all_bars.append(
                Bar(
                    minute_ms=minute_ms,
                    ts=iso_from_ms(minute_ms),
                    date=date,
                    hour_utc=dt.hour,
                    open=open_p,
                    high=bucket["high"],
                    low=bucket["low"],
                    close=close_p,
                    close_ts_ms=bucket["close_ts_ms"],
                    volume=bucket["volume"],
                    price_change_pct=change,
                )
            )
            if keep_ticks:
                all_ticks[minute_ms] = bucket["ticks"]
    all_bars.sort(key=lambda b: b.minute_ms)
    return all_bars, all_ticks


def detect_explosions(bars: list[Bar]) -> list[Explosion]:
    out: list[Explosion] = []
    for bar in bars:
        if not math.isfinite(bar.price_change_pct) or abs(bar.price_change_pct) < EXPLOSION_THRESHOLD_PCT:
            continue
        out.append(
            Explosion(
                minute_ms=bar.minute_ms,
                ts=bar.ts,
                date=bar.date,
                hour_utc=bar.hour_utc,
                direction="long" if bar.price_change_pct > 0 else "short",
                size_pct=bar.price_change_pct,
            )
        )
    return out


def summarize_returns(values: list[float]) -> dict[str, Any]:
    vals = [v for v in values if math.isfinite(v)]
    wins = sum(1 for v in vals if v > 0)
    avg = average(vals)
    return {
        "n": len(vals),
        "win_rate": pct(wins, len(vals)),
        "avg_return_pct": avg,
        "net_return_pct": avg - FEE_ROUND_TRIP_PCT if math.isfinite(avg) else float("nan"),
        "net_positive": bool(math.isfinite(avg) and avg > FEE_ROUND_TRIP_PCT),
    }


def event_rows(bars: list[Bar], explosions: list[Explosion]) -> list[dict[str, Any]]:
    by_minute = {b.minute_ms: b for b in bars}
    rows: list[dict[str, Any]] = []
    for exp in explosions:
        exp_bar = by_minute.get(exp.minute_ms)
        if exp_bar is None:
            continue
        reversal_direction = "short" if exp.direction == "long" else "long"
        row: dict[str, Any] = {
            "minute_ms": exp.minute_ms,
            "explosion_ts": exp.ts,
            "date": exp.date,
            "hour_utc": exp.hour_utc,
            "explosion_direction": exp.direction,
            "reversal_direction": reversal_direction,
            "explosion_size_pct": exp.size_pct,
            "explosion_abs_pct": abs(exp.size_pct),
            "entry_close_price": exp_bar.close,
            "close_returns": {},
            "delayed_returns": {},
        }
        for hold in REVERSAL_HOLDS_MIN:
            target = by_minute.get(exp.minute_ms + hold * 60000)
            row["close_returns"][str(hold)] = (
                direction_return(exp_bar.close, target.close, reversal_direction) if target else None
            )
        delayed_entry = by_minute.get(exp.minute_ms + 60000)
        if delayed_entry:
            row["delayed_entry_ts"] = delayed_entry.ts
            row["delayed_entry_price"] = delayed_entry.close
            for hold in REVERSAL_HOLDS_MIN:
                target = by_minute.get(delayed_entry.minute_ms + hold * 60000)
                row["delayed_returns"][str(hold)] = (
                    direction_return(delayed_entry.close, target.close, reversal_direction) if target else None
                )
        rows.append(row)
    return rows


def opposite_delay_stats(explosions: list[Explosion]) -> dict[str, Any]:
    delays: list[int] = []
    for i, exp in enumerate(explosions):
        for nxt in explosions[i + 1:]:
            if nxt.direction != exp.direction:
                delays.append(int((nxt.minute_ms - exp.minute_ms) / 60000))
                break
    return {
        "n": len(delays),
        "median_min": median(delays) if delays else None,
        "avg_min": average(float(v) for v in delays),
    }


def verdict(rev3: dict[str, Any]) -> str:
    wr = safe_float(rev3.get("win_rate"))
    avg_ret = safe_float(rev3.get("avg_return_pct"))
    net = safe_float(rev3.get("net_return_pct"))
    if not math.isfinite(wr) or not math.isfinite(avg_ret):
        return "noise"
    if net > 0 and wr > 52:
        return "net_positive_reversal"
    if wr > 55 and avg_ret > 0.1:
        return "gross_reversal_fee_blocked"
    if wr > 50 and avg_ret > 0:
        return "weak_reversal_fee_blocked"
    if wr < 45:
        return "continuation"
    return "noise"


def analyze_pair(pair_dir: Path) -> dict[str, Any]:
    bars, _ = aggregate_pair(pair_dir, keep_ticks=False)
    files = tick_files(pair_dir)
    dates = sorted({b.date for b in bars})
    explosions = detect_explosions(bars)
    rows = event_rows(bars, explosions)
    delayed = {
        str(hold): summarize_returns(
            [
                safe_float(row["delayed_returns"].get(str(hold)))
                for row in rows
                if row["delayed_returns"].get(str(hold)) is not None
            ]
        )
        for hold in REVERSAL_HOLDS_MIN
    }
    close = {
        str(hold): summarize_returns(
            [
                safe_float(row["close_returns"].get(str(hold)))
                for row in rows
                if row["close_returns"].get(str(hold)) is not None
            ]
        )
        for hold in REVERSAL_HOLDS_MIN
    }
    best_hold = max(
        ((hold, payload) for hold, payload in delayed.items() if payload["n"] > 0),
        key=lambda item: safe_float(item[1]["net_return_pct"]),
        default=(None, None),
    )
    long_count = sum(1 for exp in explosions if exp.direction == "long")
    daily_counts = Counter(exp.date for exp in explosions)
    hour_counts = Counter(exp.hour_utc for exp in explosions)
    sizes = [abs(exp.size_pct) for exp in explosions]
    eligible = len(dates) >= MIN_DAYS and len(explosions) >= MIN_EXPLOSIONS
    return {
        "pair": pair_dir.name,
        "days": len(dates),
        "files": [p.name for p in files],
        "bar_count": len(bars),
        "explosion_count": len(explosions),
        "explosive_per_day": len(explosions) / len(dates) if dates else 0.0,
        "long_count": long_count,
        "short_count": len(explosions) - long_count,
        "long_pct": pct(long_count, len(explosions)),
        "short_pct": pct(len(explosions) - long_count, len(explosions)),
        "daily_explosions": dict(sorted(daily_counts.items())),
        "hour_counts": dict(sorted((str(k), v) for k, v in hour_counts.items())),
        "oscillation": opposite_delay_stats(explosions),
        "explosion_size": {
            "avg_abs_pct": average(sizes),
            "p50_abs_pct": median(sizes) if sizes else None,
            "p75_abs_pct": percentile(sizes, 75),
            "p90_abs_pct": percentile(sizes, 90),
        },
        "reversal_delayed_entry": delayed,
        "reversal_close_entry": close,
        "best_hold": int(best_hold[0]) if best_hold[0] is not None else None,
        "best_hold_net_return_pct": best_hold[1]["net_return_pct"] if best_hold[1] else None,
        "verdict": verdict(delayed["3"]),
        "eligible": eligible,
        "sample_note": "preliminary" if len(rows) < 30 else "usable",
        "events": rows,
    }


def scan_universe() -> list[dict[str, Any]]:
    pair_dirs = sorted([p for p in TICK_ROOT.iterdir() if p.is_dir()], key=lambda p: p.name)
    workers = min(12, max(4, (os.cpu_count() or 4)))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(analyze_pair, pair_dir): pair_dir.name for pair_dir in pair_dirs}
        for future in as_completed(futures):
            pair = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"pair": pair, "error": repr(exc), "eligible": False}
            print(f"scanned {pair}")
            results.append(result)
    return sorted(results, key=lambda r: r["pair"])


def render_universe_report(results: list[dict[str, Any]], old_data: dict[str, Any] | None) -> str:
    eligible = [r for r in results if r.get("eligible")]
    excluded = [r for r in results if not r.get("eligible")]
    ranked = sorted(eligible, key=lambda r: safe_float(r.get("explosive_per_day")), reverse=True)
    net_positive = [
        r
        for r in eligible
        if any((r["reversal_delayed_entry"][str(h)]["net_positive"] for h in REVERSAL_HOLDS_MIN))
    ]
    old_by_pair = {r["pair"]: r for r in (old_data or {}).get("pairs", []) if isinstance(r, dict)}

    lines = [
        "# Reversal Universe V2 - 20.05.2026",
        "",
        f"Source: `{TICK_ROOT}`. Parallel scan with `ThreadPoolExecutor`; threshold `abs(1m open->close) >= {EXPLOSION_THRESHOLD_PCT:.1f}%`.",
        f"Eligibility: `days >= {MIN_DAYS}` and `explosions >= {MIN_EXPLOSIONS}`. Fee model: `{FEE_ROUND_TRIP_PCT:.2f}%` round trip.",
        "",
        "Universe metrics below use the original research delayed-entry method for comparability: enter against the explosion one full 1m bar after the explosive candle, then measure forward close-to-close return.",
        "",
        f"- scanned pairs: `{len(results)}`",
        f"- eligible pairs: `{len(eligible)}`",
        f"- net-positive eligible pairs on at least one hold: `{len(net_positive)}`",
        f"- excluded pairs: `{len(excluded)}`",
        "",
        "## Eligible Universe",
        "",
        "| pair | days | explosions | exp/day | old_exp | rev3_WR | rev3_avg | rev3_net | best_hold | best_net | verdict |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in ranked:
        old = old_by_pair.get(row["pair"], {})
        old_exp = old.get("explosion_count", "new")
        rev3 = row["reversal_delayed_entry"]["3"]
        net_cell = f"**{fmt(rev3['net_return_pct'], '%')}**" if rev3["net_positive"] else fmt(rev3["net_return_pct"], "%")
        best_net = safe_float(row.get("best_hold_net_return_pct"))
        best_cell = f"**{fmt(best_net, '%')}**" if math.isfinite(best_net) and best_net > 0 else fmt(best_net, "%")
        lines.append(
            f"| {row['pair']} | {row['days']} | {row['explosion_count']} | {fmt(row['explosive_per_day'])} | "
            f"{old_exp} | {fmt(rev3['win_rate'], '%')} | {fmt(rev3['avg_return_pct'], '%')} | {net_cell} | "
            f"{row['best_hold']}m | {best_cell} | {row['verdict']} |"
        )

    lines.extend(["", "## Net Edge By Hold", ""])
    for row in sorted(eligible, key=lambda r: safe_float(r["best_hold_net_return_pct"]), reverse=True):
        lines.extend(
            [
                f"### {row['pair']}",
                "",
                f"- days: `{row['days']}`, explosions: `{row['explosion_count']}`, exp/day: `{fmt(row['explosive_per_day'])}`",
                f"- explosion size avg/p75/p90: `{fmt(row['explosion_size']['avg_abs_pct'], '%')}` / `{fmt(row['explosion_size']['p75_abs_pct'], '%')}` / `{fmt(row['explosion_size']['p90_abs_pct'], '%')}`",
                "",
                "| hold | n | WR | avg_return | net_return |",
                "| ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for hold in REVERSAL_HOLDS_MIN:
            payload = row["reversal_delayed_entry"][str(hold)]
            net = payload["net_return_pct"]
            net_txt = f"**{fmt(net, '%')}**" if payload["net_positive"] else fmt(net, "%")
            lines.append(
                f"| {hold}m | {payload['n']} | {fmt(payload['win_rate'], '%')} | {fmt(payload['avg_return_pct'], '%')} | {net_txt} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Priority Pair Check",
            "",
            "| pair | status | days | explosions | rev3_net | best_hold | best_net | comment |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    by_pair = {r["pair"]: r for r in results}
    for pair in PRIORITY_PAIRS:
        row = by_pair.get(pair)
        if row is None:
            lines.append(f"| {pair} | missing | n/a | n/a | n/a | n/a | n/a | not present in tape root |")
            continue
        if row.get("error"):
            lines.append(f"| {pair} | error | n/a | n/a | n/a | n/a | n/a | {row['error']} |")
            continue
        rev3 = row["reversal_delayed_entry"]["3"]
        best_hold = f"{row['best_hold']}m" if row.get("best_hold") is not None else "n/a"
        comment = "eligible" if row["eligible"] else "not eligible"
        if row["days"] < MIN_DAYS:
            comment += f"; days<{MIN_DAYS}"
        if row["explosion_count"] < MIN_EXPLOSIONS:
            comment += f"; explosions<{MIN_EXPLOSIONS}"
        lines.append(
            f"| {pair} | {'eligible' if row['eligible'] else 'excluded'} | {row['days']} | {row['explosion_count']} | "
            f"{fmt(rev3['net_return_pct'], '%')} | {best_hold} | {fmt(row['best_hold_net_return_pct'], '%')} | {comment} |"
        )

    lines.extend(
        [
            "",
            "## Excluded Pairs",
            "",
            "| pair | days | explosions | reason |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for row in sorted(excluded, key=lambda r: (-safe_float(r.get("days")), -safe_float(r.get("explosion_count")), r["pair"])):
        if row.get("error"):
            reason = row["error"]
            days = "n/a"
            exps = "n/a"
        else:
            reasons = []
            if row["days"] < MIN_DAYS:
                reasons.append(f"days<{MIN_DAYS}")
            if row["explosion_count"] < MIN_EXPLOSIONS:
                reasons.append(f"explosions<{MIN_EXPLOSIONS}")
            reason = ", ".join(reasons)
            days = row["days"]
            exps = row["explosion_count"]
        lines.append(f"| {row['pair']} | {days} | {exps} | {reason} |")

    recommended = [
        r
        for r in eligible
        if safe_float(r["best_hold_net_return_pct"]) > 0 and r["sample_note"] == "usable"
    ]
    watch = [
        r
        for r in eligible
        if safe_float(r["best_hold_net_return_pct"]) > 0 and r["sample_note"] != "usable"
    ]
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            f"Do not expand `eligible_pairs` just because gross WR is above 50%; after the `0.20%` fee most gross edges disappear.",
            f"Config candidates with usable sample and positive best net: `{', '.join(r['pair'] for r in recommended) or 'none'}`.",
            f"Watch-only positive-net preliminary candidates: `{', '.join(r['pair'] for r in watch) or 'none'}`.",
            "Pairs with negative best net should stay out of `config.yaml` until a parameter/filter test shows positive net after fees.",
        ]
    )
    return "\n".join(lines) + "\n"


def ticks_for_window(
    ticks_by_minute: dict[int, list[tuple[int, float]]],
    start_ms: int,
    end_ms: int,
) -> list[tuple[int, float]]:
    start_minute = start_ms - (start_ms % 60000)
    out: list[tuple[int, float]] = []
    minute = start_minute
    while minute <= end_ms:
        for ts_ms, price in ticks_by_minute.get(minute, []):
            if start_ms < ts_ms <= end_ms:
                out.append((ts_ms, price))
        minute += 60000
    return out


def simulate_trace(
    trace: list[tuple[int, float]],
    sl_pct: float,
    tp_pct: float,
    max_hold_min: int,
    be_trigger_pct: float | None,
) -> dict[str, Any]:
    stop_active_pct = -sl_pct
    be_armed = False
    best_mfe = 0.0
    worst_mae = 0.0
    last_ret = 0.0
    exit_elapsed_ms = max_hold_min * 60000
    exit_return = 0.0
    outcome = "timeout"
    end_elapsed_ms = max_hold_min * 60000
    for elapsed_ms, ret in trace:
        if elapsed_ms > end_elapsed_ms:
            break
        last_ret = ret
        best_mfe = max(best_mfe, ret)
        worst_mae = min(worst_mae, ret)
        if ret >= tp_pct:
            outcome = "tp"
            exit_return = tp_pct
            exit_elapsed_ms = elapsed_ms
            break
        if be_trigger_pct is not None and not be_armed and ret >= be_trigger_pct:
            be_armed = True
            stop_active_pct = 0.0
        if ret <= stop_active_pct:
            outcome = "be" if be_armed and stop_active_pct == 0.0 else "sl"
            exit_return = stop_active_pct
            exit_elapsed_ms = elapsed_ms
            break
    else:
        exit_return = last_ret

    return {
        "outcome": outcome,
        "gross_return_pct": exit_return,
        "net_return_pct": exit_return - FEE_ROUND_TRIP_PCT,
        "mfe_pct": best_mfe,
        "mae_pct": worst_mae,
        "exit_elapsed_ms": exit_elapsed_ms,
        "hold_sec": exit_elapsed_ms / 1000.0,
    }


def bill_events_for_engine(bars: list[Bar], ticks_by_minute: dict[int, list[tuple[int, float]]] | None = None) -> list[dict[str, Any]]:
    rows = []
    by_minute = {b.minute_ms: b for b in bars}
    for exp in detect_explosions(bars):
        bar = by_minute.get(exp.minute_ms)
        if not bar:
            continue
        reversal_direction = "short" if exp.direction == "long" else "long"
        row = {
            "minute_ms": exp.minute_ms,
            "ts": exp.ts,
            "date": exp.date,
            "hour_utc": exp.hour_utc,
            "explosion_direction": exp.direction,
            "reversal_direction": reversal_direction,
            "explosion_size_pct": exp.size_pct,
            "entry_price": bar.close,
            "entry_ts_ms": bar.close_ts_ms,
        }
        if ticks_by_minute is not None:
            raw_ticks = ticks_for_window(ticks_by_minute, bar.close_ts_ms, bar.close_ts_ms + max(PARAM_HOLD) * 60000)
            row["trace"] = [
                (ts_ms - bar.close_ts_ms, direction_return(bar.close, price, reversal_direction))
                for ts_ms, price in raw_ticks
            ]
        rows.append(row)
    return rows


def param_sweep_bill(bars: list[Bar], ticks_by_minute: dict[int, list[tuple[int, float]]]) -> dict[str, Any]:
    events = bill_events_for_engine(bars, ticks_by_minute)
    combos: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for sl in PARAM_SL:
        for tp in PARAM_TP:
            for hold in PARAM_HOLD:
                for be in PARAM_BE:
                    sims = [
                        simulate_trace(
                            e["trace"],
                            sl,
                            tp,
                            hold,
                            be,
                        )
                        for e in events
                    ]
                    n = len(sims)
                    outcomes = Counter(s["outcome"] for s in sims)
                    tp_rate = outcomes["tp"] / n if n else 0.0
                    sl_rate = outcomes["sl"] / n if n else 0.0
                    be_rate = outcomes["be"] / n if n else 0.0
                    timeout_rate = outcomes["timeout"] / n if n else 0.0
                    formula_net = tp_rate * tp - sl_rate * sl - FEE_ROUND_TRIP_PCT
                    realized_net = average([safe_float(s["gross_return_pct"]) for s in sims]) - FEE_ROUND_TRIP_PCT
                    row = {
                        "sl_pct": sl,
                        "tp_pct": tp,
                        "max_hold_min": hold,
                        "be_trigger_pct": be,
                        "n": n,
                        "tp_rate": tp_rate * 100,
                        "sl_rate": sl_rate * 100,
                        "be_rate": be_rate * 100,
                        "timeout_rate": timeout_rate * 100,
                        "avg_mfe_pct": average([safe_float(s["mfe_pct"]) for s in sims]),
                        "avg_mae_pct": average([safe_float(s["mae_pct"]) for s in sims]),
                        "avg_gross_return_pct": average([safe_float(s["gross_return_pct"]) for s in sims]),
                        "net_pnl_pct": formula_net,
                        "realized_net_return_pct": realized_net,
                    }
                    combos.append(row)
                    if sl == 0.8 and tp == 1.5 and hold == 15 and be == 0.5:
                        current = row
    combos.sort(key=lambda r: (safe_float(r["net_pnl_pct"]), safe_float(r["realized_net_return_pct"])), reverse=True)
    return {"events_n": len(events), "top": combos[:10], "current": current, "all": combos}


def combo_defs() -> list[tuple[float, float, int, float | None]]:
    return [(sl, tp, hold, be) for sl in PARAM_SL for tp in PARAM_TP for hold in PARAM_HOLD for be in PARAM_BE]


def summarize_trace(trace: list[tuple[int, float]]) -> dict[str, Any]:
    ge_thresholds = sorted(set(PARAM_TP + [v for v in PARAM_BE if v is not None]))
    le_thresholds = sorted([-v for v in PARAM_SL])
    first_ge: dict[float, int | None] = {thr: None for thr in ge_thresholds}
    first_le: dict[float, int | None] = {thr: None for thr in le_thresholds}
    be_zero_after: dict[float, int | None] = {thr: None for thr in PARAM_BE if thr is not None}
    first_ge_idx: dict[float, int | None] = {thr: None for thr in ge_thresholds}
    max_by_hold: dict[int, float] = {}
    min_by_hold: dict[int, float] = {}
    last_by_hold: dict[int, float] = {}
    max_ret = 0.0
    min_ret = 0.0
    last_ret = 0.0
    hold_idx = 0
    holds = sorted(PARAM_HOLD)

    for idx, (elapsed_ms, ret) in enumerate(trace):
        while hold_idx < len(holds) and elapsed_ms > holds[hold_idx] * 60000:
            hold = holds[hold_idx]
            max_by_hold[hold] = max_ret
            min_by_hold[hold] = min_ret
            last_by_hold[hold] = last_ret
            hold_idx += 1
        if hold_idx >= len(holds):
            break
        last_ret = ret
        max_ret = max(max_ret, ret)
        min_ret = min(min_ret, ret)
        for thr in ge_thresholds:
            if first_ge[thr] is None and ret >= thr:
                first_ge[thr] = elapsed_ms
                first_ge_idx[thr] = idx
        for thr in le_thresholds:
            if first_le[thr] is None and ret <= thr:
                first_le[thr] = elapsed_ms
        for be_thr in be_zero_after:
            ge_idx = first_ge_idx.get(be_thr)
            if ge_idx is not None and idx > ge_idx and be_zero_after[be_thr] is None and ret <= 0.0:
                be_zero_after[be_thr] = elapsed_ms

    while hold_idx < len(holds):
        hold = holds[hold_idx]
        max_by_hold[hold] = max_ret
        min_by_hold[hold] = min_ret
        last_by_hold[hold] = last_ret
        hold_idx += 1

    return {
        "first_ge": first_ge,
        "first_le": first_le,
        "be_zero_after": be_zero_after,
        "max_by_hold": max_by_hold,
        "min_by_hold": min_by_hold,
        "last_by_hold": last_by_hold,
    }


def time_before_hold(value: int | None, hold: int) -> int | None:
    if value is None:
        return None
    return value if value <= hold * 60000 else None


def outcome_from_summary(summary: dict[str, Any], sl: float, tp: float, hold: int, be: float | None) -> dict[str, Any]:
    tp_time = time_before_hold(summary["first_ge"].get(tp), hold)
    sl_time = time_before_hold(summary["first_le"].get(-sl), hold)
    exit_return = summary["last_by_hold"][hold]
    outcome = "timeout"

    if be is None:
        if tp_time is not None and (sl_time is None or tp_time <= sl_time):
            outcome = "tp"
            exit_return = tp
        elif sl_time is not None:
            outcome = "sl"
            exit_return = -sl
    else:
        be_time = time_before_hold(summary["first_ge"].get(be), hold)
        if sl_time is not None and (be_time is None or sl_time <= be_time) and (tp_time is None or sl_time <= tp_time):
            outcome = "sl"
            exit_return = -sl
        elif tp_time is not None and (be_time is None or tp_time <= be_time):
            outcome = "tp"
            exit_return = tp
        elif be_time is not None:
            zero_time = time_before_hold(summary["be_zero_after"].get(be), hold)
            if tp_time is not None and (zero_time is None or tp_time <= zero_time):
                outcome = "tp"
                exit_return = tp
            elif zero_time is not None:
                outcome = "be"
                exit_return = 0.0

    return {
        "outcome": outcome,
        "gross_return_pct": exit_return,
        "mfe_pct": summary["max_by_hold"][hold],
        "mae_pct": summary["min_by_hold"][hold],
    }


def stream_bill_event_summaries(bars: list[Bar]) -> Iterable[dict[str, Any]]:
    events = bill_events_for_engine(bars)
    events.sort(key=lambda e: e["entry_ts_ms"])
    next_idx = 0
    active: list[dict[str, Any]] = []
    max_hold_ms = max(PARAM_HOLD) * 60000
    for path in tick_files(TICK_ROOT / "BILL-USDT-SWAP"):
        with open_tick_file(path) as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                side = (row.get("side") or "").lower()
                if side == "gap" or side not in {"buy", "sell"}:
                    continue
                ts_ms = int(float(row["ts_ms"]))
                price = safe_float(row.get("price"))
                if not math.isfinite(price):
                    continue
                while next_idx < len(events) and events[next_idx]["entry_ts_ms"] < ts_ms:
                    event = events[next_idx]
                    event["end_ts_ms"] = event["entry_ts_ms"] + max_hold_ms
                    event["trace"] = []
                    active.append(event)
                    next_idx += 1
                still_active: list[dict[str, Any]] = []
                for event in active:
                    if ts_ms > event["end_ts_ms"]:
                        yield summarize_trace(event["trace"])
                    else:
                        event["trace"].append(
                            (
                                ts_ms - event["entry_ts_ms"],
                                direction_return(event["entry_price"], price, event["reversal_direction"]),
                            )
                        )
                        still_active.append(event)
                active = still_active
    for event in active:
        yield summarize_trace(event["trace"])
    for event in events[next_idx:]:
        yield summarize_trace([])


def param_sweep_bill_streaming(bars: list[Bar]) -> dict[str, Any]:
    combos = combo_defs()
    accum = {
        combo: {
            "n": 0,
            "outcomes": Counter(),
            "gross_sum": 0.0,
            "mfe_sum": 0.0,
            "mae_sum": 0.0,
            "current_mfe": [],
        }
        for combo in combos
    }
    current_combo = (0.8, 1.5, 15, 0.5)
    for idx, summary in enumerate(stream_bill_event_summaries(bars), start=1):
        if idx % 100 == 0:
            print(f"BILL sweep processed {idx} events")
        for combo in combos:
            sl, tp, hold, be = combo
            sim = outcome_from_summary(summary, sl, tp, hold, be)
            item = accum[combo]
            item["n"] += 1
            item["outcomes"][sim["outcome"]] += 1
            item["gross_sum"] += safe_float(sim["gross_return_pct"])
            item["mfe_sum"] += safe_float(sim["mfe_pct"])
            item["mae_sum"] += safe_float(sim["mae_pct"])
            if combo == current_combo:
                item["current_mfe"].append(safe_float(sim["mfe_pct"]))

    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_mfe_vals: list[float] = []
    for combo, item in accum.items():
        sl, tp, hold, be = combo
        n = item["n"]
        outcomes = item["outcomes"]
        tp_rate = outcomes["tp"] / n if n else 0.0
        sl_rate = outcomes["sl"] / n if n else 0.0
        row = {
            "sl_pct": sl,
            "tp_pct": tp,
            "max_hold_min": hold,
            "be_trigger_pct": be,
            "n": n,
            "tp_rate": tp_rate * 100,
            "sl_rate": sl_rate * 100,
            "be_rate": (outcomes["be"] / n * 100) if n else 0.0,
            "timeout_rate": (outcomes["timeout"] / n * 100) if n else 0.0,
            "avg_mfe_pct": item["mfe_sum"] / n if n else float("nan"),
            "avg_mae_pct": item["mae_sum"] / n if n else float("nan"),
            "avg_gross_return_pct": item["gross_sum"] / n if n else float("nan"),
            "net_pnl_pct": tp_rate * tp - sl_rate * sl - FEE_ROUND_TRIP_PCT,
            "realized_net_return_pct": (item["gross_sum"] / n - FEE_ROUND_TRIP_PCT) if n else float("nan"),
        }
        rows.append(row)
        if combo == current_combo:
            current = row
            current_mfe_vals = item["current_mfe"]
            current_outcomes = dict(item["outcomes"])
    rows.sort(key=lambda r: (safe_float(r["net_pnl_pct"]), safe_float(r["realized_net_return_pct"])), reverse=True)
    return {
        "events_n": current["n"] if current else len(bill_events_for_engine(bars)),
        "top": rows[:10],
        "current": current,
        "all": rows,
        "current_outcomes": current_outcomes if current else {},
        "current_mfe_stats": {
            "avg": average(current_mfe_vals),
            "p50": median(current_mfe_vals) if current_mfe_vals else None,
            "p75": percentile(current_mfe_vals, 75),
            "p90": percentile(current_mfe_vals, 90),
        },
    }


def render_param_report(sweep: dict[str, Any]) -> str:
    lines = [
        "# BILL Parameter Sweep - 20.05.2026",
        "",
        "Method: tick-level replay on `BILL-USDT-SWAP`; entry is the close tick of the explosive 1m candle; trade direction is against the explosion.",
        f"Fee is always `{FEE_ROUND_TRIP_PCT:.2f}%` round trip. `net_pnl` follows the requested formula: `tp_rate * tp_pct - sl_rate * sl_pct - fee`; timeout mark-to-market is shown separately as `realized_net`.",
        "",
        f"- events: `{sweep['events_n']}`",
        "",
        "## Top 10 By net_pnl",
        "",
        "| rank | SL | TP | hold | BE | TP% | SL% | BE% | timeout% | avg_MFE | net_pnl | realized_net |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for idx, row in enumerate(sweep["top"], start=1):
        be = "off" if row["be_trigger_pct"] is None else fmt(row["be_trigger_pct"], "%")
        lines.append(
            f"| {idx} | {fmt(row['sl_pct'], '%')} | {fmt(row['tp_pct'], '%')} | {row['max_hold_min']}m | {be} | "
            f"{fmt(row['tp_rate'], '%')} | {fmt(row['sl_rate'], '%')} | {fmt(row['be_rate'], '%')} | {fmt(row['timeout_rate'], '%')} | "
            f"{fmt(row['avg_mfe_pct'], '%')} | **{fmt(row['net_pnl_pct'], '%')}** | {fmt(row['realized_net_return_pct'], '%')} |"
        )

    cur = sweep.get("current")
    lines.extend(["", "## Current Params", ""])
    if cur:
        be = "off" if cur["be_trigger_pct"] is None else fmt(cur["be_trigger_pct"], "%")
        lines.extend(
            [
                "| SL | TP | hold | BE | TP% | SL% | BE% | timeout% | avg_MFE | net_pnl | realized_net |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
                f"| {fmt(cur['sl_pct'], '%')} | {fmt(cur['tp_pct'], '%')} | {cur['max_hold_min']}m | {be} | "
                f"{fmt(cur['tp_rate'], '%')} | {fmt(cur['sl_rate'], '%')} | {fmt(cur['be_rate'], '%')} | {fmt(cur['timeout_rate'], '%')} | "
                f"{fmt(cur['avg_mfe_pct'], '%')} | **{fmt(cur['net_pnl_pct'], '%')}** | {fmt(cur['realized_net_return_pct'], '%')} |",
            ]
        )
    positives = [r for r in sweep["all"] if safe_float(r["net_pnl_pct"]) > 0]
    pos_realized = [r for r in sweep["all"] if safe_float(r["realized_net_return_pct"]) > 0]
    best = sweep["top"][0] if sweep["top"] else None
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            f"Positive requested-formula combinations: `{len(positives)}` / `{len(sweep['all'])}`.",
            f"Positive realized-net combinations including timeout mark-to-market: `{len(pos_realized)}` / `{len(sweep['all'])}`.",
        ]
    )
    if best:
        best_be = "off" if best["be_trigger_pct"] is None else fmt(best["be_trigger_pct"], "%")
        lines.append(
            f"Best formula result is SL `{fmt(best['sl_pct'], '%')}`, TP `{fmt(best['tp_pct'], '%')}`, hold `{best['max_hold_min']}m`, BE `{best_be}` with net_pnl `{fmt(best['net_pnl_pct'], '%')}`."
        )
    if cur:
        lines.append(
            f"Current BILL params are {'positive' if safe_float(cur['net_pnl_pct']) > 0 else 'not positive'} by the requested formula after fees."
        )
    lines.append("Do not change engine parameters in production from this file alone; use these results as the next paper config candidate set.")
    return "\n".join(lines) + "\n"


def summarize_group(rows: list[float]) -> dict[str, Any]:
    vals = [v for v in rows if math.isfinite(v)]
    return {
        "n": len(vals),
        "wr": pct(sum(1 for v in vals if v > 0), len(vals)),
        "avg": average(vals),
        "net": average(vals) - FEE_ROUND_TRIP_PCT if vals else float("nan"),
    }


def quantile_bucket(value: float, cuts: list[float]) -> str:
    if value <= cuts[0]:
        return "Q1 small"
    if value <= cuts[1]:
        return "Q2"
    if value <= cuts[2]:
        return "Q3"
    return "Q4 large"


def render_hypotheses(
    universe: list[dict[str, Any]],
    bill_bars: list[Bar],
    sweep: dict[str, Any],
) -> str:
    bill = next(r for r in universe if r["pair"] == "BILL-USDT-SWAP")
    events = bill["events"]
    close3 = [safe_float(e["close_returns"].get("3")) for e in events if e["close_returns"].get("3") is not None]
    delayed3 = [safe_float(e["delayed_returns"].get("3")) for e in events if e["delayed_returns"].get("3") is not None]
    delayed10 = [safe_float(e["delayed_returns"].get("10")) for e in events if e["delayed_returns"].get("10") is not None]
    s_close3 = summarize_group(close3)
    s_delayed3 = summarize_group(delayed3)
    s_delayed10 = summarize_group(delayed10)

    no_be = [r for r in sweep["all"] if r["be_trigger_pct"] is None]
    be03 = [r for r in sweep["all"] if r["be_trigger_pct"] == 0.3]
    be05 = [r for r in sweep["all"] if r["be_trigger_pct"] == 0.5]
    be07 = [r for r in sweep["all"] if r["be_trigger_pct"] == 0.7]
    by_be = []
    for name, rows in [("off", no_be), ("0.3%", be03), ("0.5%", be05), ("0.7%", be07)]:
        by_be.append(
            {
                "name": name,
                "best_formula": max((safe_float(r["net_pnl_pct"]) for r in rows), default=float("nan")),
                "avg_formula": average([safe_float(r["net_pnl_pct"]) for r in rows]),
                "best_realized": max((safe_float(r["realized_net_return_pct"]) for r in rows), default=float("nan")),
            }
        )

    by_tp = []
    for tp in PARAM_TP:
        rows = [r for r in sweep["all"] if r["tp_pct"] == tp]
        by_tp.append(
            {
                "tp": tp,
                "best_formula": max((safe_float(r["net_pnl_pct"]) for r in rows), default=float("nan")),
                "avg_formula": average([safe_float(r["net_pnl_pct"]) for r in rows]),
                "best_realized": max((safe_float(r["realized_net_return_pct"]) for r in rows), default=float("nan")),
            }
        )

    sizes = [safe_float(e["explosion_abs_pct"]) for e in events]
    cuts = [percentile(sizes, 25), percentile(sizes, 50), percentile(sizes, 75)]
    by_size: dict[str, list[float]] = defaultdict(list)
    for e in events:
        ret = safe_float(e["close_returns"].get("3"))
        if math.isfinite(ret):
            by_size[quantile_bucket(safe_float(e["explosion_abs_pct"]), cuts)].append(ret)

    sessions = {"Asia 00-06": [], "EU 07-15": [], "US 16-23": []}
    for e in events:
        ret = safe_float(e["close_returns"].get("3"))
        if not math.isfinite(ret):
            continue
        hour = int(e["hour_utc"])
        if 0 <= hour <= 6:
            sessions["Asia 00-06"].append(ret)
        elif 7 <= hour <= 15:
            sessions["EU 07-15"].append(ret)
        else:
            sessions["US 16-23"].append(ret)

    by_cluster = {"0-1 prior explosions": [], "2+ prior explosions": []}
    event_minutes = [e["minute_ms"] for e in events]
    for idx, e in enumerate(events):
        prior = sum(1 for m in event_minutes[:idx] if 0 < e["minute_ms"] - m <= 5 * 60000)
        ret = safe_float(e["close_returns"].get("3"))
        if math.isfinite(ret):
            by_cluster["2+ prior explosions" if prior >= 2 else "0-1 prior explosions"].append(ret)

    by_side = {"fade up candle (short)": [], "fade down candle (long)": []}
    for e in events:
        ret = safe_float(e["close_returns"].get("3"))
        if math.isfinite(ret):
            by_side["fade up candle (short)" if e["explosion_direction"] == "long" else "fade down candle (long)"].append(ret)

    bars_by_minute = {b.minute_ms: b for b in bill_bars}
    by_entry_lag: dict[str, list[float]] = {"close": [], "after 1m": [], "after 2m": []}
    for e in events:
        exp_bar = bars_by_minute.get(e["minute_ms"])
        if not exp_bar:
            continue
        rev_dir = e["reversal_direction"]
        target = bars_by_minute.get(e["minute_ms"] + 3 * 60000)
        if target:
            by_entry_lag["close"].append(direction_return(exp_bar.close, target.close, rev_dir))
        entry1 = bars_by_minute.get(e["minute_ms"] + 60000)
        target1 = bars_by_minute.get(e["minute_ms"] + 4 * 60000)
        if entry1 and target1:
            by_entry_lag["after 1m"].append(direction_return(entry1.close, target1.close, rev_dir))
        entry2 = bars_by_minute.get(e["minute_ms"] + 2 * 60000)
        target2 = bars_by_minute.get(e["minute_ms"] + 5 * 60000)
        if entry2 and target2:
            by_entry_lag["after 2m"].append(direction_return(entry2.close, target2.close, rev_dir))

    current_outcomes = Counter(sweep.get("current_outcomes") or {})
    current_n = sum(current_outcomes.values())
    current_mfe = sweep.get("current_mfe_stats") or {}

    lines = [
        "# Hypotheses - 20.05.2026",
        "",
        "All hypothesis tests below use `BILL-USDT-SWAP` unless stated otherwise. Forward-return tests use close-entry against the explosive candle; the parameter tests use tick replay.",
        "",
        "## H1: Commission Trap",
        "",
        "| test | n | WR | avg_gross | net_after_fee |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| close-entry 3m | {s_close3['n']} | {fmt(s_close3['wr'], '%')} | {fmt(s_close3['avg'], '%')} | {fmt(s_close3['net'], '%')} |",
        f"| delayed-entry 3m | {s_delayed3['n']} | {fmt(s_delayed3['wr'], '%')} | {fmt(s_delayed3['avg'], '%')} | {fmt(s_delayed3['net'], '%')} |",
        f"| delayed-entry 10m | {s_delayed10['n']} | {fmt(s_delayed10['wr'], '%')} | {fmt(s_delayed10['avg'], '%')} | {fmt(s_delayed10['net'], '%')} |",
        "",
        "Conclusion: the raw close-to-close reversal edge is mostly below the fee hurdle; positive WR alone is not enough.",
        "",
        "## H2: BE Too Early",
        "",
        "| BE trigger | best net_pnl | avg net_pnl | best realized_net |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in by_be:
        lines.append(
            f"| {row['name']} | {fmt(row['best_formula'], '%')} | {fmt(row['avg_formula'], '%')} | {fmt(row['best_realized'], '%')} |"
        )
    lines.extend(
        [
            "",
            f"Current-param outcome mix: TP `{fmt(pct(current_outcomes['tp'], current_n), '%')}`, SL `{fmt(pct(current_outcomes['sl'], current_n), '%')}`, BE `{fmt(pct(current_outcomes['be'], current_n), '%')}`, timeout `{fmt(pct(current_outcomes['timeout'], current_n), '%')}`.",
            f"Current-param MFE avg/p50/p75/p90: `{fmt(current_mfe.get('avg'), '%')}` / `{fmt(current_mfe.get('p50'), '%')}` / `{fmt(current_mfe.get('p75'), '%')}` / `{fmt(current_mfe.get('p90'), '%')}`.",
            "Conclusion: BE is useful only if its trigger improves net_pnl versus no-BE; otherwise it converts many trades with real MFE into zero-gross exits while fees remain.",
            "",
            "## H3: TP Too Far",
            "",
            "| TP | best net_pnl | avg net_pnl | best realized_net |",
            "| ---: | ---: | ---: | ---: |",
        ]
    )
    for row in by_tp:
        lines.append(
            f"| {fmt(row['tp'], '%')} | {fmt(row['best_formula'], '%')} | {fmt(row['avg_formula'], '%')} | {fmt(row['best_realized'], '%')} |"
        )
    lines.extend(
        [
            "",
            "Conclusion: compare the best rows above with TP `1.5%`; if smaller TP dominates, current TP is beyond typical post-entry MFE.",
            "",
            "## H4: Explosion Size Quantiles",
            "",
            f"Quantile cuts by absolute explosion size: Q25 `{fmt(cuts[0], '%')}`, Q50 `{fmt(cuts[1], '%')}`, Q75 `{fmt(cuts[2], '%')}`.",
            "",
            "| bucket | n | WR 3m | avg 3m | net 3m |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for bucket in ["Q1 small", "Q2", "Q3", "Q4 large"]:
        summary = summarize_group(by_size[bucket])
        lines.append(
            f"| {bucket} | {summary['n']} | {fmt(summary['wr'], '%')} | {fmt(summary['avg'], '%')} | {fmt(summary['net'], '%')} |"
        )
    lines.extend(
        [
            "",
            "## H5: Hour / Session",
            "",
            "| session UTC | n | WR 3m | avg 3m | net 3m |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for session, vals in sessions.items():
        summary = summarize_group(vals)
        lines.append(
            f"| {session} | {summary['n']} | {fmt(summary['wr'], '%')} | {fmt(summary['avg'], '%')} | {fmt(summary['net'], '%')} |"
        )
    lines.extend(
        [
            "",
            "## H6: Consecutive Explosions",
            "",
            "| last 5m context | n | WR 3m | avg 3m | net 3m |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, vals in by_cluster.items():
        summary = summarize_group(vals)
        lines.append(f"| {name} | {summary['n']} | {fmt(summary['wr'], '%')} | {fmt(summary['avg'], '%')} | {fmt(summary['net'], '%')} |")
    lines.extend(
        [
            "",
            "## H7: Direction Asymmetry",
            "",
            "| side | n | WR 3m | avg 3m | net 3m |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, vals in by_side.items():
        summary = summarize_group(vals)
        lines.append(f"| {name} | {summary['n']} | {fmt(summary['wr'], '%')} | {fmt(summary['avg'], '%')} | {fmt(summary['net'], '%')} |")
    lines.extend(
        [
            "",
            "## H8: Entry Timing",
            "",
            "| entry timing | n | WR 3m | avg 3m | net 3m |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, vals in by_entry_lag.items():
        summary = summarize_group(vals)
        lines.append(f"| {name} | {summary['n']} | {fmt(summary['wr'], '%')} | {fmt(summary['avg'], '%')} | {fmt(summary['net'], '%')} |")
    lines.extend(
        [
            "",
            "## Final Conclusion",
            "",
            "The small profit is primarily a fee-hurdle and exit-shape problem: many BILL reversals are directionally correct but too small for `0.20%` round-trip fees plus a distant TP.",
            "Next config experiment should prefer only parameter combinations with positive tick-replay net_pnl, and pair inclusion should require positive net edge, not just gross WR.",
        ]
    )
    return "\n".join(lines) + "\n"


def classify_context(pair_dir: str, pair_change: float, context_change: float) -> str:
    if not math.isfinite(context_change) or abs(context_change) < 0.3:
        return "isolated"
    if pair_change == 0:
        return "isolated"
    same_sign = (pair_change > 0 and context_change > 0) or (pair_change < 0 and context_change < 0)
    return "aligned" if same_sign else "opposite"


def network_context(universe: list[dict[str, Any]]) -> dict[str, Any]:
    btc_bars, _ = aggregate_pair(TICK_ROOT / "BTC-USDT-SWAP", keep_ticks=False)
    sol_bars, _ = aggregate_pair(TICK_ROOT / "SOL-USDT-SWAP", keep_ticks=False)
    ctx = {
        "BTC": {b.minute_ms: b.price_change_pct for b in btc_bars},
        "SOL": {b.minute_ms: b.price_change_pct for b in sol_bars},
    }
    rows = []
    for pair in universe:
        if pair.get("explosion_count", 0) < 30:
            continue
        per_context: dict[str, Any] = {}
        for ctx_name, ctx_map in ctx.items():
            groups: dict[str, list[float]] = defaultdict(list)
            for event in pair["events"]:
                pair_change = safe_float(event["explosion_size_pct"])
                ctx_change = safe_float(ctx_map.get(event["minute_ms"]))
                group = classify_context(pair["pair"], pair_change, ctx_change)
                ret = safe_float(event["close_returns"].get("3"))
                if math.isfinite(ret):
                    groups[group].append(ret)
            summaries = {name: summarize_group(vals) for name, vals in groups.items()}
            aligned_wr = safe_float(summaries.get("aligned", {}).get("wr"))
            isolated_wr = safe_float(summaries.get("isolated", {}).get("wr"))
            opposite_wr = safe_float(summaries.get("opposite", {}).get("wr"))
            spread = max([v for v in [aligned_wr, isolated_wr, opposite_wr] if math.isfinite(v)], default=float("nan")) - min(
                [v for v in [aligned_wr, isolated_wr, opposite_wr] if math.isfinite(v)], default=float("nan")
            )
            per_context[ctx_name] = {"groups": summaries, "wr_spread": spread}
        best_ctx = max(per_context.items(), key=lambda kv: safe_float(kv[1]["wr_spread"]))[0]
        rows.append({"pair": pair["pair"], "events": pair["explosion_count"], "best_context": best_ctx, "contexts": per_context})
    return {"rows": rows}


def render_network_report(network: dict[str, Any]) -> str:
    lines = [
        "# Network Context - 20.05.2026",
        "",
        "Method: for every pair explosive 1m candle with pair sample `n >= 30`, join the same-minute `BTC-USDT-SWAP` and `SOL-USDT-SWAP` candle from tape.",
        "Classification is `isolated` when the network candle moved less than `0.3%`; otherwise `aligned` if signs match and `opposite` if signs differ. WR uses close-entry 3m reversal return.",
        "",
        "## Best Predictor Per Pair",
        "",
        "| pair | events | best ctx | isolated WR/n | aligned WR/n | opposite WR/n | decision |",
        "| --- | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for row in sorted(network["rows"], key=lambda r: r["pair"]):
        best = row["best_context"]
        groups = row["contexts"][best]["groups"]
        iso = groups.get("isolated", {"n": 0, "wr": float("nan"), "avg": float("nan")})
        ali = groups.get("aligned", {"n": 0, "wr": float("nan"), "avg": float("nan")})
        opp = groups.get("opposite", {"n": 0, "wr": float("nan"), "avg": float("nan")})
        decision = "no filter"
        if ali["n"] >= 10 and iso["n"] >= 10 and safe_float(ali["wr"]) + 5 < safe_float(iso["wr"]):
            decision = "filter aligned"
        if ali["n"] >= 10 and opp["n"] >= 10 and safe_float(ali["wr"]) + 5 < safe_float(opp["wr"]):
            decision = "filter aligned"
        lines.append(
            f"| {row['pair']} | {row['events']} | {best} | {fmt(iso['wr'], '%')}/{iso['n']} | "
            f"{fmt(ali['wr'], '%')}/{ali['n']} | {fmt(opp['wr'], '%')}/{opp['n']} | {decision} |"
        )
    lines.extend(["", "## Full BTC/SOL Tables", ""])
    for row in sorted(network["rows"], key=lambda r: r["pair"]):
        lines.extend([f"### {row['pair']}", "", "| context | group | n | WR 3m | avg 3m | net 3m |", "| --- | --- | ---: | ---: | ---: | ---: |"])
        for ctx_name in ["BTC", "SOL"]:
            groups = row["contexts"][ctx_name]["groups"]
            for group in ["isolated", "aligned", "opposite"]:
                summary = groups.get(group, {"n": 0, "wr": float("nan"), "avg": float("nan"), "net": float("nan")})
                lines.append(
                    f"| {ctx_name} | {group} | {summary['n']} | {fmt(summary['wr'], '%')} | {fmt(summary['avg'], '%')} | {fmt(summary['net'], '%')} |"
                )
        lines.append("")
    lines.extend(
        [
            "## Conclusion",
            "",
            "Use the `decision` column only when the group has enough observations; small aligned/opposite buckets should not drive production filters.",
            "If a pair shows lower aligned WR with both BTC and SOL, the next paper run should exclude aligned network impulses for that pair; otherwise keep network filtering off.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    old_path = OUT_DIR / "reversal_universe_data.json"
    old_data = json.loads(old_path.read_text(encoding="utf-8")) if old_path.exists() else None

    if UNIVERSE_JSON.exists():
        payload = json.loads(UNIVERSE_JSON.read_text(encoding="utf-8"))
        universe = payload["pairs"]
        print(f"loaded {UNIVERSE_JSON}")
    else:
        universe = scan_universe()
        payload = {
            "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": str(TICK_ROOT),
            "threshold_pct": EXPLOSION_THRESHOLD_PCT,
            "fee_round_trip_pct": FEE_ROUND_TRIP_PCT,
            "min_days": MIN_DAYS,
            "min_explosions": MIN_EXPLOSIONS,
            "pairs": universe,
        }
        UNIVERSE_JSON.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        print(f"saved {UNIVERSE_JSON}")
    UNIVERSE_MD.write_text(render_universe_report(universe, old_data), encoding="utf-8")
    print(f"saved {UNIVERSE_MD}")

    bill_bars, _ = aggregate_pair(TICK_ROOT / "BILL-USDT-SWAP", keep_ticks=False)
    sweep = param_sweep_bill_streaming(bill_bars)
    PARAM_MD.write_text(render_param_report(sweep), encoding="utf-8")
    HYPOTHESES_MD.write_text(render_hypotheses(universe, bill_bars, sweep), encoding="utf-8")
    print(f"saved {PARAM_MD}")
    print(f"saved {HYPOTHESES_MD}")

    network = network_context(universe)
    NETWORK_MD.write_text(render_network_report(network), encoding="utf-8")
    print(f"saved {NETWORK_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
