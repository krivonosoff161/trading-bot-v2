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

CONFIG = {
    "case_limit": 10,
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
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    log(f"saved {REPORT_MD}")
    log(f"saved {SUMMARY_JSON}")
    log(f"saved cases to {CASE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
