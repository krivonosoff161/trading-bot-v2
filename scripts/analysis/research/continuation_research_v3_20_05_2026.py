from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, pstdev
from typing import Any, Iterable

import continuation_research_20_05_2026 as base
import continuation_research_v2_20_05_2026 as v2


ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
SIDES_MD = OUT_DIR / "continuation_sidesplit_v3_20_05_2026.md"
MODEL_MD = OUT_DIR / "continuation_model_v3_20_05_2026.md"
SUMMARY_JSON = OUT_DIR / "continuation_summary_v3_20_05_2026.json"
RUN_LOG = OUT_DIR / "continuation_run_v3_20_05_2026.log"

CONFIG = {
    "fee_pct": 0.20,
    "entry_slippage_pct": 0.05,
    "max_hold_min": 20,
    "pairs": base.PAIRS,
    "impulse_stop_buffers_pct": [0.0, 0.1, 0.2],
    "structure_break_k": [1, 2, 3],
    "giveback_pct": [30, 40, 50],
    "intra_triggers": [
        {"name": "intra_0p3_10s", "threshold_pct": 0.3, "max_sec": 10.0},
        {"name": "intra_0p5_20s", "threshold_pct": 0.5, "max_sec": 20.0},
        {"name": "intra_0p7_30s", "threshold_pct": 0.7, "max_sec": 30.0},
    ],
    "step_entries": ["step1", "step2"],
    "base_model": {
        "pattern": "single_impulse",
        "entry_mode": "intra_0p3_10s",
        "exit_mode": "structure_k1",
        "buffer_pct": 0.1,
        "cluster_min_prior_5m": 2,
        "min_body_ratio_prev4": 1.5,
    },
}


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


def log(message: str) -> None:
    print(message)
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def body_pct(bar: base.Bar) -> float:
    return abs((bar.close - bar.open) / bar.open * 100) if bar.open > 0 else float("nan")


def body_ratio_prev4(event: base.Event, bars_by_minute: dict[int, base.Bar]) -> float:
    prev = []
    for i in range(1, 5):
        bar = bars_by_minute.get(event.minute_ms - i * 60000)
        if bar is not None:
            prev.append(body_pct(bar))
    avg_prev = average(prev)
    return body_pct(bars_by_minute[event.minute_ms]) / avg_prev if avg_prev > 0 else float("nan")


def empty_accum() -> dict[str, Any]:
    return {
        "n": 0,
        "gross": [],
        "net": [],
        "wins": [],
        "losses": [],
        "mfe": [],
        "outcomes": Counter(),
    }


def update_accum(acc: dict[str, Any], sim: dict[str, Any]) -> None:
    gross = safe_float(sim["gross_pct"])
    net = safe_float(sim["net_pct"])
    acc["n"] += 1
    acc["gross"].append(gross)
    acc["net"].append(net)
    acc["mfe"].append(safe_float(sim["mfe_pct"]))
    if net > 0:
        acc["wins"].append(net)
    else:
        acc["losses"].append(abs(net))
    acc["outcomes"][sim["outcome"]] += 1


def finalize(acc: dict[str, Any]) -> dict[str, Any]:
    nets = [v for v in acc["net"] if math.isfinite(v)]
    gross = [v for v in acc["gross"] if math.isfinite(v)]
    wins = [v for v in acc["wins"] if math.isfinite(v)]
    losses = [v for v in acc["losses"] if math.isfinite(v)]
    mfes = [v for v in acc["mfe"] if math.isfinite(v)]
    n = len(nets)
    p_win = len(wins) / n if n else 0.0
    p_loss = len(losses) / n if n else 0.0
    avg_win = average(wins)
    avg_loss = average(losses)
    return {
        "n": n,
        "avg_gross_pct": average(gross),
        "avg_net_pct": average(nets),
        "median_net_pct": median(nets) if nets else None,
        "std_net_pct": pstdev(nets) if len(nets) > 1 else 0.0 if nets else float("nan"),
        "iqr_net_pct": percentile(nets, 75) - percentile(nets, 25) if nets else float("nan"),
        "win_rate": pct(len(wins), n),
        "p_win": p_win,
        "avg_win_pct": avg_win,
        "p_loss": p_loss,
        "avg_loss_pct": avg_loss,
        "ev_formula_pct": p_win * avg_win - p_loss * avg_loss if n else float("nan"),
        "avg_mfe_pct": average(mfes),
        "outcomes": dict(acc["outcomes"]),
    }


def event_entries(original: base.Event, signal_bar: base.Bar, bars_by_minute: dict[int, base.Bar], ticks: dict[int, list[tuple[int, float]]]) -> list[v2.EntryEvent]:
    entries = [v2.to_entry_event(original, "close")]
    signal_ticks = ticks.get(original.minute_ms, [])
    for trigger in CONFIG["intra_triggers"]:
        entry = v2.find_intra_entry(original, signal_bar, signal_ticks, trigger)
        if entry is not None:
            entries.append(entry)
    entries.extend(v2.staircase_step_events(original, bars_by_minute))
    return entries


def simulate_all_for_entry(entry: v2.EntryEvent, signal_bar: base.Bar, bars_by_minute: dict[int, base.Bar], ticks: dict[int, list[tuple[int, float]]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    event_ticks = v2.ticks_for_event(ticks, entry.entry_ts_ms)
    if not event_ticks:
        return []
    out = []
    for buffer_pct in CONFIG["impulse_stop_buffers_pct"]:
        initial_stop = v2.impulse_stop_price(entry, signal_bar, buffer_pct)
        sims = [v2.round1_trail(entry, bars_by_minute, event_ticks)]
        for k in CONFIG["structure_break_k"]:
            sims.append(v2.find_structure_exit(entry, bars_by_minute, event_ticks, k, initial_stop))
        for giveback in CONFIG["giveback_pct"]:
            sims.append(v2.find_giveback_exit(entry, event_ticks, giveback, initial_stop))
        for sim in sims:
            out.append(({"buffer_pct": buffer_pct, "mode": sim["mode"]}, sim))
    return out


def is_base_model_event(original: base.Event, entry: v2.EntryEvent, sim_meta: dict[str, Any], sim: dict[str, Any], body_ratio: float) -> bool:
    cfg = CONFIG["base_model"]
    return (
        original.pattern == cfg["pattern"]
        and entry.entry_mode == cfg["entry_mode"]
        and sim["mode"] == cfg["exit_mode"]
        and abs(sim_meta["buffer_pct"] - cfg["buffer_pct"]) < 1e-9
        and original.repeated_5m >= cfg["cluster_min_prior_5m"]
        and math.isfinite(body_ratio)
        and body_ratio >= cfg["min_body_ratio_prev4"]
    )


def half_label(event: base.Event, dates_sorted: list[str]) -> str:
    if not dates_sorted:
        return "unknown"
    midpoint = len(dates_sorted) // 2
    late_dates = set(dates_sorted[midpoint:])
    return "late" if event.date in late_dates else "early"


def analyze_pair(pair: str) -> dict[str, Any]:
    log(f"v3 analyze {pair}")
    bars, ticks = base.aggregate_pair(pair, keep_ticks=True)
    if not bars:
        return {"pair": pair, "error": "no bars"}
    bars_by_minute = {b.minute_ms: b for b in bars}
    dates = sorted({b.date for b in bars})
    originals = base.detect_single_impulse(pair, bars) + base.detect_staircase(pair, bars)

    side_accum: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(empty_accum)
    base_accum: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(empty_accum)

    for idx, original in enumerate(originals, start=1):
        if idx % 200 == 0:
            log(f"{pair}: v3 simulated {idx}/{len(originals)}")
        signal_bar = bars_by_minute.get(original.minute_ms)
        if signal_bar is None:
            continue
        bratio = body_ratio_prev4(original, bars_by_minute)
        for entry in event_entries(original, signal_bar, bars_by_minute, ticks):
            sims = simulate_all_for_entry(entry, signal_bar, bars_by_minute, ticks)
            for sim_meta, sim in sims:
                regime = "cluster_2plus" if entry.repeated_5m >= 2 else "all_noncluster"
                key = (
                    pair,
                    original.pattern,
                    entry.entry_mode,
                    sim["mode"],
                    sim_meta["buffer_pct"],
                    regime,
                    entry.direction,
                )
                update_accum(side_accum[key], sim)
                all_key = (
                    pair,
                    original.pattern,
                    entry.entry_mode,
                    sim["mode"],
                    sim_meta["buffer_pct"],
                    "all",
                    entry.direction,
                )
                update_accum(side_accum[all_key], sim)
                if is_base_model_event(original, entry, sim_meta, sim, bratio):
                    for dim in [
                        ("all", entry.direction),
                        (half_label(original, dates), entry.direction),
                        ("all", "both"),
                        (half_label(original, dates), "both"),
                    ]:
                        update_accum(base_accum[dim], sim)

    side_rows = []
    for key, acc in side_accum.items():
        pair_name, pattern, entry_mode, mode, buffer_pct, regime, direction = key
        side_rows.append(
            {
                "pair": pair_name,
                "pattern": pattern,
                "entry_mode": entry_mode,
                "mode": mode,
                "buffer_pct": buffer_pct,
                "regime": regime,
                "direction": direction,
                **finalize(acc),
            }
        )

    base_rows = []
    for key, acc in base_accum.items():
        period, direction = key
        base_rows.append({"pair": pair, "period": period, "direction": direction, **finalize(acc)})

    return {"pair": pair, "days": len(dates), "side_rows": side_rows, "base_rows": base_rows}


def combine_side_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for result in results:
        if result.get("error"):
            continue
        out.extend(result["side_rows"])
    return out


def side_pair_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        if row["regime"] != "all":
            continue
        key = (row["pair"], row["pattern"], row["entry_mode"], row["mode"], row["buffer_pct"])
        grouped[key][row["direction"]] = row
    out = []
    for key, sides in grouped.items():
        long = sides.get("long")
        short = sides.get("short")
        if not long or not short:
            continue
        pair, pattern, entry_mode, mode, buffer_pct = key
        out.append(
            {
                "pair": pair,
                "pattern": pattern,
                "entry_mode": entry_mode,
                "mode": mode,
                "buffer_pct": buffer_pct,
                "n_long": long["n"],
                "net_long": long["avg_net_pct"],
                "win_long": long["win_rate"],
                "n_short": short["n"],
                "net_short": short["avg_net_pct"],
                "win_short": short["win_rate"],
                "both_positive": long["n"] >= 20 and short["n"] >= 20 and long["avg_net_pct"] > 0 and short["avg_net_pct"] > 0,
            }
        )
    out.sort(key=lambda r: min(safe_float(r["net_long"]), safe_float(r["net_short"])), reverse=True)
    return out


def render_sides(rows: list[dict[str, Any]]) -> str:
    pairs = side_pair_table(rows)
    both = [r for r in pairs if r["both_positive"]]
    lines = [
        "# Continuation Side Split V3 - 20.05.2026",
        "",
        "All V2-style combinations are split by direction. Rows below require both long and short side to exist; `both_positive` requires `n >= 20` per side and positive avg net on both sides.",
        "",
        f"- both-side positive rows: `{len(both)}`",
        "",
        "## Best Long/Short Balanced Rows",
        "",
        "| pair | pattern | entry | exit | buffer | n long | net long | win long | n short | net short | win short | both+ |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in pairs[:80]:
        lines.append(
            f"| {row['pair']} | {row['pattern']} | {row['entry_mode']} | {row['mode']} | {fmt(row['buffer_pct'], '%')} | "
            f"{row['n_long']} | {fmt(row['net_long'], '%')} | {fmt(row['win_long'], '%')} | "
            f"{row['n_short']} | {fmt(row['net_short'], '%')} | {fmt(row['win_short'], '%')} | "
            f"{'yes' if row['both_positive'] else 'no'} |"
        )
    one_sided = [r for r in pairs if (r["n_long"] >= 20 and r["net_long"] > 0) != (r["n_short"] >= 20 and r["net_short"] > 0)]
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"One-sided positive rows: `{len(one_sided)}`.",
            "If a pair is positive only on one side, treat it as directional window bias, not a robust continuation law.",
        ]
    )
    return "\n".join(lines) + "\n"


def combine_base_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for result in results:
        if result.get("error"):
            continue
        out.extend(result["base_rows"])
    return out


def render_model(results: list[dict[str, Any]], side_rows: list[dict[str, Any]]) -> str:
    base_rows = combine_base_rows(results)
    base_rows.sort(key=lambda r: (r["pair"], r["period"], r["direction"]))
    pair_all = [r for r in base_rows if r["period"] == "all"]
    stable = []
    by_pair = defaultdict(dict)
    for row in base_rows:
        by_pair[(row["pair"], row["direction"])][row["period"]] = row
    for (pair, direction), periods in by_pair.items():
        all_row = periods.get("all")
        early = periods.get("early")
        late = periods.get("late")
        if all_row and early and late and all_row["n"] >= 20 and early["n"] >= 10 and late["n"] >= 10:
            if all_row["avg_net_pct"] > 0 and early["avg_net_pct"] > 0 and late["avg_net_pct"] > 0:
                stable.append((pair, direction, all_row, early, late))

    cfg = CONFIG["base_model"]
    lines = [
        "# Continuation Formal Model V3 - 20.05.2026",
        "",
        "## Fixed Base Model",
        "",
        "This model is fixed from trading logic, not optimized over this run:",
        "",
        f"- Pattern: `{cfg['pattern']}`.",
        "- Signal: 1m impulse candle in continuation direction with `abs(open->close) >= 0.8%`.",
        f"- Body-strength filter: impulse body >= `{cfg['min_body_ratio_prev4']}x` average absolute body of previous 4 completed 1m candles.",
        f"- Regime filter: at least `{cfg['cluster_min_prior_5m']}` prior explosive candles in the last 5 minutes.",
        "- Entry: first tick inside the impulse candle where move from candle open reaches `0.3%` within `10s`; taker slippage `0.05%` applied.",
        f"- Stop: impulse candle extreme +/- `{cfg['buffer_pct']:.1f}%` buffer.",
        "- Exit: structure break `k=1`, meaning a completed candle closes beyond the previous completed bar's structural level.",
        "- Max hold: `20m`; fee: `0.20%` round trip.",
        "",
        "EV decomposition uses realized net values:",
        "",
        "`E[net] = p_win * avg_win - p_loss * avg_loss`",
        "",
        "Fees are already included in net returns.",
        "",
        "## Base Model By Pair / Side / Time",
        "",
        "| pair | period | side | n | avg net | median | std | IQR | win | avg win | avg loss | EV formula | avg MFE |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in base_rows:
        lines.append(
            f"| {row['pair']} | {row['period']} | {row['direction']} | {row['n']} | {fmt(row['avg_net_pct'], '%')} | "
            f"{fmt(row['median_net_pct'], '%')} | {fmt(row['std_net_pct'], '%')} | {fmt(row['iqr_net_pct'], '%')} | "
            f"{fmt(row['win_rate'], '%')} | {fmt(row['avg_win_pct'], '%')} | {fmt(row['avg_loss_pct'], '%')} | "
            f"{fmt(row['ev_formula_pct'], '%')} | {fmt(row['avg_mfe_pct'], '%')} |"
        )

    lines.extend(
        [
            "",
            "## Stability Verdict",
            "",
            f"Stable positive pair/side/time cells: `{len(stable)}`.",
        ]
    )
    if stable:
        for pair, direction, all_row, early, late in stable:
            lines.append(f"- `{pair}` `{direction}`: all `{fmt(all_row['avg_net_pct'], '%')}`, early `{fmt(early['avg_net_pct'], '%')}`, late `{fmt(late['avg_net_pct'], '%')}`.")
    else:
        lines.append("No base-model cell passed positive all/early/late stability with normal sample.")

    stable_by_pair = defaultdict(set)
    for pair, direction, _, _, _ in stable:
        stable_by_pair[pair].add(direction)
    robust_pairs = sorted(pair for pair, directions in stable_by_pair.items() if {"long", "short"}.issubset(directions))
    both_side_rows = [r for r in side_pair_table(side_rows) if r["both_positive"]]
    lines.extend(
        [
            "",
            "## Base-Model Verdict",
            "",
        ]
    )
    if robust_pairs:
        lines.append(f"A cautious paper base exists on: `{', '.join(robust_pairs)}`. It passed long/short and early/late checks under the fixed model.")
    else:
        lines.append("No robust base model: no pair passed the fixed model with positive long and short sides across both early and late time splits with normal sample. The best BSB/early-entry rows are useful research candidates, not production config.")

    lines.extend(
        [
            "",
            "## GPT Hypotheses",
            "",
            "- The v2 BSB edge is likely a directional micro-regime, not a universal continuation rule, unless long and short both stay positive.",
            "- Cluster + early entry is still the correct direction for research, but the base needs more days or cross-pair confirmation.",
            "- Median below mean implies fat-tail dependence; size/risk should be capped until more sample confirms the tail is repeatable.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")
    results = []
    for pair in CONFIG["pairs"]:
        if not (base.TICK_ROOT / pair).exists():
            results.append({"pair": pair, "error": "missing pair dir"})
            continue
        results.append(analyze_pair(pair))

    side_rows = combine_side_rows(results)
    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": CONFIG,
        "pairs": results,
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    SIDES_MD.write_text(render_sides(side_rows), encoding="utf-8")
    MODEL_MD.write_text(render_model(results, side_rows), encoding="utf-8")
    log(f"saved {SIDES_MD}")
    log(f"saved {MODEL_MD}")
    log(f"saved {SUMMARY_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
