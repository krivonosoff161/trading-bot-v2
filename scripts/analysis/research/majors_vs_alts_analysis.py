from __future__ import annotations

import copy
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]

import sys

sys.path.insert(0, str(ROOT))

from scripts.analysis.research.main_block1_analysis import load_archive_rows, stats  # noqa: E402
from scripts.backtest.bt_screener_sim import call_signal, load_cache, visible_newest  # noqa: E402
from src.strategy.indicators import calc_adx, calc_slope, parse_candles  # noqa: E402
from src.strategy.signal_engine import _PAIR_PARAMS, _PAIR_PARAMS_DEFAULT, _mode_cfg  # noqa: E402


OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
REPORT_PATH = ROOT / "docs" / "gpt_majors_vs_alts_19_05_2026.md"
DATA_PATH = OUT_DIR / "majors_vs_alts_19_05_2026.json"
CONFIG_PATH = ROOT / "config.yaml"

MAJORS = {"BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT"}
TARGET_BUCKETS = [("TRENDING", "SWING"), ("DRIFT", "FAST"), ("TRENDING", "FAST")]


@dataclass(slots=True)
class ReplayRow:
    baseline: dict[str, Any]
    prefilter_pass: bool
    prefilter_vol_ratio: float
    prefilter_adx: float
    current_result: Any | None
    kept_same_bucket: bool
    reason: str
    details: dict[str, Any]


def read_config() -> tuple[dict[str, Any], dict[str, Any]]:
    payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return dict(payload.get("strategy") or {}), dict(payload.get("main_screener") or {})


def ts_to_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def base_to_inst_id(symbol: str) -> str:
    return symbol if symbol.endswith("-SWAP") else f"{symbol}-SWAP"


def visible_oldest(candles_oldest: list[list[float | int]], close_ts_ms: int) -> list[list[float | int]]:
    return [row for row in candles_oldest if int(row[0]) < close_ts_ms]


def prefilter_check(pair_data: dict[str, list[list[float | int]]], close_ts_ms: int) -> tuple[bool, float, float]:
    candles_15m = visible_oldest(pair_data["15m"], close_ts_ms)
    candles_1h = visible_oldest(pair_data["1H"], close_ts_ms)
    if len(candles_15m) < 20 or len(candles_1h) < 20:
        return False, float("nan"), float("nan")
    current = candles_15m[-1]
    hist_vol = [row[5] for row in candles_15m[-16:-1]]
    vol_ratio = float(current[5]) / mean(hist_vol) if hist_vol and mean(hist_vol) > 0 else 0.0
    newest_first = list(reversed(candles_1h[-30:]))
    highs, lows, closes = parse_candles(newest_first)
    adx_approx = float(calc_adx(highs, lows, closes, period=9, bar_index=-2)[0]) if len(newest_first) >= 20 else 0.0
    passed = not (vol_ratio < MAIN_CFG["prefilter_vol_ratio_min"] and adx_approx < MAIN_CFG["prefilter_adx_min"])
    return passed, vol_ratio, adx_approx


def replay_trade(row: dict[str, Any], cache: dict[str, dict[str, list[list[float | int]]]]) -> ReplayRow:
    inst_id = base_to_inst_id(str(row["symbol"]))
    pair_data = cache.get(inst_id)
    if not pair_data:
        return ReplayRow(row, False, float("nan"), float("nan"), None, False, "missing_cache", {})

    close_ts_ms = ts_to_ms(str(row["ts"]))
    pre_pass, pre_vol, pre_adx = prefilter_check(pair_data, close_ts_ms)
    if not pre_pass:
        return ReplayRow(
            row,
            False,
            pre_vol,
            pre_adx,
            None,
            False,
            "prefilter",
            {"prefilter_vol_ratio": pre_vol, "prefilter_adx": pre_adx},
        )

    result = call_signal(pair_data, str(row["pair"]), close_ts_ms, STRATEGY_CFG)
    if result is None:
        return ReplayRow(row, True, pre_vol, pre_adx, None, False, "compute_none", {})

    same_bucket = (
        result.entry_signal == "ENTRY"
        and result.regime == row["regime"]
        and (result.trade_style or "") == row["style"]
        and result.side == row["side"]
    )
    engine = getattr(result, "engine_vars", {}) or {}
    indicators = getattr(result, "indicators", {}) or {}
    h15 = indicators.get("15m", {}) or {}

    reason = "kept" if same_bucket else "other"
    if not same_bucket:
        if engine.get("regime") == "TRENDING" and float(engine.get("vol_ratio_sig") or 0.0) < float(STRATEGY_CFG.get("min_vol_ratio_trending", 1.5)):
            reason = "min_vol_ratio_trending"
        elif row["regime"] == "TRENDING" and row["style"] == "SWING" and row["side"] == "sell":
            if float(engine.get("rsi_15m") or 50.0) < 25.0 and float(h15.get("bb_pct_b") or 50.0) < 5.0:
                reason = "oversold_short_veto"
        elif result.regime != row["regime"]:
            reason = f"reclassified_regime_{result.regime}"
        elif result.entry_signal == "ENTRY" and (result.trade_style or "") != row["style"]:
            reason = f"reclassified_style_{result.trade_style}"
        elif result.entry_signal == "NO_TRADE" and not result.side:
            sl_cur = float((row.get("slope_1h") if row["style"] == "SWING" else row.get("slope_15m")) or 0.0)
            slope_min = float(STRATEGY_CFG.get("slope_min", 35.0))
            if abs(sl_cur) < slope_min:
                reason = "slope_min"
            else:
                reason = "conditions_not_met"
        else:
            reason = result.drop_reason or "other"

    details = {
        "entry_signal": result.entry_signal,
        "result_regime": result.regime,
        "result_style": result.trade_style,
        "result_side": result.side,
        "drop_reason": result.drop_reason,
        "vol_ratio_sig": float(engine.get("vol_ratio_sig") or 0.0),
        "rsi_15m": float(engine.get("rsi_15m") or 0.0),
        "bb_pct_b_15m": float(h15.get("bb_pct_b") or 0.0),
        "adx_4h": float(engine.get("adx_4h") or 0.0),
        "adx_1h": float(engine.get("adx_1h") or 0.0),
    }
    return ReplayRow(row, True, pre_vol, pre_adx, result, same_bucket, reason, details)


def swing_cfg(symbol: str) -> dict[str, Any]:
    pp = _PAIR_PARAMS.get(symbol, _PAIR_PARAMS_DEFAULT)
    return _mode_cfg(pp, "trending", "swing")


def slope_pair(pair_data: dict[str, list[list[float | int]]], close_ts_ms: int) -> tuple[float, float]:
    raw_1h = visible_newest(pair_data["1H"], close_ts_ms, 120)
    _, _, closes_1h = parse_candles(raw_1h)
    slope_now = float(calc_slope(closes_1h, period=5)) if len(closes_1h) >= 6 else 0.0
    slope_prev = float(calc_slope(closes_1h[:-1], period=5)) if len(closes_1h) >= 7 else 0.0
    return slope_now, slope_prev


def classify_conditions_not_met(item: ReplayRow, cache: dict[str, dict[str, list[list[float | int]]]]) -> tuple[str, list[str]]:
    row = item.baseline
    result = item.current_result
    engine = getattr(result, "engine_vars", {}) or {}
    indicators = getattr(result, "indicators", {}) or {}
    h15 = indicators.get("15m", {}) or {}
    symbol = str(row["symbol"])
    cfg = swing_cfg(symbol)
    pair_data = cache.get(base_to_inst_id(symbol))
    close_ts_ms = ts_to_ms(str(row["ts"]))
    slope_now, slope_prev = slope_pair(pair_data, close_ts_ms) if pair_data else (0.0, 0.0)
    slope_min = float(STRATEGY_CFG.get("slope_min", 35.0))

    notes: list[str] = []
    if float(engine.get("vol_ratio_sig") or 0.0) < float(STRATEGY_CFG.get("min_vol_ratio_trending", 1.5)):
        notes.append("also_fails_min_vol_ratio")

    if result.regime != row["regime"]:
        return f"regime_reclassified({row['regime']}->{result.regime})", notes
    if bool(engine.get("four_h_conflict")):
        return "4h_conflict", notes
    if str(engine.get("bias_1h") or "") != "UP":
        return "bias_check_fail", notes
    if not bool(engine.get("five_m_trigger")):
        return "5m_trigger_mismatch", notes
    if slope_now < slope_min or slope_now <= slope_prev:
        if slope_now < slope_min:
            notes.append(f"slope_now={slope_now:.1f}<min={slope_min:.1f}")
        if slope_now <= slope_prev:
            notes.append(f"slope_not_rising(now={slope_now:.1f},prev={slope_prev:.1f})")
        return "slope_min_veto", notes

    if not bool(engine.get("adx_1h_rising")):
        notes.append("adx_1h_rising=False")
    if float(h15.get("bb_width_pct") or 0.0) < float(cfg.get("bb_width_min", 0.0)):
        notes.append(f"bb_width={float(h15.get('bb_width_pct') or 0.0):.2f}<min={float(cfg.get('bb_width_min', 0.0)):.2f}")
    if float(engine.get("adx_1h") or 0.0) < float(cfg.get("adx", 18.0)):
        notes.append(f"adx_1h={float(engine.get('adx_1h') or 0.0):.1f}<min={float(cfg.get('adx', 18.0)):.1f}")
    if float(engine.get("di_spread_1h") or 0.0) < 8.0:
        notes.append(f"di_spread_1h={float(engine.get('di_spread_1h') or 0.0):.1f}<8")
    if float(engine.get("di_spread_4h") or 0.0) < 8.0:
        notes.append(f"di_spread_4h={float(engine.get('di_spread_4h') or 0.0):.1f}<8")
    if float(engine.get("vol_ratio_sig") or 0.0) < float(cfg.get("vol", 0.0)):
        notes.append(f"pair_vol_ratio={float(engine.get('vol_ratio_sig') or 0.0):.2f}<min={float(cfg.get('vol', 0.0)):.2f}")

    return "other", notes


def fmt_stat(value: float, suffix: str = "") -> str:
    return "n/a" if not math.isfinite(value) else f"{value:.2f}{suffix}"


def bucket_title(regime: str, style: str) -> str:
    return f"{regime} x {style}"


def summarize_replays(replays: list[ReplayRow]) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline_rows = [item.baseline for item in replays]
    kept_rows = [item.baseline for item in replays if item.kept_same_bucket]
    return stats(baseline_rows), stats(kept_rows)


def metric_table(regime: str, style: str, replays: list[ReplayRow]) -> list[str]:
    before, after = summarize_replays(replays)
    lines = [
        f"### {bucket_title(regime, style)}",
        "",
        "| Metric | AS-IS | AFTER NEW FILTERS | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    rows = [
        ("n", before["n_decisive"], after["n_decisive"], after["n_decisive"] - before["n_decisive"]),
        ("WR", before["wr"], after["wr"], after["wr"] - before["wr"]),
        ("avg_R", before["avg_r"], after["avg_r"], after["avg_r"] - before["avg_r"]),
        ("std_R", before["std_r"], after["std_r"], after["std_r"] - before["std_r"]),
        ("PF", before["profit_factor"], after["profit_factor"], after["profit_factor"] - before["profit_factor"]),
        ("max_DD", before["max_dd"], after["max_dd"], after["max_dd"] - before["max_dd"]),
    ]
    for name, left, right, delta in rows:
        if name == "n":
            lines.append(f"| {name} | {int(left)} | {int(right)} | {int(delta)} |")
        elif name == "WR":
            lines.append(f"| {name} | {fmt_stat(left, '%')} | {fmt_stat(right, '%')} | {fmt_stat(delta, ' pp')} |")
        else:
            lines.append(f"| {name} | {fmt_stat(left)} | {fmt_stat(right)} | {fmt_stat(delta)} |")
    lines.append("")
    return lines


def cut_table(regime: str, style: str, replays: list[ReplayRow]) -> list[str]:
    counts = Counter(item.reason for item in replays if not item.kept_same_bucket)
    lines = [
        f"### {bucket_title(regime, style)} filter cuts",
        "",
        "| Reason | Count |",
        "| --- | ---: |",
    ]
    for reason, count in counts.most_common():
        lines.append(f"| {reason} | {count} |")
    if len(lines) == 4:
        lines.append("| none | 0 |")
    lines.append("")
    return lines


def sample_drops(regime: str, style: str, replays: list[ReplayRow], limit: int = 8) -> list[str]:
    lines = [
        f"### {bucket_title(regime, style)} dropped trades",
        "",
        "| signal_id | symbol | side | outcome | reason | pre_vol | live_vol |",
        "| --- | --- | --- | --- | --- | ---: | ---: |",
    ]
    shown = 0
    for item in replays:
        if item.kept_same_bucket:
            continue
        row = item.baseline
        lines.append(
            f"| {row['signal_id']} | {row['symbol']} | {row['side']} | {row['outcome']} | {item.reason} | "
            f"{fmt_stat(item.prefilter_vol_ratio)} | {fmt_stat(float(item.details.get('vol_ratio_sig') or float('nan')))} |"
        )
        shown += 1
        if shown >= limit:
            break
    if shown == 0:
        lines.append("| none | - | - | - | - | - | - |")
    lines.append("")
    return lines


def conditions_not_met_section(replays: list[ReplayRow], cache: dict[str, dict[str, list[list[float | int]]]]) -> tuple[list[str], dict[str, Any]]:
    target = [item for item in replays if item.reason == "conditions_not_met"]
    breakdown_rows: list[dict[str, Any]] = []
    for item in target:
        sub_reason, notes = classify_conditions_not_met(item, cache)
        breakdown_rows.append(
            {
                "signal_id": item.baseline["signal_id"],
                "symbol": item.baseline["symbol"],
                "sub_reason": sub_reason,
                "notes": notes,
                "overlap_min_vol": "also_fails_min_vol_ratio" in notes,
            }
        )

    counts = Counter(row["sub_reason"] for row in breakdown_rows)
    requested = [
        ("slope_min_veto", "slope_min veto"),
        ("regime_reclassified", "regime_reclassified (TRENDING->other)"),
        ("5m_trigger_mismatch", "5m trigger mismatch"),
        ("bias_check_fail", "bias check fail"),
        ("4h_conflict", "4h conflict"),
        ("other", "other"),
    ]

    lines = [
        "## TRENDING x SWING `conditions_not_met` decomposition",
        "",
        "| Sub-reason | Count | Trade IDs |",
        "| --- | ---: | --- |",
    ]
    for key, label in requested:
        matched = [row["signal_id"] for row in breakdown_rows if row["sub_reason"] == key or row["sub_reason"].startswith(key + "(")]
        lines.append(f"| {label} | {len(matched)} | {', '.join(matched) if matched else '-'} |")
    lines.extend(
        [
            "",
            "### Overlap with `min_vol_ratio_trending`",
            "",
            "| signal_id | sub-reason | also fails min_vol<1.5 | notes |",
            "| --- | --- | --- | --- |",
        ]
    )
    overlap_count = 0
    for row in breakdown_rows:
        overlap = "yes" if row["overlap_min_vol"] else "no"
        if row["overlap_min_vol"]:
            overlap_count += 1
        notes = ", ".join(row["notes"]) if row["notes"] else "-"
        lines.append(f"| {row['signal_id']} | {row['sub_reason']} | {overlap} | {notes} |")
    if not breakdown_rows:
        lines.append("| none | - | - | - |")
    lines.append("")

    payload = {
        "count": len(breakdown_rows),
        "sub_reason_counts": {
            "slope_min_veto": counts.get("slope_min_veto", 0),
            "regime_reclassified": sum(1 for row in breakdown_rows if row["sub_reason"].startswith("regime_reclassified")),
            "5m_trigger_mismatch": counts.get("5m_trigger_mismatch", 0),
            "bias_check_fail": counts.get("bias_check_fail", 0),
            "4h_conflict": counts.get("4h_conflict", 0),
            "other": counts.get("other", 0),
        },
        "min_vol_overlap_count": overlap_count,
        "rows": breakdown_rows,
    }
    return lines, payload


def verdict_for_bucket(regime: str, style: str, replays: list[ReplayRow]) -> tuple[str, str]:
    before, after = summarize_replays(replays)
    if before["n_decisive"] <= 0:
        return "N/A", "no baseline rows"
    cut_pct = (before["n_decisive"] - after["n_decisive"]) / before["n_decisive"] * 100.0
    avg_after = after["avg_r"]
    if bucket_title(regime, style) != "TRENDING x SWING":
        if math.isfinite(after["wr"]) and math.isfinite(before["wr"]) and math.isfinite(avg_after) and math.isfinite(before["avg_r"]):
            if after["wr"] >= before["wr"] and avg_after >= before["avg_r"]:
                return "precision-positive", f"cuts {cut_pct:.1f}% but improves surviving subset quality"
    if cut_pct < 20.0 and math.isfinite(avg_after) and avg_after > 0:
        return "A", f"cuts {cut_pct:.1f}% and avg_R stays positive"
    if cut_pct > 30.0 or (math.isfinite(avg_after) and avg_after < 0):
        return "B", f"cuts {cut_pct:.1f}% or avg_R turns non-positive"
    return "C", f"intermediate case: cuts {cut_pct:.1f}% with avg_R={avg_after:+.2f}"


def methodology() -> str:
    return (
        "Archive decisive majors trades were taken from the old scanner logs and labels, then replayed against the current "
        "`ws_main_screener` gating stack on the same timestamps using cached 5m/15m/1H/4H candles and current `compute_signal()`. "
        "The replay applies the outer 15m prefilter (`prefilter_vol_ratio_min=1.0`, `prefilter_adx_min=10`) before `compute_signal()`, "
        "then treats a trade as surviving only if current logic still emits `ENTRY` with the same regime/style/side bucket."
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_cache()
    archive_rows = [
        row
        for row in load_archive_rows()
        if row["decisive"] and row["pair"] in MAJORS and (row["regime"], row["style"]) in TARGET_BUCKETS
    ]

    by_bucket: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in archive_rows:
        by_bucket[(str(row["regime"]), str(row["style"]))].append(row)

    bucket_replays: dict[tuple[str, str], list[ReplayRow]] = {}
    payload: dict[str, Any] = {"buckets": {}}

    for bucket in TARGET_BUCKETS:
        replays = [replay_trade(row, cache) for row in sorted(by_bucket.get(bucket, []), key=lambda item: item["ts"])]
        bucket_replays[bucket] = replays
        before, after = summarize_replays(replays)
        verdict, rationale = verdict_for_bucket(bucket[0], bucket[1], replays)
        payload["buckets"][f"{bucket[0]} x {bucket[1]}"] = {
            "baseline_n": before["n_decisive"],
            "after_n": after["n_decisive"],
            "baseline_wr": before["wr"],
            "after_wr": after["wr"],
            "baseline_avg_r": before["avg_r"],
            "after_avg_r": after["avg_r"],
            "cut_reasons": dict(Counter(item.reason for item in replays if not item.kept_same_bucket)),
            "verdict": verdict,
            "rationale": rationale,
        }

    ts_condition_lines, ts_condition_payload = conditions_not_met_section(bucket_replays.get(("TRENDING", "SWING"), []), cache)
    payload["trex_swing_conditions_not_met"] = ts_condition_payload

    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# Majors vs Alts Replay — 2026-05-19")
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append(methodology())
    lines.append("")
    lines.append("## AS-IS vs AFTER")
    lines.append("")
    for bucket in TARGET_BUCKETS:
        lines.extend(metric_table(bucket[0], bucket[1], bucket_replays[bucket]))

    lines.append("## Breakdown Filter Cuts")
    lines.append("")
    for bucket in TARGET_BUCKETS:
        lines.extend(cut_table(bucket[0], bucket[1], bucket_replays[bucket]))

    lines.append("## Dropped Trade Samples")
    lines.append("")
    for bucket in TARGET_BUCKETS:
        lines.extend(sample_drops(bucket[0], bucket[1], bucket_replays[bucket]))

    lines.extend(ts_condition_lines)

    lines.append("## Verdict")
    lines.append("")
    for bucket in TARGET_BUCKETS:
        verdict, rationale = verdict_for_bucket(bucket[0], bucket[1], bucket_replays[bucket])
        lines.append(f"- {bucket_title(bucket[0], bucket[1])}: scenario `{verdict}` — {rationale}.")
    lines.append("")

    ts_replays = bucket_replays.get(("TRENDING", "SWING"), [])
    ts_counts = Counter(item.reason for item in ts_replays if not item.kept_same_bucket)
    lines.append("## Concrete Next Experiment")
    lines.append("")
    if ts_counts.get("prefilter", 0) == 0 and ts_counts.get("min_vol_ratio_trending", 0) == 0:
        lines.append("- Current live filter stack does not materially damage archive majors in `TRENDING x SWING`; wait for pinned-majors live accumulation before changing main scanner config.")
    else:
        lines.append("- The next code-free experiment should isolate `min_vol_ratio_trending` on majors only, because that is the only filter with a plausible archive-edge cost inside `TRENDING x SWING`.")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"saved {REPORT_PATH}")
    print(f"saved {DATA_PATH}")
    return 0


if __name__ == "__main__":
    STRATEGY_CFG, MAIN_CFG = read_config()
    MAIN_CFG.setdefault("prefilter_vol_ratio_min", 1.0)
    MAIN_CFG.setdefault("prefilter_adx_min", 10.0)
    raise SystemExit(main())
