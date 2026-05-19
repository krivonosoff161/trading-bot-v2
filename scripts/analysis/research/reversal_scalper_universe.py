from __future__ import annotations

import csv
import gzip
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
TICK_ROOT = Path(r"E:\trading-data\ticks")
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
REPORT_PATH = OUT_DIR / "reversal_universe_report.md"
DATA_PATH = OUT_DIR / "reversal_universe_data.json"

EXPLOSION_THRESHOLD_PCT = 0.8
MIN_DAYS = 3
MIN_EXPLOSIONS = 10
REVERSAL_HORIZONS = [1, 2, 3, 5, 10]
INTRA_CHECKPOINTS_SEC = [10, 15, 20, 30, 40, 50]
INTRA_THRESHOLDS_PCT = [0.3, 0.5, 0.7]
INTRA_TRIGGER_DEFS = {
    "move_0p5_20s": {"threshold": 0.5, "max_sec": 20.0, "volume_mult": None},
    "move_0p5_20s_vol2x": {"threshold": 0.5, "max_sec": 20.0, "volume_mult": 2.0},
    "move_0p3_10s": {"threshold": 0.3, "max_sec": 10.0, "volume_mult": None},
}
SL_PROMOTION_RULES = {
    "be_after_0p5": {"mfe_trigger_pct": 0.5, "lock_pct": 0.0},
    "lock_0p2_after_0p7": {"mfe_trigger_pct": 0.7, "lock_pct": 0.2},
    "lock_0p4_after_1p0": {"mfe_trigger_pct": 1.0, "lock_pct": 0.4},
}


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
    volume_contracts: float
    buy_vol: float
    sell_vol: float
    price_change_pct: float
    intra: dict[str, Any]


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
    if name.endswith(".csv.gz"):
        return name.removesuffix(".csv.gz")
    return name.removesuffix(".csv")


def tick_files(pair_dir: Path) -> list[Path]:
    return sorted([*pair_dir.glob("*.csv"), *pair_dir.glob("*.csv.gz")], key=lambda p: date_from_file(p))


def open_tick_file(path: Path):
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def aggregate_file(path: Path) -> list[Bar]:
    minutes: dict[int, dict[str, Any]] = {}
    date = date_from_file(path)
    with open_tick_file(path) as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            side = (row.get("side") or "").lower()
            if side == "gap" or side not in {"buy", "sell"}:
                continue
            ts_ms = int(float(row["ts_ms"]))
            minute_ms = ts_ms - (ts_ms % 60000)
            price = safe_float(row.get("price"))
            size = safe_float(row.get("size"))
            if not math.isfinite(price) or not math.isfinite(size):
                continue
            bucket = minutes.get(minute_ms)
            if bucket is None:
                bucket = {
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 0.0,
                    "buy_vol": 0.0,
                    "sell_vol": 0.0,
                    "vol_20s": 0.0,
                    "checkpoint_prices": {},
                    "cross": {"long": {}, "short": {}},
                    "ticks": [],
                }
                minutes[minute_ms] = bucket
            elapsed_ms = ts_ms - minute_ms
            elapsed_sec = elapsed_ms / 1000.0
            bucket["ticks"].append((elapsed_sec, price))
            if elapsed_ms <= 20000:
                bucket["vol_20s"] += size
            for checkpoint in INTRA_CHECKPOINTS_SEC:
                if elapsed_ms >= checkpoint * 1000 and str(checkpoint) not in bucket["checkpoint_prices"]:
                    bucket["checkpoint_prices"][str(checkpoint)] = price
            if bucket["open"] > 0:
                long_move = (price - bucket["open"]) / bucket["open"] * 100
                short_move = (bucket["open"] - price) / bucket["open"] * 100
                for threshold in INTRA_THRESHOLDS_PCT:
                    key = str(threshold)
                    if long_move >= threshold and key not in bucket["cross"]["long"]:
                        bucket["cross"]["long"][key] = {"sec": elapsed_sec, "price": price}
                    if short_move >= threshold and key not in bucket["cross"]["short"]:
                        bucket["cross"]["short"][key] = {"sec": elapsed_sec, "price": price}
            bucket["high"] = max(bucket["high"], price)
            bucket["low"] = min(bucket["low"], price)
            bucket["close"] = price
            bucket["volume"] += size
            if side == "buy":
                bucket["buy_vol"] += size
            else:
                bucket["sell_vol"] += size

    bars: list[Bar] = []
    for minute_ms in sorted(minutes):
        bucket = minutes[minute_ms]
        open_p = bucket["open"]
        close_p = bucket["close"]
        change = (close_p - open_p) / open_p * 100 if open_p > 0 else float("nan")
        dt = datetime.fromtimestamp(minute_ms / 1000, tz=timezone.utc)
        checkpoint_moves = {
            sec: ((price - open_p) / open_p * 100 if open_p > 0 else float("nan"))
            for sec, price in bucket["checkpoint_prices"].items()
        }
        bars.append(
            Bar(
                minute_ms=minute_ms,
                ts=iso_from_ms(minute_ms),
                date=date,
                hour_utc=dt.hour,
                open=open_p,
                high=bucket["high"],
                low=bucket["low"],
                close=close_p,
                volume_contracts=bucket["volume"],
                buy_vol=bucket["buy_vol"],
                sell_vol=bucket["sell_vol"],
                price_change_pct=change,
                intra={
                    "vol_20s": bucket["vol_20s"],
                    "checkpoint_moves_pct": checkpoint_moves,
                    "cross": bucket["cross"],
                    "ticks": bucket["ticks"],
                },
            )
        )
    return bars


def average(values: Iterable[float]) -> float:
    vals = [v for v in values if math.isfinite(v)]
    return sum(vals) / len(vals) if vals else float("nan")


def percentile(values: list[float], q: float) -> float:
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


def pct(part: int, total: int) -> float:
    return part / total * 100 if total else float("nan")


def directional_return(entry: float, price: float, direction: str) -> float:
    if entry <= 0 or price <= 0:
        return float("nan")
    if direction == "long":
        return (price - entry) / entry * 100
    return (entry - price) / entry * 100


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


def reversal_rows(bars: list[Bar], explosions: list[Explosion]) -> list[dict[str, Any]]:
    by_minute = {bar.minute_ms: bar for bar in bars}
    rows: list[dict[str, Any]] = []
    for exp in explosions:
        entry_bar = by_minute.get(exp.minute_ms + 60000)
        if entry_bar is None:
            continue
        reversal_direction = "short" if exp.direction == "long" else "long"
        payload: dict[str, Any] = {
            "explosion_ts": exp.ts,
            "explosion_direction": exp.direction,
            "reversal_direction": reversal_direction,
            "explosion_size_pct": exp.size_pct,
            "entry_ts": entry_bar.ts,
            "entry_price": entry_bar.close,
            "returns": {},
        }
        for horizon in REVERSAL_HORIZONS:
            target = by_minute.get(entry_bar.minute_ms + horizon * 60000)
            payload["returns"][str(horizon)] = (
                directional_return(entry_bar.close, target.close, reversal_direction) if target else None
            )
        rows.append(payload)
    return rows


def summarize_returns(rows: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    vals = [safe_float(row["returns"].get(str(horizon))) for row in rows if row["returns"].get(str(horizon)) is not None]
    wins = sum(1 for value in vals if value > 0)
    return {
        "n": len(vals),
        "avg_return_pct": average(vals),
        "win_rate": pct(wins, len(vals)),
    }


def summarize_values(values: list[float]) -> dict[str, Any]:
    vals = [value for value in values if math.isfinite(value)]
    return {
        "n": len(vals),
        "avg": average(vals),
        "p50": median(vals) if vals else None,
        "p75": percentile(vals, 75),
        "p90": percentile(vals, 90),
    }


def intra_rows(bars: list[Bar], explosions: list[Explosion]) -> list[dict[str, Any]]:
    by_minute = {bar.minute_ms: bar for bar in bars}
    avg_vol_20s = average([safe_float(bar.intra.get("vol_20s")) for bar in bars])
    rows: list[dict[str, Any]] = []
    for exp in explosions:
        bar = by_minute.get(exp.minute_ms)
        if bar is None:
            continue
        direction = exp.direction
        direction_cross = (bar.intra.get("cross") or {}).get(direction) or {}
        row: dict[str, Any] = {
            "ts": exp.ts,
            "direction": direction,
            "size_pct": exp.size_pct,
            "vol_20s": safe_float(bar.intra.get("vol_20s")),
            "avg_vol_20s": avg_vol_20s,
            "checkpoint_moves_pct": bar.intra.get("checkpoint_moves_pct") or {},
            "threshold_hits": {},
            "triggers": {},
        }
        for threshold in INTRA_THRESHOLDS_PCT:
            key = str(threshold)
            row["threshold_hits"][key] = direction_cross.get(key)
        for name, cfg in INTRA_TRIGGER_DEFS.items():
            key = str(cfg["threshold"])
            hit = direction_cross.get(key)
            volume_ok = True
            if cfg["volume_mult"] is not None:
                volume_ok = (
                    math.isfinite(row["vol_20s"])
                    and math.isfinite(avg_vol_20s)
                    and row["vol_20s"] >= float(cfg["volume_mult"]) * avg_vol_20s
                )
            triggered = bool(hit and safe_float(hit.get("sec")) <= float(cfg["max_sec"]) and volume_ok)
            payload: dict[str, Any] = {"triggered": triggered}
            if triggered:
                trigger_price = safe_float(hit.get("price"))
                payload.update(
                    {
                        "sec": safe_float(hit.get("sec")),
                        "price": trigger_price,
                        "to_close_return_pct": directional_return(trigger_price, bar.close, direction),
                    }
                )
                for horizon in (1, 3):
                    target = by_minute.get(bar.minute_ms + horizon * 60000)
                    close_ret = directional_return(bar.close, target.close, direction) if target else None
                    intra_ret = directional_return(trigger_price, target.close, direction) if target else None
                    payload[f"intra_plus_{horizon}m_pct"] = intra_ret
                    payload[f"close_plus_{horizon}m_pct"] = close_ret
                    payload[f"edge_vs_close_{horizon}m_pct"] = (
                        intra_ret - close_ret
                        if intra_ret is not None and close_ret is not None
                        else None
                    )
                horizon_ticks: list[tuple[float, float]] = []
                trigger_sec = safe_float(hit.get("sec"))
                for minute in range(0, 6):
                    item = by_minute.get(bar.minute_ms + minute * 60000)
                    if item is None:
                        continue
                    for tick_sec, tick_price in item.intra.get("ticks", []):
                        abs_sec = minute * 60.0 + safe_float(tick_sec)
                        if abs_sec >= trigger_sec:
                            horizon_ticks.append((abs_sec, safe_float(tick_price)))
                if direction == "long":
                    mfe = max(((price - trigger_price) / trigger_price * 100 for _, price in horizon_ticks), default=float("nan"))
                    mae_after_mfe: dict[str, float | None] = {}
                    for rule_name, rule in SL_PROMOTION_RULES.items():
                        hit_idx = None
                        for idx, (_, price) in enumerate(horizon_ticks):
                            if (price - trigger_price) / trigger_price * 100 >= rule["mfe_trigger_pct"]:
                                hit_idx = idx
                                break
                        if hit_idx is None:
                            mae_after_mfe[rule_name] = None
                        else:
                            mae_after_mfe[rule_name] = min(
                                ((price - trigger_price) / trigger_price * 100 for _, price in horizon_ticks[hit_idx:]),
                                default=float("nan"),
                            )
                else:
                    mfe = max(((trigger_price - price) / trigger_price * 100 for _, price in horizon_ticks), default=float("nan"))
                    mae_after_mfe = {}
                    for rule_name, rule in SL_PROMOTION_RULES.items():
                        hit_idx = None
                        for idx, (_, price) in enumerate(horizon_ticks):
                            if (trigger_price - price) / trigger_price * 100 >= rule["mfe_trigger_pct"]:
                                hit_idx = idx
                                break
                        if hit_idx is None:
                            mae_after_mfe[rule_name] = None
                        else:
                            mae_after_mfe[rule_name] = min(
                                ((trigger_price - price) / trigger_price * 100 for _, price in horizon_ticks[hit_idx:]),
                                default=float("nan"),
                            )
                payload["mfe_0_5m_pct"] = mfe
                payload["sl_promotion"] = {}
                for rule_name, rule in SL_PROMOTION_RULES.items():
                    hit = math.isfinite(mfe) and mfe >= float(rule["mfe_trigger_pct"])
                    locked = hit and mae_after_mfe.get(rule_name) is not None and safe_float(mae_after_mfe.get(rule_name)) <= float(rule["lock_pct"])
                    payload["sl_promotion"][rule_name] = {
                        "hit": hit,
                        "lock_pct": float(rule["lock_pct"]),
                        "would_exit_at_promoted_stop": locked,
                        "stop_result_pct": float(rule["lock_pct"]) if locked else None,
                    }
            row["triggers"][name] = payload
        rows.append(row)
    return rows


def summarize_intra(rows: list[dict[str, Any]]) -> dict[str, Any]:
    threshold_times: dict[str, dict[str, Any]] = {}
    for threshold in INTRA_THRESHOLDS_PCT:
        key = str(threshold)
        threshold_times[key] = summarize_values([
            safe_float((row["threshold_hits"].get(key) or {}).get("sec"))
            for row in rows
            if row["threshold_hits"].get(key)
        ])

    triggers: dict[str, dict[str, Any]] = {}
    for name in INTRA_TRIGGER_DEFS:
        active = [row["triggers"][name] for row in rows if row["triggers"][name]["triggered"]]
        sl_summary: dict[str, dict[str, Any]] = {}
        for rule_name in SL_PROMOTION_RULES:
            hit_rows = [row for row in active if (row.get("sl_promotion") or {}).get(rule_name, {}).get("hit")]
            exit_rows = [
                row
                for row in active
                if (row.get("sl_promotion") or {}).get(rule_name, {}).get("would_exit_at_promoted_stop")
            ]
            sl_summary[rule_name] = {
                "hit_n": len(hit_rows),
                "hit_pct": pct(len(hit_rows), len(active)),
                "would_exit_n": len(exit_rows),
                "would_exit_pct": pct(len(exit_rows), len(active)),
                "avg_stop_result_pct": average(
                    safe_float((row.get("sl_promotion") or {}).get(rule_name, {}).get("stop_result_pct"))
                    for row in exit_rows
                ),
            }
        triggers[name] = {
            "n": len(active),
            "fire_pct_of_explosions": pct(len(active), len(rows)),
            "avg_trigger_sec": average(safe_float(row.get("sec")) for row in active),
            "to_close_avg_pct": average(safe_float(row.get("to_close_return_pct")) for row in active),
            "intra_plus_1m_avg_pct": average(safe_float(row.get("intra_plus_1m_pct")) for row in active),
            "close_plus_1m_avg_pct": average(safe_float(row.get("close_plus_1m_pct")) for row in active),
            "edge_vs_close_1m_avg_pct": average(safe_float(row.get("edge_vs_close_1m_pct")) for row in active),
            "intra_plus_3m_avg_pct": average(safe_float(row.get("intra_plus_3m_pct")) for row in active),
            "close_plus_3m_avg_pct": average(safe_float(row.get("close_plus_3m_pct")) for row in active),
            "edge_vs_close_3m_avg_pct": average(safe_float(row.get("edge_vs_close_3m_pct")) for row in active),
            "mfe_0_5m_avg_pct": average(safe_float(row.get("mfe_0_5m_pct")) for row in active),
            "sl_promotion": sl_summary,
        }
    return {
        "n_explosions": len(rows),
        "threshold_times_sec": threshold_times,
        "triggers": triggers,
    }


def verdict(rev3: dict[str, Any]) -> str:
    wr = safe_float(rev3.get("win_rate"))
    avg_ret = safe_float(rev3.get("avg_return_pct"))
    if not math.isfinite(wr) or not math.isfinite(avg_ret):
        return "noise"
    if wr > 55.0 and avg_ret > 0.1:
        return "strong_reversal"
    if wr > 50.0 and avg_ret < 0.1:
        return "weak_reversal"
    if wr < 45.0:
        return "continuation"
    return "noise"


def analyze_pair(pair_dir: Path) -> dict[str, Any]:
    pair = pair_dir.name
    files = tick_files(pair_dir)
    all_bars: list[Bar] = []
    days_with_bars = 0
    for path in files:
        bars = aggregate_file(path)
        if bars:
            days_with_bars += 1
            all_bars.extend(bars)
    explosions = detect_explosions(all_bars)
    rev_rows = reversal_rows(all_bars, explosions)
    intra = intra_rows(all_bars, explosions)
    long_count = sum(1 for exp in explosions if exp.direction == "long")
    short_count = len(explosions) - long_count
    daily_counts = Counter(exp.date for exp in explosions)
    forward = {str(h): summarize_returns(rev_rows, h) for h in REVERSAL_HORIZONS}
    best_hold = max(
        ((h, payload) for h, payload in forward.items() if payload["n"] > 0),
        key=lambda item: item[1]["avg_return_pct"],
        default=(None, None),
    )
    hour_counts = Counter(exp.hour_utc for exp in explosions)
    sizes = [abs(exp.size_pct) for exp in explosions]
    osc = opposite_delay_stats(explosions)
    eligible = days_with_bars >= MIN_DAYS and len(explosions) >= MIN_EXPLOSIONS
    return {
        "pair": pair,
        "days": days_with_bars,
        "files": [path.name for path in files],
        "bar_count": len(all_bars),
        "explosion_count": len(explosions),
        "explosive_per_day": len(explosions) / days_with_bars if days_with_bars else 0.0,
        "long_count": long_count,
        "short_count": short_count,
        "long_pct": pct(long_count, len(explosions)),
        "short_pct": pct(short_count, len(explosions)),
        "daily_explosions": dict(sorted(daily_counts.items())),
        "oscillation": osc,
        "reversal": forward,
        "best_hold": int(best_hold[0]) if best_hold[0] is not None else None,
        "best_hold_avg_return_pct": best_hold[1]["avg_return_pct"] if best_hold[1] else None,
        "verdict": verdict(forward["3"]),
        "eligible": eligible,
        "sample_note": "preliminary" if len(rev_rows) < 20 else "usable",
        "explosion_size": {
            "avg_abs_pct": average(sizes),
            "p75_abs_pct": percentile(sizes, 75),
            "p90_abs_pct": percentile(sizes, 90),
        },
        "hour_counts": dict(sorted((str(k), v) for k, v in hour_counts.items())),
        "reversal_rows_n": len(rev_rows),
        "intra": summarize_intra(intra),
    }


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


def render_report(all_results: list[dict[str, Any]]) -> str:
    eligible = [row for row in all_results if row["eligible"]]
    excluded = [row for row in all_results if not row["eligible"]]
    ranked = sorted(eligible, key=lambda row: row["explosive_per_day"], reverse=True)
    top_reversal = sorted(
        eligible,
        key=lambda row: safe_float(row["reversal"]["3"]["avg_return_pct"]),
        reverse=True,
    )[:10]

    lines = [
        "# Reversal Scalper Universe Scan",
        "",
        f"Universe source: `{TICK_ROOT}`. Each directory is treated as one pair.",
        f"Explosive 1m candle threshold: `abs(price_change_pct) >= {EXPLOSION_THRESHOLD_PCT:.1f}%`.",
        "Reversal test: after an explosive candle closes, wait one full 1m bar, enter against the explosion at that bar close, then measure forward return.",
        "",
        f"- scanned pairs: `{len(all_results)}`",
        f"- eligible pairs (`days >= {MIN_DAYS}` and `explosions >= {MIN_EXPLOSIONS}`): `{len(eligible)}`",
        f"- excluded pairs: `{len(excluded)}`",
        "",
        "## Universe Table",
        "",
        "| pair | days | bars | explosions | exp/day | long_pct | osc_median | rev3_n | rev3_WR | rev3_avg | best_hold | verdict | note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in ranked:
        rev3 = row["reversal"]["3"]
        lines.append(
            f"| {row['pair']} | {row['days']} | {row['bar_count']} | {row['explosion_count']} | "
            f"{fmt(row['explosive_per_day'])} | {fmt(row['long_pct'], '%')} | {fmt(row['oscillation']['median_min'], 'm')} | "
            f"{rev3['n']} | {fmt(rev3['win_rate'], '%')} | {fmt(rev3['avg_return_pct'], '%')} | "
            f"{row['best_hold']}m | {row['verdict']} | {row['sample_note']} |"
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
    for row in sorted(excluded, key=lambda item: (-item["days"], -item["explosion_count"], item["pair"])):
        reasons = []
        if row["days"] < MIN_DAYS:
            reasons.append(f"days<{MIN_DAYS}")
        if row["explosion_count"] < MIN_EXPLOSIONS:
            reasons.append(f"explosions<{MIN_EXPLOSIONS}")
        lines.append(f"| {row['pair']} | {row['days']} | {row['explosion_count']} | {', '.join(reasons)} |")

    lines.extend(
        [
            "",
            "## Top 10 Reversal Candidates By 3m Avg Return",
            "",
        ]
    )
    for row in top_reversal:
        lines.extend(
            [
                f"### {row['pair']}",
                "",
                f"- verdict: `{row['verdict']}`",
                f"- days: `{row['days']}`, explosions: `{row['explosion_count']}`, exp/day: `{fmt(row['explosive_per_day'])}`",
                f"- explosion size avg/p75/p90: `{fmt(row['explosion_size']['avg_abs_pct'], '%')}` / `{fmt(row['explosion_size']['p75_abs_pct'], '%')}` / `{fmt(row['explosion_size']['p90_abs_pct'], '%')}`",
                f"- oscillation median next opposite: `{fmt(row['oscillation']['median_min'], 'm')}`",
                "",
                "| hold | n | WR | avg_return |",
                "| ---: | ---: | ---: | ---: |",
            ]
        )
        for horizon in REVERSAL_HORIZONS:
            payload = row["reversal"][str(horizon)]
            lines.append(f"| {horizon}m | {payload['n']} | {fmt(payload['win_rate'], '%')} | {fmt(payload['avg_return_pct'], '%')} |")
        top_hours = sorted(row["hour_counts"].items(), key=lambda kv: int(kv[0]))
        hours_str = ", ".join(f"{hour}: {count}" for hour, count in top_hours)
        lines.extend(["", f"UTC hour explosion counts: `{hours_str}`", ""])

    lines.extend(
        [
            "## Intra-Candle Entry Research",
            "",
            "Method: for every explosive 1m candle, inspect ticks inside that candle. Trigger price is the first tick where the move from candle open reaches the threshold in the final explosion direction.",
            "",
            "Tested triggers:",
            "",
            "- `move_0p5_20s`: price moved at least `0.5%` within first `20s`.",
            "- `move_0p5_20s_vol2x`: same, plus first-20s volume is at least `2x` the pair's average 20s volume.",
            "- `move_0p3_10s`: aggressive trigger, price moved at least `0.3%` within first `10s`.",
            "",
            "Returns are measured in the explosion direction. `edge_vs_close` compares early entry with entering at the same candle close on the same event set.",
            "",
            "### Trigger Summary By Pair",
            "",
            "| pair | trigger | n | fire_pct | avg_sec | to_close | intra_1m | close_1m | edge_1m | intra_3m | close_3m | edge_3m |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in ranked:
        for trigger_name, payload in row["intra"]["triggers"].items():
            lines.append(
                f"| {row['pair']} | {trigger_name} | {payload['n']} | {fmt(payload['fire_pct_of_explosions'], '%')} | "
                f"{fmt(payload['avg_trigger_sec'], 's')} | {fmt(payload['to_close_avg_pct'], '%')} | "
                f"{fmt(payload['intra_plus_1m_avg_pct'], '%')} | {fmt(payload['close_plus_1m_avg_pct'], '%')} | {fmt(payload['edge_vs_close_1m_avg_pct'], '%')} | "
                f"{fmt(payload['intra_plus_3m_avg_pct'], '%')} | {fmt(payload['close_plus_3m_avg_pct'], '%')} | {fmt(payload['edge_vs_close_3m_avg_pct'], '%')} |"
            )

    lines.extend(
        [
            "",
            "### Threshold Hit Timing",
            "",
            "| pair | threshold | n | median_sec | p75_sec | p90_sec |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in ranked:
        for threshold, payload in row["intra"]["threshold_times_sec"].items():
            lines.append(
                f"| {row['pair']} | {threshold}% | {payload['n']} | {fmt(payload['p50'], 's')} | "
                f"{fmt(payload['p75'], 's')} | {fmt(payload['p90'], 's')} |"
            )

    lines.extend(
        [
            "",
            "### Dynamic SL Promotion",
            "",
            "Question tested: after an intra-candle entry has positive MFE, can the stop be promoted to BE or positive lock so a later stop-out closes flat/green instead of red. This approximates the user's slippage concern: if the promoted stop is above entry for longs or below entry for shorts, execution can still be positive even when the stop is hit.",
            "MFE and promoted-stop hits are evaluated in tick order after the trigger, not from full candle high/low.",
            "",
            "Rules tested on the first 5 minutes after trigger:",
            "",
            "- `be_after_0p5`: after MFE reaches `+0.5%`, promote SL to `0.0%`.",
            "- `lock_0p2_after_0p7`: after MFE reaches `+0.7%`, promote SL to `+0.2%`.",
            "- `lock_0p4_after_1p0`: after MFE reaches `+1.0%`, promote SL to `+0.4%`.",
            "",
            "| pair | trigger | rule | hit_n | hit_pct | would_stop_n | would_stop_pct | stop_result | mfe_0_5m_avg |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in ranked:
        for trigger_name, trigger_payload in row["intra"]["triggers"].items():
            for rule_name, payload in trigger_payload["sl_promotion"].items():
                lines.append(
                    f"| {row['pair']} | {trigger_name} | {rule_name} | {payload['hit_n']} | {fmt(payload['hit_pct'], '%')} | "
                    f"{payload['would_exit_n']} | {fmt(payload['would_exit_pct'], '%')} | {fmt(payload['avg_stop_result_pct'], '%')} | "
                    f"{fmt(trigger_payload['mfe_0_5m_avg_pct'], '%')} |"
                )

    lines.extend(
        [
            "",
            "Interpretation: dynamic SL promotion is relevant when `hit_pct` is high. A promoted stop does not create the initial edge, but it can convert a later SL into BE or a small positive exit after the impulse has already paid enough MFE.",
            "",
            "### WS Implementation Concept",
            "",
            "Variant A - `confirm=0` candles:",
            "",
            "- `ws_feed.py` already receives forming candle updates from OKX.",
            "- Add a branch in `_on_candle_update()` for `confirm=0` updates.",
            "- Track per-symbol current 1m candle open, latest close, elapsed seconds, and volume.",
            "- Fire an early candidate when `abs(open -> current_close) >= threshold` and elapsed time is inside the tested window.",
            "- Simpler integration, less moving state, and no separate trade-stream consumer.",
            "",
            "Variant B - trades stream:",
            "",
            "- Add a new component reading the same OKX trades channel as the recorder.",
            "- Maintain rolling per-symbol 10s/20s open/current/volume state directly from trades.",
            "- Fire when price move and volume trigger are met.",
            "- More precise than `confirm=0`, but higher implementation risk: duplicate stream handling, state drift, race with recorder/orchestrator, and more noisy microstructure spikes.",
            "",
            "Recommendation: start with Variant A. It is enough to test whether early entry adds more than `0.1%` edge over close-entry without adding a second real-time trades engine. Use Variant B only if `confirm=0` granularity is too slow or misses the measured trigger windows.",
            "",
        ]
    )

    lines.extend(
        [
            "## Notes",
            "",
            "- Pairs are never blended for verdicts; each verdict is per-pair.",
            "- `preliminary` means the pair has fewer than 20 reversal rows even if it passed the minimum inclusion filter.",
            "- A `continuation` verdict means the 3m reversal win rate is below 45%, so fading the explosion is probably the wrong side for that pair.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pair_dirs = sorted([path for path in TICK_ROOT.iterdir() if path.is_dir()], key=lambda p: p.name)
    results = []
    for pair_dir in pair_dirs:
        print(f"analyzing {pair_dir.name}")
        results.append(analyze_pair(pair_dir))

    payload = {
        "threshold_pct": EXPLOSION_THRESHOLD_PCT,
        "min_days": MIN_DAYS,
        "min_explosions": MIN_EXPLOSIONS,
        "pairs": results,
    }
    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(render_report(results), encoding="utf-8")
    print(f"saved {REPORT_PATH}")
    print(f"saved {DATA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
