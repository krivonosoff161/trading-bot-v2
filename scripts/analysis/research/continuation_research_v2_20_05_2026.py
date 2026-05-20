from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import continuation_research_20_05_2026 as base


ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
CASE_DIR = OUT_DIR / "continuation_cases_v2_20_05_2026"
REPORT_MD = OUT_DIR / "continuation_structural_exit_v2_20_05_2026.md"
ENTRY_MD = OUT_DIR / "continuation_entry_timing_v2_20_05_2026.md"
HYP_MD = OUT_DIR / "continuation_hypotheses_v2_20_05_2026.md"
SUMMARY_JSON = OUT_DIR / "continuation_summary_v2_20_05_2026.json"
RUN_LOG = OUT_DIR / "continuation_run_v2_20_05_2026.log"

CONFIG = {
    "fee_pct": 0.20,
    "entry_slippage_pct": 0.05,
    "max_hold_min": 20,
    "impulse_stop_buffers_pct": [0.0, 0.1, 0.2],
    "structure_break_k": [1, 2, 3],
    "giveback_pct": [30, 40, 50],
    "giveback_activation_mfe_pct": 0.5,
    "intra_triggers": [
        {"name": "intra_0p3_10s", "threshold_pct": 0.3, "max_sec": 10.0},
        {"name": "intra_0p5_20s", "threshold_pct": 0.5, "max_sec": 20.0},
        {"name": "intra_0p7_30s", "threshold_pct": 0.7, "max_sec": 30.0},
    ],
    "step_entries": ["step1", "step2"],
    "pairs": base.PAIRS,
}


@dataclass(slots=True)
class EntryEvent:
    pair: str
    pattern: str
    entry_mode: str
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
    return base.safe_float(value)


def average(values: Iterable[float]) -> float:
    return base.average(values)


def percentile(values: Iterable[float], q: float) -> float:
    return base.percentile(values, q)


def pct(part: int | float, total: int | float) -> float:
    return base.pct(part, total)


def fmt(value: Any, suffix: str = "") -> str:
    return base.fmt(value, suffix)


def directional_return(entry: float, price: float, direction: str) -> float:
    return base.directional_return(entry, price, direction)


def slipped_entry(price: float, direction: str) -> float:
    return base.slipped_entry(price, direction, CONFIG["entry_slippage_pct"])


def log(message: str) -> None:
    print(message)
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def to_entry_event(event: base.Event, mode: str, price: float | None = None, ts_ms: int | None = None) -> EntryEvent:
    return EntryEvent(
        pair=event.pair,
        pattern=event.pattern,
        entry_mode=mode,
        minute_ms=event.minute_ms,
        ts=event.ts,
        date=event.date,
        hour=event.hour,
        direction=event.direction,
        entry_price_raw=event.entry_price_raw if price is None else price,
        entry_ts_ms=event.entry_ts_ms if ts_ms is None else ts_ms,
        signal_size_pct=event.signal_size_pct,
        repeated_5m=event.repeated_5m,
        meta=dict(event.meta),
    )


def impulse_stop_price(event: EntryEvent, signal_bar: base.Bar, buffer_pct: float) -> float:
    if event.direction == "long":
        return signal_bar.low * (1 - buffer_pct / 100)
    return signal_bar.high * (1 + buffer_pct / 100)


def ticks_for_event(ticks: dict[int, list[tuple[int, float]]], entry_ts_ms: int) -> list[tuple[int, float]]:
    return base.ticks_for_event(ticks, entry_ts_ms, CONFIG["max_hold_min"])


def stop_hit(price: float, stop: float, direction: str) -> bool:
    return price <= stop if direction == "long" else price >= stop


def find_structure_exit(
    event: EntryEvent,
    bars_by_minute: dict[int, base.Bar],
    event_ticks: list[tuple[int, float]],
    k: int,
    initial_stop: float,
) -> dict[str, Any]:
    entry = slipped_entry(event.entry_price_raw, event.direction)
    best = 0.0
    worst = 0.0
    last_price = event.entry_price_raw
    exit_ts_ms = event.entry_ts_ms + CONFIG["max_hold_min"] * 60000
    completed: list[base.Bar] = []
    checked_minutes: set[int] = set()
    significant_mfe_seen = False
    outcome = "timeout"
    gross = 0.0
    level = initial_stop

    def check_closed_bars(up_to_minute: int) -> tuple[bool, int | None, float | None]:
        nonlocal level
        minute = event.minute_ms + 60000
        while minute <= up_to_minute:
            if minute not in checked_minutes:
                checked_minutes.add(minute)
                bar = bars_by_minute.get(minute)
                if bar is not None:
                    if len(completed) >= k:
                        window = completed[-k:]
                        level = min(b.low for b in window) if event.direction == "long" else max(b.high for b in window)
                        broken = bar.close < level if event.direction == "long" else bar.close > level
                        if broken:
                            return True, bar.close_ts_ms, bar.close
                    completed.append(bar)
            minute += 60000
        return False, None, None

    for ts_ms, price in event_ticks:
        current_minute = ts_ms - ts_ms % 60000
        broken, break_ts, break_price = check_closed_bars(current_minute - 60000)
        if broken and break_ts is not None and break_price is not None:
            outcome = "structure_break"
            gross = directional_return(entry, break_price, event.direction)
            exit_ts_ms = break_ts
            break

        last_price = price
        ret = directional_return(entry, price, event.direction)
        best = max(best, ret)
        worst = min(worst, ret)
        significant_mfe_seen = significant_mfe_seen or best >= 0.7
        if stop_hit(price, initial_stop, event.direction):
            outcome = "impulse_stop"
            gross = directional_return(entry, initial_stop, event.direction)
            exit_ts_ms = ts_ms
            break
    else:
        gross = directional_return(entry, last_price, event.direction)

    stopped_before_mfe = outcome == "impulse_stop" and not significant_mfe_seen
    return {
        "mode": f"structure_k{k}",
        "outcome": outcome,
        "gross_pct": gross,
        "net_pct": gross - CONFIG["fee_pct"],
        "mfe_pct": best,
        "mae_pct": worst,
        "capture_ratio": gross / best if best > 0 else float("nan"),
        "exit_ts_ms": exit_ts_ms,
        "stopped_before_mfe": stopped_before_mfe,
        "last_level": level,
    }


def find_giveback_exit(
    event: EntryEvent,
    event_ticks: list[tuple[int, float]],
    giveback_pct: int,
    initial_stop: float,
) -> dict[str, Any]:
    entry = slipped_entry(event.entry_price_raw, event.direction)
    best = 0.0
    worst = 0.0
    last_price = event.entry_price_raw
    exit_ts_ms = event.entry_ts_ms + CONFIG["max_hold_min"] * 60000
    significant_mfe_seen = False
    outcome = "timeout"
    gross = 0.0
    activation = CONFIG["giveback_activation_mfe_pct"]

    for ts_ms, price in event_ticks:
        last_price = price
        ret = directional_return(entry, price, event.direction)
        best = max(best, ret)
        worst = min(worst, ret)
        significant_mfe_seen = significant_mfe_seen or best >= 0.7
        if stop_hit(price, initial_stop, event.direction):
            outcome = "impulse_stop"
            gross = directional_return(entry, initial_stop, event.direction)
            exit_ts_ms = ts_ms
            break
        if best >= activation:
            floor_ret = best * (1 - giveback_pct / 100)
            if ret <= floor_ret:
                outcome = "giveback"
                gross = ret
                exit_ts_ms = ts_ms
                break
    else:
        gross = directional_return(entry, last_price, event.direction)

    stopped_before_mfe = outcome == "impulse_stop" and not significant_mfe_seen
    return {
        "mode": f"giveback_{giveback_pct}",
        "outcome": outcome,
        "gross_pct": gross,
        "net_pct": gross - CONFIG["fee_pct"],
        "mfe_pct": best,
        "mae_pct": worst,
        "capture_ratio": gross / best if best > 0 else float("nan"),
        "exit_ts_ms": exit_ts_ms,
        "stopped_before_mfe": stopped_before_mfe,
    }


def round1_trail(event: EntryEvent, bars_by_minute: dict[int, base.Bar], event_ticks: list[tuple[int, float]]) -> dict[str, Any]:
    base_event = base.Event(
        pair=event.pair,
        pattern=event.pattern,
        minute_ms=event.minute_ms,
        ts=event.ts,
        date=event.date,
        hour=event.hour,
        direction=event.direction,
        entry_price_raw=event.entry_price_raw,
        entry_ts_ms=event.entry_ts_ms,
        signal_size_pct=event.signal_size_pct,
        repeated_5m=event.repeated_5m,
        meta=event.meta,
    )
    sim = base.simulate_exit(
        base_event,
        bars_by_minute,
        event_ticks,
        "trail",
        0.8,
        None,
        CONFIG["max_hold_min"],
        CONFIG["entry_slippage_pct"],
        0.15,
    )
    return {
        "mode": "round1_tight_trail",
        "outcome": sim["outcome"],
        "gross_pct": sim["gross_pct"],
        "net_pct": sim["net_pct"],
        "mfe_pct": sim["mfe_pct"],
        "mae_pct": sim["mae_pct"],
        "capture_ratio": sim["capture_ratio"],
        "exit_ts_ms": sim["exit_ts_ms"],
        "stopped_before_mfe": sim["outcome"] in {"sl", "trail"} and sim["mfe_pct"] < 0.7,
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
        "stopped_before_mfe": 0,
        "outcomes": Counter(),
    }


def update_accum(acc: dict[str, Any], sim: dict[str, Any]) -> None:
    net = safe_float(sim["net_pct"])
    gross = safe_float(sim["gross_pct"])
    mfe = safe_float(sim["mfe_pct"])
    capture = safe_float(sim["capture_ratio"])
    acc["n"] += 1
    acc["gross_sum"] += gross
    acc["net_sum"] += net
    acc["net_values"].append(net)
    acc["net_wins"] += 1 if net > 0 else 0
    acc["mfe_sum"] += mfe
    if math.isfinite(capture):
        acc["capture_sum"] += capture
        acc["capture_n"] += 1
    acc["stopped_before_mfe"] += 1 if sim.get("stopped_before_mfe") else 0
    acc["outcomes"][sim["outcome"]] += 1


def finalize(acc: dict[str, Any]) -> dict[str, Any]:
    n = acc["n"]
    vals = acc["net_values"]
    outcomes = acc["outcomes"]
    return {
        "n": n,
        "avg_gross_pct": acc["gross_sum"] / n if n else float("nan"),
        "avg_net_pct": acc["net_sum"] / n if n else float("nan"),
        "median_net_pct": median(vals) if vals else None,
        "net_win_rate": pct(acc["net_wins"], n),
        "avg_mfe_pct": acc["mfe_sum"] / n if n else float("nan"),
        "capture_ratio_avg": acc["capture_sum"] / acc["capture_n"] if acc["capture_n"] else float("nan"),
        "stopped_before_mfe_rate": pct(acc["stopped_before_mfe"], n),
        "outcomes": dict(outcomes),
    }


def find_intra_entry(event: base.Event, signal_bar: base.Bar, signal_ticks: list[tuple[int, float]], trigger: dict[str, Any]) -> EntryEvent | None:
    threshold = float(trigger["threshold_pct"])
    max_elapsed_ms = float(trigger["max_sec"]) * 1000
    for ts_ms, price in signal_ticks:
        elapsed = ts_ms - signal_bar.minute_ms
        if elapsed < 0 or elapsed > max_elapsed_ms:
            continue
        move = (price - signal_bar.open) / signal_bar.open * 100
        if event.direction == "long" and move >= threshold:
            return to_entry_event(event, trigger["name"], price=price, ts_ms=ts_ms)
        if event.direction == "short" and move <= -threshold:
            return to_entry_event(event, trigger["name"], price=price, ts_ms=ts_ms)
    return None


def staircase_step_events(event: base.Event, bars_by_minute: dict[int, base.Bar]) -> list[EntryEvent]:
    if event.pattern != "staircase":
        return []
    window = int(event.meta.get("window", 3))
    start_minute = event.minute_ms - (window - 1) * 60000
    out: list[EntryEvent] = []
    for idx, mode in enumerate(CONFIG["step_entries"]):
        bar = bars_by_minute.get(start_minute + idx * 60000)
        if bar is None:
            continue
        out.append(to_entry_event(event, mode, price=bar.close, ts_ms=bar.close_ts_ms))
    return out


def simulate_event_set(
    pair: str,
    bars: list[base.Bar],
    ticks: dict[int, list[tuple[int, float]]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    bars_by_minute = {b.minute_ms: b for b in bars}
    base_events = base.detect_single_impulse(pair, bars) + base.detect_staircase(pair, bars)
    accum: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(empty_accum)
    entry_accum: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(empty_accum)
    case_rows: list[dict[str, Any]] = []

    for idx, original in enumerate(base_events, start=1):
        if idx % 100 == 0:
            log(f"{pair}: v2 simulated {idx}/{len(base_events)} base events")
        signal_bar = bars_by_minute.get(original.minute_ms)
        if signal_bar is None:
            continue

        close_event = to_entry_event(original, "close")
        entry_events = [close_event]
        signal_ticks = ticks.get(original.minute_ms, [])
        for trigger in CONFIG["intra_triggers"]:
            intra = find_intra_entry(original, signal_bar, signal_ticks, trigger)
            if intra is not None:
                entry_events.append(intra)
        entry_events.extend(staircase_step_events(original, bars_by_minute))

        for entry_event in entry_events:
            event_ticks = ticks_for_event(ticks, entry_event.entry_ts_ms)
            if not event_ticks:
                continue
            for buffer_pct in CONFIG["impulse_stop_buffers_pct"]:
                initial_stop = impulse_stop_price(entry_event, signal_bar, buffer_pct)
                sims = [round1_trail(entry_event, bars_by_minute, event_ticks)]
                for k in CONFIG["structure_break_k"]:
                    sims.append(find_structure_exit(entry_event, bars_by_minute, event_ticks, k, initial_stop))
                for giveback in CONFIG["giveback_pct"]:
                    sims.append(find_giveback_exit(entry_event, event_ticks, giveback, initial_stop))

                for sim in sims:
                    regime = "cluster_2plus" if entry_event.repeated_5m >= 2 else "all_noncluster"
                    keys = [
                        (entry_event.pattern, entry_event.entry_mode, sim["mode"], buffer_pct, "all"),
                        (entry_event.pattern, entry_event.entry_mode, sim["mode"], buffer_pct, regime),
                    ]
                    for key in keys:
                        update_accum(accum[key], sim)
                    if entry_event.entry_mode != "close":
                        update_accum(entry_accum[(entry_event.pattern, entry_event.entry_mode, sim["mode"], buffer_pct)], sim)

                if entry_event.entry_mode == "close" and buffer_pct == 0.1:
                    best_sim = max(sims, key=lambda item: safe_float(item["net_pct"]))
                    case_rows.append(
                        {
                            "event": entry_event,
                            "signal_bar": signal_bar,
                            "sim": best_sim,
                            "all_sims": sims,
                        }
                    )

    summaries: dict[str, dict[str, Any]] = {}
    for key, acc in accum.items():
        pattern, entry_mode, mode, buffer_pct, regime = key
        name = f"{pattern}|{entry_mode}|{mode}|buffer={buffer_pct}|{regime}"
        summaries[name] = {
            "pair": pair,
            "pattern": pattern,
            "entry_mode": entry_mode,
            "mode": mode,
            "buffer_pct": buffer_pct,
            "regime": regime,
            **finalize(acc),
        }

    entry_summaries: list[dict[str, Any]] = []
    for key, acc in entry_accum.items():
        pattern, entry_mode, mode, buffer_pct = key
        entry_summaries.append(
            {
                "pair": pair,
                "pattern": pattern,
                "entry_mode": entry_mode,
                "mode": mode,
                "buffer_pct": buffer_pct,
                **finalize(acc),
            }
        )
    return summaries, entry_summaries, case_rows


def analyze_pair(pair: str) -> dict[str, Any]:
    log(f"v2 analyze {pair}")
    bars, ticks = base.aggregate_pair(pair, keep_ticks=True)
    if not bars:
        return {"pair": pair, "error": "no bars"}
    summaries, entry_summaries, cases = simulate_event_set(pair, bars, ticks)
    return {
        "pair": pair,
        "days": len({b.date for b in bars}),
        "summaries": summaries,
        "entry_summaries": entry_summaries,
        "case_rows": cases,
    }


def all_summary_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        if result.get("error"):
            continue
        rows.extend(result["summaries"].values())
    return rows


def render_report(results: list[dict[str, Any]]) -> str:
    rows = [r for r in all_summary_rows(results) if r["entry_mode"] == "close" and r["regime"] == "all" and r["n"] >= 20]
    rows.sort(key=lambda r: safe_float(r["avg_net_pct"]), reverse=True)
    lines = [
        "# Continuation Structural Exit V2 - 20.05.2026",
        "",
        "V2 changes tested together: impulse-candle stop, loose structural exits, and cluster regime split. Net includes 0.20% taker round trip and 0.05% entry slippage.",
        "",
        "## Top Close-Entry Rows, Full Sample",
        "",
        "| rank | pair | pattern | mode | buffer | n | avg net | med net | win net | avg MFE | capture | stopped_before_mfe |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for idx, row in enumerate(rows[:40], start=1):
        portfolio_capture = (
            safe_float(row["avg_gross_pct"]) / safe_float(row["avg_mfe_pct"])
            if safe_float(row["avg_mfe_pct"]) > 0
            else float("nan")
        )
        lines.append(
            f"| {idx} | {row['pair']} | {row['pattern']} | {row['mode']} | {fmt(row['buffer_pct'], '%')} | {row['n']} | "
            f"**{fmt(row['avg_net_pct'], '%')}** | {fmt(row['median_net_pct'], '%')} | {fmt(row['net_win_rate'], '%')} | "
            f"{fmt(row['avg_mfe_pct'], '%')} | {fmt(portfolio_capture * 100 if math.isfinite(portfolio_capture) else None, '%')} | "
            f"{fmt(row['stopped_before_mfe_rate'], '%')} |"
        )

    lines.extend(
        [
            "",
            "## Cluster Delta",
            "",
            "| pair | pattern | mode | buffer | all net | cluster net | delta | all n | cluster n |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    by_key: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in all_summary_rows(results):
        if row["entry_mode"] != "close":
            continue
        key = (row["pair"], row["pattern"], row["mode"], row["buffer_pct"])
        by_key[key][row["regime"]] = row
    delta_rows = []
    for key, payload in by_key.items():
        all_row = payload.get("all")
        cluster_row = payload.get("cluster_2plus")
        if not all_row or not cluster_row or cluster_row["n"] < 20:
            continue
        delta = safe_float(cluster_row["avg_net_pct"]) - safe_float(all_row["avg_net_pct"])
        delta_rows.append((delta, key, all_row, cluster_row))
    delta_rows.sort(key=lambda item: item[0], reverse=True)
    for delta, key, all_row, cluster_row in delta_rows[:30]:
        pair, pattern, mode, buffer_pct = key
        lines.append(
            f"| {pair} | {pattern} | {mode} | {fmt(buffer_pct, '%')} | {fmt(all_row['avg_net_pct'], '%')} | "
            f"{fmt(cluster_row['avg_net_pct'], '%')} | **{fmt(delta, '%')}** | {all_row['n']} | {cluster_row['n']} |"
        )

    positives = [r for r in rows if safe_float(r["avg_net_pct"]) > 0]
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            f"Positive full-sample close-entry rows with n>=20: `{len(positives)}`.",
            "If the positive rows are absent or only appear in small cluster buckets, do not change `config.yaml`; use them only as paper candidates.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_entry_report(results: list[dict[str, Any]]) -> str:
    rows = []
    for result in results:
        if result.get("error"):
            continue
        rows.extend([r for r in result["entry_summaries"] if r["n"] >= 20])
    rows.sort(key=lambda r: safe_float(r["avg_net_pct"]), reverse=True)
    lines = [
        "# Continuation Entry Timing V2 - 20.05.2026",
        "",
        "Early entries are compared with the same v2 exit modes. This specifically checks whether cases 9/12 were late-entry failures.",
        "",
        "| rank | pair | pattern | entry | mode | buffer | n | avg net | med net | win net | avg MFE | capture |",
        "| ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for idx, row in enumerate(rows[:50], start=1):
        portfolio_capture = (
            safe_float(row["avg_gross_pct"]) / safe_float(row["avg_mfe_pct"])
            if safe_float(row["avg_mfe_pct"]) > 0
            else float("nan")
        )
        lines.append(
            f"| {idx} | {row['pair']} | {row['pattern']} | {row['entry_mode']} | {row['mode']} | {fmt(row['buffer_pct'], '%')} | "
            f"{row['n']} | **{fmt(row['avg_net_pct'], '%')}** | {fmt(row['median_net_pct'], '%')} | {fmt(row['net_win_rate'], '%')} | "
            f"{fmt(row['avg_mfe_pct'], '%')} | {fmt(portfolio_capture * 100 if math.isfinite(portfolio_capture) else None, '%')} |"
        )
    positives = [r for r in rows if safe_float(r["avg_net_pct"]) > 0]
    lines.extend(["", "## Conclusion", "", f"Positive early-entry rows with n>=20: `{len(positives)}`.", "If early entries improve MFE but remain net-negative, the issue is still capture/fee rather than only late signal formation."])
    return "\n".join(lines) + "\n"


def render_hypotheses(results: list[dict[str, Any]]) -> str:
    rows = all_summary_rows(results)
    close_rows = [r for r in rows if r["entry_mode"] == "close" and r["regime"] == "all" and r["n"] >= 20]
    by_mode = defaultdict(list)
    by_buffer = defaultdict(list)
    for row in close_rows:
        by_mode[row["mode"]].append(row)
        by_buffer[row["buffer_pct"]].append(row)

    def agg(items: list[dict[str, Any]]) -> dict[str, Any]:
        captures = [
            safe_float(r["avg_gross_pct"]) / safe_float(r["avg_mfe_pct"])
            for r in items
            if safe_float(r["avg_mfe_pct"]) > 0
        ]
        return {
            "n_rows": len(items),
            "best": max((safe_float(r["avg_net_pct"]) for r in items), default=float("nan")),
            "avg": average(safe_float(r["avg_net_pct"]) for r in items),
            "best_capture": max(captures, default=float("nan")),
            "avg_stopped": average(safe_float(r["stopped_before_mfe_rate"]) for r in items),
        }

    lines = [
        "# GPT Hypotheses V2 - 20.05.2026",
        "",
        "Focus: whether wider impulse stop and looser exits fix the round-1 tight-trail problem.",
        "",
        "## Exit Mode Families",
        "",
        "| mode | rows | best net | avg net | best portfolio capture | avg stopped_before_mfe |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in sorted(by_mode):
        s = agg(by_mode[mode])
        lines.append(f"| {mode} | {s['n_rows']} | {fmt(s['best'], '%')} | {fmt(s['avg'], '%')} | {fmt(s['best_capture'] * 100 if math.isfinite(safe_float(s['best_capture'])) else None, '%')} | {fmt(s['avg_stopped'], '%')} |")

    lines.extend(["", "## Impulse Stop Buffer", "", "| buffer | rows | best net | avg net | best portfolio capture | avg stopped_before_mfe |", "| ---: | ---: | ---: | ---: | ---: | ---: |"])
    for buffer_pct in sorted(by_buffer):
        s = agg(by_buffer[buffer_pct])
        lines.append(f"| {fmt(buffer_pct, '%')} | {s['n_rows']} | {fmt(s['best'], '%')} | {fmt(s['avg'], '%')} | {fmt(s['best_capture'] * 100 if math.isfinite(safe_float(s['best_capture'])) else None, '%')} | {fmt(s['avg_stopped'], '%')} |")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `stopped_before_mfe_rate` tests the case-13 diagnosis directly: if it falls but net stays negative, the wider stop fixed shakeout but increased loss size or still failed capture.",
            "- Portfolio capture (`avg_gross / avg_MFE`) is the core metric for tail capture. Positive MFE without capture is not tradable edge.",
            "- Cluster rows in the main report show whether the 2+ explosions regime is actually executable, not just visually attractive.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_case_filename(path: Path) -> tuple[str, str, str] | None:
    name = path.stem
    parts = name.split("_")
    if len(parts) < 5:
        return None
    pair = parts[2]
    if parts[3] == "single" and parts[4] == "impulse":
        pattern = "single_impulse"
        ts_token = parts[5]
    elif parts[3] == "staircase":
        pattern = "staircase"
        ts_token = parts[4]
    else:
        return None
    return pair, pattern, ts_token


def token_from_event(event: EntryEvent) -> str:
    return event.ts.replace("-", "").replace(":", "").replace("Z", "")


def plot_case(path: Path, bars: list[base.Bar], case: dict[str, Any]) -> None:
    event: EntryEvent = case["event"]
    sim = case["sim"]
    subset = [b for b in bars if event.minute_ms - 10 * 60000 <= b.minute_ms <= event.minute_ms + 20 * 60000]
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
    ax.axhline(event.entry_price_raw, color="#225ea8", linestyle="--", linewidth=1, label="entry")
    exit_idx = idx_by_minute.get(sim["exit_ts_ms"] - sim["exit_ts_ms"] % 60000)
    if exit_idx is not None:
        ax.axvline(exit_idx, color="#111111", linestyle=":", linewidth=1, label=sim["mode"])
    ax.set_title(f"{event.pair} {event.pattern} {event.direction} {event.ts} | {sim['mode']} net {sim['net_pct']:.2f}% MFE {sim['mfe_pct']:.2f}%")
    ax.set_xticks(x[::5])
    ax.set_xticklabels([subset[i].ts[11:16] for i in x[::5]])
    ax.grid(alpha=0.2)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_same_cases(results: list[dict[str, Any]]) -> None:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    old_case_dir = OUT_DIR / "continuation_cases_20_05_2026"
    wanted = []
    for old_path in sorted(old_case_dir.glob("*.png")):
        parsed = parse_case_filename(old_path)
        if parsed:
            wanted.append((old_path.name, *parsed))

    case_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for result in results:
        for case in result.get("case_rows", []):
            event = case["event"]
            case_index[(event.pair, event.pattern, token_from_event(event))] = case

    bars_cache: dict[str, list[base.Bar]] = {}
    for old_name, pair, pattern, ts_token in wanted:
        case = case_index.get((pair, pattern, ts_token))
        if case is None:
            continue
        if pair not in bars_cache:
            bars_cache[pair], _ = base.aggregate_pair(pair, keep_ticks=False)
        plot_case(CASE_DIR / old_name.replace(".png", "_v2.png"), bars_cache[pair], case)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")
    results = []
    for pair in CONFIG["pairs"]:
        if not (base.TICK_ROOT / pair).exists():
            results.append({"pair": pair, "error": "missing pair dir"})
            continue
        results.append(analyze_pair(pair))

    serializable = []
    for result in results:
        serializable.append({k: v for k, v in result.items() if k != "case_rows"})
    SUMMARY_JSON.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "config": CONFIG,
                "pairs": serializable,
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    REPORT_MD.write_text(render_report(results), encoding="utf-8")
    ENTRY_MD.write_text(render_entry_report(results), encoding="utf-8")
    HYP_MD.write_text(render_hypotheses(results), encoding="utf-8")
    generate_same_cases(results)
    log(f"saved {REPORT_MD}")
    log(f"saved {ENTRY_MD}")
    log(f"saved {HYP_MD}")
    log(f"saved {SUMMARY_JSON}")
    log(f"saved cases to {CASE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
