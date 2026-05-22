from __future__ import annotations

import csv
import gzip
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
import regime_exit_rerun_22_05_2026 as exit_rerun
import regime_model_phaseB_21_05_2026 as phase_b


SUFFIX = "22_05_2026"
ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
CASE_DIR = OUT_DIR / f"three_engines_cases_{SUFFIX}"
REPORT_MD = OUT_DIR / f"three_engines_report_{SUFFIX}.md"
SUMMARY_JSON = OUT_DIR / f"three_engines_summary_{SUFFIX}.json"
RUN_LOG = OUT_DIR / f"three_engines_run_{SUFFIX}.log"
TICK_ROOT = Path(r"E:\trading-data\ticks")

CONFIG = {
    "fee_pct": 0.20,
    "entry_slippage_pct": 0.03,
    "universe_symbols": exit_rerun.CONFIG["universe_symbols"],
    "trend_hold_bars": 32,
    "trend_structure_k": 3,
    "trend_edge_pct": 1.40,
    "impulse_tick_trigger_pct": 0.30,
    "impulse_tick_trigger_sec": 300,
    "impulse_hold_bars": 16,
    "impulse_structure_k": 1,
    "impulse_edge_pct": 0.80,
    "fade_hold_bars": 8,
    "fade_stop_buffer_pct": 0.12,
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


def load_replay() -> tuple[dict[str, phase_a.CandleSet], list[dict[str, Any]], list[dict[str, Any]], int, int]:
    universe = list(CONFIG["universe_symbols"])
    strategy_cfg = phase_a.load_strategy_config()
    start_close_ms, end_close_ms = phase_a.cached_reference_window(universe)
    candle_sets: dict[str, phase_a.CandleSet] = {}
    decisions_all: list[dict[str, Any]] = []
    events_all: list[dict[str, Any]] = []
    log(f"three engines replay window {iso_from_ms(start_close_ms)} -> {iso_from_ms(end_close_ms)}")
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


def tick_date_from_name(path: Path) -> str | None:
    name = path.name
    if len(name) < 10:
        return None
    date = name[:10]
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None
    return date


def tick_files_for_symbol(symbol: str) -> list[Path]:
    folder = TICK_ROOT / symbol
    if not folder.exists():
        return []
    return sorted([p for p in folder.iterdir() if p.is_file() and tick_date_from_name(p)])


def tick_coverage(symbols: Iterable[str], start_ms: int, end_ms: int) -> dict[str, Any]:
    start_date = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date().isoformat()
    end_date = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).date().isoformat()
    rows = []
    for symbol in sorted(symbols):
        files = tick_files_for_symbol(symbol)
        overlap = [p for p in files if start_date <= (tick_date_from_name(p) or "") <= end_date]
        rows.append(
            {
                "symbol": symbol,
                "dir_exists": (TICK_ROOT / symbol).exists(),
                "file_count": len(files),
                "overlap_files": len(overlap),
                "first_file": tick_date_from_name(files[0]) if files else None,
                "last_file": tick_date_from_name(files[-1]) if files else None,
            }
        )
    return {
        "tick_root": str(TICK_ROOT),
        "period_dates": {"start": start_date, "end": end_date},
        "symbols": rows,
        "dir_coverage": pct(sum(1 for row in rows if row["dir_exists"]), len(rows)),
        "period_file_coverage": pct(sum(1 for row in rows if row["overlap_files"] > 0), len(rows)),
    }


def candle_coverage(candle_sets: dict[str, phase_a.CandleSet]) -> dict[str, Any]:
    rows = []
    for symbol, candle_set in sorted(candle_sets.items()):
        rows.append(
            {
                "symbol": symbol,
                "source": candle_set.source,
                "bars": {tf: len(candle_set.rows.get(tf) or []) for tf in ["5m", "15m", "1H", "4H"]},
            }
        )
    return {"symbols": rows, "loaded_symbols": len(rows)}


def open_tick_file(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def load_ticks(symbol: str, start_ms: int, end_ms: int, cache: dict[str, list[tuple[int, float]]]) -> list[tuple[int, float]]:
    if symbol in cache:
        return cache[symbol]
    out: list[tuple[int, float]] = []
    start_date = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date().isoformat()
    end_date = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).date().isoformat()
    for path in tick_files_for_symbol(symbol):
        date = tick_date_from_name(path) or ""
        if not (start_date <= date <= end_date):
            continue
        with open_tick_file(path) as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                side = (row.get("side") or "").lower()
                if side == "gap" or side not in {"buy", "sell"}:
                    continue
                ts_ms = int(float(row.get("ts_ms") or 0))
                if not (start_ms <= ts_ms <= end_ms):
                    continue
                price = safe_float(row.get("price"))
                if math.isfinite(price):
                    out.append((ts_ms, price))
    out.sort(key=lambda item: item[0])
    cache[symbol] = out
    return out


def ticks_between(ticks: list[tuple[int, float]], start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    return [(ts, price) for ts, price in ticks if start_ms <= ts <= end_ms]


def slipped_entry(price: float, side: str) -> float:
    return exit_rerun.slipped_entry(price, side)


def dir_return(entry: float, price: float, side: str) -> float:
    return phase_b.dir_return(entry, price, side)


def route_engine(event: dict[str, Any]) -> str | None:
    cell = phase_b.event_cell(event)
    if cell == "RANGING":
        return "fade"
    if cell == "TRENDING_IMPULSE":
        return "impulse"
    if cell in {"TRENDING_GRIND", "TRENDING_SWING"}:
        return "trend"
    if event.get("corrected_regime") == "DRIFT" and event.get("move_type") == "FAST":
        return "trend"
    return None


def hold_for_engine(engine: str, move_type: str) -> int:
    if engine == "trend":
        return CONFIG["trend_hold_bars"]
    if engine == "impulse":
        return CONFIG["impulse_hold_bars"]
    return CONFIG["fade_hold_bars"]


def find_tick_impulse_entry(
    ticks: list[tuple[int, float]],
    start_ms: int,
    open_price: float,
    side: str,
) -> tuple[int, float] | None:
    trigger_until = start_ms + CONFIG["impulse_tick_trigger_sec"] * 1000
    threshold = CONFIG["impulse_tick_trigger_pct"]
    for ts_ms, price in ticks_between(ticks, start_ms, trigger_until):
        if dir_return(open_price, price, side) >= threshold:
            return ts_ms, price
    return None


def sim_trailing_exit(
    rows: list[list[Any]],
    idx: int,
    entry_price_raw: float,
    side: str,
    hold_bars: int,
    structure_k: int,
    stop: float,
) -> dict[str, Any]:
    entry = slipped_entry(entry_price_raw, side)
    end = min(len(rows) - 1, idx + hold_bars)
    best = 0.0
    worst = 0.0
    outcome = "TIME"
    exit_idx = end
    exit_price = safe_float(rows[end][4])
    for j in range(idx + 1, end + 1):
        row = rows[j]
        fav = exit_rerun.favorable_price(row, side)
        adv = exit_rerun.adverse_price(row, side)
        close = safe_float(row[4])
        best = max(best, dir_return(entry, fav, side))
        worst = min(worst, dir_return(entry, adv, side))
        if exit_rerun.stop_hit(row, stop, side):
            outcome = "SL"
            exit_idx = j
            exit_price = stop
            break
        if exit_rerun.structure_break(rows, j, side, structure_k):
            outcome = f"STRUCT_K{structure_k}"
            exit_idx = j
            exit_price = close
            break
    gross = dir_return(entry, exit_price, side)
    return {
        "entry": entry,
        "entry_raw": entry_price_raw,
        "stop": stop,
        "exit_idx": exit_idx,
        "exit_price": exit_price,
        "outcome": outcome,
        "gross_pct": gross,
        "net_pct": gross - CONFIG["fee_pct"],
        "mfe_pct": best,
        "mae_pct": worst,
        "capture_pct": positive_capture(gross, best),
    }


def sim_fade_exit(rows: list[list[Any]], idx: int, side: str, engine_ctx: dict[str, Any]) -> dict[str, Any] | None:
    h15 = ((engine_ctx.get("indicators") or {}).get("15m") or {})
    middle = safe_float(h15.get("bb_middle"))
    upper = safe_float(h15.get("bb_upper"))
    lower = safe_float(h15.get("bb_lower"))
    if not all(math.isfinite(v) and v > 0 for v in [middle, upper, lower]):
        return None
    entry_raw = safe_float(rows[idx][4])
    entry = slipped_entry(entry_raw, side)
    buffer_dist = entry_raw * CONFIG["fade_stop_buffer_pct"] / 100
    stop = lower - buffer_dist if side == "long" else upper + buffer_dist
    target = middle
    end = min(len(rows) - 1, idx + CONFIG["fade_hold_bars"])
    best = 0.0
    worst = 0.0
    outcome = "TIME"
    exit_idx = end
    exit_price = safe_float(rows[end][4])
    for j in range(idx + 1, end + 1):
        row = rows[j]
        fav = exit_rerun.favorable_price(row, side)
        adv = exit_rerun.adverse_price(row, side)
        best = max(best, dir_return(entry, fav, side))
        worst = min(worst, dir_return(entry, adv, side))
        if exit_rerun.stop_hit(row, stop, side):
            outcome = "SL"
            exit_idx = j
            exit_price = stop
            break
        hit_mid = safe_float(row[2]) >= target if side == "long" else safe_float(row[3]) <= target
        if hit_mid:
            outcome = "BB_MID"
            exit_idx = j
            exit_price = target
            break
    gross = dir_return(entry, exit_price, side)
    risk = abs(entry - stop)
    r_to_mid = abs(target - entry) / risk if risk > 0 else float("nan")
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
        "r_to_mid": r_to_mid,
        "capture_pct": positive_capture(gross, best),
    }


def movement_available(rows: list[list[Any]], idx: int, side: str, hold_bars: int, basis_price: float) -> dict[str, Any]:
    end = min(len(rows) - 1, idx + hold_bars)
    best = 0.0
    worst = 0.0
    best_idx = idx
    worst_idx = idx
    for j in range(idx, end + 1):
        fav = exit_rerun.favorable_price(rows[j], side)
        adv = exit_rerun.adverse_price(rows[j], side)
        fav_ret = dir_return(basis_price, fav, side)
        adv_ret = dir_return(basis_price, adv, side)
        if fav_ret > best:
            best = fav_ret
            best_idx = j
        if adv_ret < worst:
            worst = adv_ret
            worst_idx = j
    return {"available_pct": best, "adverse_pct": worst, "best_idx": best_idx, "worst_idx": worst_idx}


def build_engine_rows(
    events: list[dict[str, Any]],
    candle_sets: dict[str, phase_a.CandleSet],
    start_ms: int,
    end_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, list[tuple[int, float]]]]:
    rows = []
    tick_cache: dict[str, list[tuple[int, float]]] = {}
    for event in events:
        symbol = event["symbol"]
        candle_set = candle_sets.get(symbol)
        if not candle_set:
            continue
        idx = phase_b.candle_idx(candle_set, "15m", int(event["start_open_ms"]))
        if idx is None:
            continue
        engine = route_engine(event)
        if engine is None:
            continue
        candle_rows = candle_set.rows["15m"]
        model = phase_b.model_for_event(event) or engine
        side = phase_b.side_from_structure(candle_rows, idx, model if model != "trend_grind_watch" else "trend_impulse")
        if side is None:
            side = event["direction"]
        engine_ctx = event.get("start_engine") or event.get("engine") or {}
        row = {
            "engine": engine,
            "symbol": symbol,
            "ts": event["start_ts"],
            "idx": idx,
            "cell": phase_b.event_cell(event),
            "tier": phase_b.volatility_tier(symbol, candle_set),
            "period": phase_b.event_period(event, start_ms, end_ms),
            "move_type": event["move_type"],
            "event_direction": event["direction"],
            "model_side": side,
            "side_match": side == event["direction"],
            "skip_reason": "",
        }
        if engine in {"trend", "impulse"}:
            guard, reason = phase_b.peak_guard(candle_rows, idx, side, engine_ctx)
            if guard and engine == "impulse":
                row["skip_reason"] = f"peak_guard:{reason}"
                rows.append(row)
                continue
        if engine == "trend":
            entry_raw = safe_float(candle_rows[idx][4])
            stop = exit_rerun.structural_stop(candle_rows, idx, side)
            sim = sim_trailing_exit(candle_rows, idx, entry_raw, side, CONFIG["trend_hold_bars"], CONFIG["trend_structure_k"], stop)
            avail = movement_available(candle_rows, idx, side, CONFIG["trend_hold_bars"], entry_raw)
            row.update({"sim": sim, "available": avail, "edge_exists": avail["available_pct"] >= CONFIG["trend_edge_pct"]})
        elif engine == "impulse":
            open_price = safe_float(candle_rows[idx][1])
            event_start = int(event["start_open_ms"])
            avail = movement_available(candle_rows, idx, side, CONFIG["impulse_hold_bars"], open_price)
            ticks = load_ticks(symbol, event_start, event_start + phase_a.TF_MS["15m"], tick_cache)
            tick_entry = find_tick_impulse_entry(ticks, event_start, open_price, side) if ticks else None
            if tick_entry is None:
                row["skip_reason"] = "no_tick_trigger_or_coverage"
                row["tick_count_window"] = len(ticks)
                row["available"] = avail
                row["edge_exists"] = avail["available_pct"] >= CONFIG["impulse_edge_pct"]
                rows.append(row)
                continue
            entry_ts, entry_raw = tick_entry
            stop = exit_rerun.structural_stop(candle_rows, idx, side)
            sim = sim_trailing_exit(candle_rows, idx, entry_raw, side, CONFIG["impulse_hold_bars"], CONFIG["impulse_structure_k"], stop)
            row.update(
                {
                    "sim": sim,
                    "available": avail,
                    "edge_exists": avail["available_pct"] >= CONFIG["impulse_edge_pct"],
                    "entry_lag_pct": dir_return(open_price, entry_raw, side),
                    "entry_delay_sec": (entry_ts - event_start) / 1000,
                    "tick_count_window": len(ticks),
                }
            )
        else:
            sim = sim_fade_exit(candle_rows, idx, side, engine_ctx)
            if sim is None:
                row["skip_reason"] = "missing_bb_data"
                rows.append(row)
                continue
            avail = movement_available(candle_rows, idx, side, CONFIG["fade_hold_bars"], safe_float(candle_rows[idx][4]))
            row.update({"sim": sim, "available": avail, "edge_exists": sim["outcome"] == "BB_MID"})
        rows.append(row)
    return rows, tick_cache


def empty_accum() -> dict[str, Any]:
    return {
        "n": 0,
        "filled": 0,
        "wins": 0,
        "net": [],
        "capture": [],
        "available": [],
        "mae": [],
        "hold": [],
        "r_to_mid": [],
        "entry_lag": [],
        "entry_delay": [],
        "edge": 0,
        "side_known": 0,
        "side_match": 0,
        "outcomes": Counter(),
        "skips": Counter(),
    }


def update_accum(acc: dict[str, Any], row: dict[str, Any]) -> None:
    acc["n"] += 1
    if row.get("model_side") in {"long", "short"} and row.get("event_direction") in {"long", "short"}:
        acc["side_known"] += 1
        acc["side_match"] += 1 if row["model_side"] == row["event_direction"] else 0
    if row.get("skip_reason"):
        acc["skips"][row["skip_reason"]] += 1
        return
    sim = row.get("sim") or {}
    avail = row.get("available") or {}
    if not sim:
        acc["skips"]["no_sim"] += 1
        return
    acc["filled"] += 1
    acc["wins"] += 1 if safe_float(sim.get("net_pct")) > 0 else 0
    acc["net"].append(safe_float(sim.get("net_pct")))
    acc["capture"].append(safe_float(sim.get("capture_pct")))
    acc["available"].append(safe_float(avail.get("available_pct")))
    acc["mae"].append(safe_float(sim.get("mae_pct")))
    acc["edge"] += 1 if row.get("edge_exists") else 0
    acc["outcomes"][sim.get("outcome") or "UNKNOWN"] += 1
    acc["entry_lag"].append(safe_float(row.get("entry_lag_pct")))
    acc["entry_delay"].append(safe_float(row.get("entry_delay_sec")))
    acc["r_to_mid"].append(safe_float(sim.get("r_to_mid")))
    exit_idx = sim.get("exit_idx")
    if exit_idx is not None:
        acc["hold"].append((int(exit_idx) - int(row["idx"])) * 15)


def finalize_accum(acc: dict[str, Any]) -> dict[str, Any]:
    filled = acc["filled"]
    return {
        "n": acc["n"],
        "filled": filled,
        "fill_rate": pct(filled, acc["n"]),
        "avg_net_pct": average(acc["net"]),
        "win_rate": pct(acc["wins"], filled),
        "avg_capture_pct": average(acc["capture"]),
        "avg_available_pct": average(acc["available"]),
        "edge_exists_rate": pct(acc["edge"], filled),
        "avg_mae_pct": average(acc["mae"]),
        "avg_hold_min": average(acc["hold"]),
        "avg_r_to_mid": average(acc["r_to_mid"]),
        "avg_entry_lag_pct": average(acc["entry_lag"]),
        "avg_entry_delay_sec": average(acc["entry_delay"]),
        "side_match_rate": pct(acc["side_match"], acc["side_known"]),
        "outcomes": dict(acc["outcomes"]),
        "skips": dict(acc["skips"]),
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accum: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(empty_accum)
    for row in rows:
        keys = [
            (row["engine"], "all", "both", "all"),
            (row["engine"], row.get("tier") or "unknown", "both", "all"),
            (row["engine"], "all", row.get("model_side") or "none", "all"),
            (row["engine"], "all", "both", row.get("period") or "all"),
        ]
        for key in keys:
            update_accum(accum[key], row)
    out = []
    for key, acc in accum.items():
        engine, tier, side, period = key
        out.append({"engine": engine, "tier": tier, "side": side, "period": period, **finalize_accum(acc)})
    out.sort(key=lambda row: (row["engine"], row["tier"], row["side"], row["period"]))
    return out


def go_no_go(row: dict[str, Any], summary: list[dict[str, Any]]) -> str:
    if row["filled"] < 20:
        return "NO-GO: sample<20"
    if safe_float(row["avg_net_pct"]) <= 0:
        return "NO-GO: net<=0"
    side_rows = [
        r for r in summary
        if r["engine"] == row["engine"] and r["tier"] == "all" and r["period"] == "all" and r["side"] in {"long", "short"}
    ]
    if row["engine"] != "fade" and (len(side_rows) < 2 or any(r["filled"] < 20 or safe_float(r["avg_net_pct"]) <= 0 for r in side_rows)):
        return "NO-GO: side split fails"
    period_rows = [
        r for r in summary
        if r["engine"] == row["engine"] and r["tier"] == "all" and r["side"] == "both" and r["period"] in {"early", "late"}
    ]
    if len(period_rows) < 2 or any(r["filled"] < 10 or safe_float(r["avg_net_pct"]) <= 0 for r in period_rows):
        return "NO-GO: early/late split fails"
    return "GO"


def main_rows(summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in summary if r["tier"] == "all" and r["side"] == "both" and r["period"] == "all"]


def render_case(path: Path, row: dict[str, Any], candle_set: phase_a.CandleSet) -> None:
    candle_rows = candle_set.rows["15m"]
    idx = int(row["idx"])
    sim = row.get("sim") or {}
    avail = row.get("available") or {}
    end_idx = min(len(candle_rows) - 1, max(int(sim.get("exit_idx", idx)), int(avail.get("best_idx", idx))) + 6)
    start_idx = max(0, idx - 10)
    subset = candle_rows[start_idx : end_idx + 1]
    x0 = idx - start_idx
    fig, ax = plt.subplots(figsize=(13, 6))
    for i, cndl in enumerate(subset):
        o, h, l, c = map(safe_float, cndl[1:5])
        color = "#15936b" if c >= o else "#c23b3b"
        ax.vlines(i, l, h, color=color, linewidth=0.9)
        ax.add_patch(patches.Rectangle((i - 0.35, min(o, c)), 0.7, max(abs(c - o), (h - l) * 0.02), color=color, alpha=0.78))
    if sim:
        exit_x = int(sim["exit_idx"]) - start_idx
        best_x = int(avail.get("best_idx", idx)) - start_idx
        ax.scatter([x0], [sim["entry_raw"]], color="#0b5bd3", s=45, zorder=8, label="entry")
        ax.axhline(sim["stop"], color="#d62728", linestyle="--", linewidth=0.8, label="stop")
        if sim.get("target"):
            ax.axhline(sim["target"], color="#2ca02c", linestyle="--", linewidth=0.8, label="target")
        ax.axvline(exit_x, color="#111111", linestyle=":", linewidth=1.0, label="exit")
        ax.scatter([best_x], [exit_rerun.favorable_price(candle_rows[int(avail.get("best_idx", idx))], row["model_side"])], color="#f0ad00", s=42, zorder=8, label="available MFE")
    else:
        best_idx = int(avail.get("best_idx", idx))
        best_x = best_idx - start_idx
        ax.scatter([x0], [safe_float(candle_rows[idx][1])], color="#6f42c1", s=45, zorder=8, label="impulse start")
        ax.scatter([best_x], [exit_rerun.favorable_price(candle_rows[best_idx], row["model_side"])], color="#f0ad00", s=42, zorder=8, label="available MFE")
    ax.set_title(
        f"{row['engine']} {row['symbol']} {row['ts']} {row['model_side']} | net {fmt(sim.get('net_pct'), '%')} cap {fmt(sim.get('capture_pct'), '%')} edge {row.get('edge_exists')}",
        fontsize=9,
        loc="left",
    )
    step = max(1, len(subset) // 8)
    ticks = list(range(0, len(subset), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([iso_from_ms(int(subset[i][0]) + phase_a.TF_MS["15m"])[11:16] for i in ticks], fontsize=8)
    ax.grid(alpha=0.18)
    ax.legend(loc="best", fontsize=8)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_cases(rows: list[dict[str, Any]], candle_sets: dict[str, phase_a.CandleSet]) -> None:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    for old in CASE_DIR.glob(f"*_{SUFFIX}.png"):
        old.unlink()
    for engine in ["trend", "impulse", "fade"]:
        candidates = [row for row in rows if row["engine"] == engine and not row.get("skip_reason") and row.get("sim")]
        candidates.sort(key=lambda row: safe_float((row.get("sim") or {}).get("net_pct")), reverse=True)
        selected = candidates[: CONFIG["case_limit_per_engine"]]
        if engine == "impulse" and len(selected) < CONFIG["case_limit_per_engine"]:
            skipped = [
                row for row in rows
                if row["engine"] == engine and row.get("skip_reason") and row.get("available")
            ]
            skipped.sort(key=lambda row: safe_float((row.get("available") or {}).get("available_pct")), reverse=True)
            selected.extend(skipped[: CONFIG["case_limit_per_engine"] - len(selected)])
        for i, row in enumerate(selected, start=1):
            candle_set = candle_sets.get(row["symbol"])
            if not candle_set:
                continue
            safe_ts = row["ts"].replace(":", "").replace("-", "").replace("Z", "")
            path = CASE_DIR / f"{engine}_{i:02d}_{row['symbol']}_{safe_ts}_{SUFFIX}.png"
            render_case(path, row, candle_set)


def render_report(summary: list[dict[str, Any]], coverage: dict[str, Any], candle_cov: dict[str, Any], start_ms: int, end_ms: int) -> str:
    main = main_rows(summary)
    main_table = [
        [
            row["engine"],
            row["n"],
            row["filled"],
            fmt(row["avg_net_pct"], "%"),
            fmt(row["win_rate"], "%"),
            fmt(row["avg_capture_pct"], "%"),
            fmt(row["avg_available_pct"], "%"),
            fmt(row["edge_exists_rate"], "%"),
            fmt(row["avg_hold_min"], "m"),
            fmt(row["side_match_rate"], "%"),
            go_no_go(row, summary),
        ]
        for row in main
    ]
    side_table = [
        [r["engine"], r["side"], r["filled"], fmt(r["avg_net_pct"], "%"), fmt(r["win_rate"], "%"), fmt(r["avg_capture_pct"], "%")]
        for r in summary
        if r["tier"] == "all" and r["period"] == "all" and r["side"] in {"long", "short"} and r["filled"] > 0
    ]
    period_table = [
        [r["engine"], r["period"], r["filled"], fmt(r["avg_net_pct"], "%"), fmt(r["win_rate"], "%"), fmt(r["avg_capture_pct"], "%")]
        for r in summary
        if r["tier"] == "all" and r["side"] == "both" and r["period"] in {"early", "late"} and r["filled"] > 0
    ]
    tier_table = [
        [r["engine"], r["tier"], r["filled"], fmt(r["avg_net_pct"], "%"), fmt(r["win_rate"], "%"), fmt(r["avg_capture_pct"], "%")]
        for r in summary
        if r["tier"] != "all" and r["side"] == "both" and r["period"] == "all" and r["filled"] > 0
    ]
    tick_missing = [r for r in coverage["symbols"] if r["overlap_files"] == 0]
    lines = [
        "# Three Engines Research - 22.05.2026",
        "",
        f"Replay period: `{iso_from_ms(start_ms)}` to `{iso_from_ms(end_ms)}`. Universe is the fixed 29-symbol Phase B universe. Fee `{CONFIG['fee_pct']:.2f}%`, slippage `{CONFIG['entry_slippage_pct']:.2f}%`.",
        "",
        "## Data Coverage",
        "",
        f"- candle symbols loaded: `{candle_cov['loaded_symbols']}`",
        f"- tick directories present: `{fmt(coverage['dir_coverage'], '%')}`",
        f"- tick files overlapping replay dates: `{fmt(coverage['period_file_coverage'], '%')}`",
        f"- symbols without replay-period tick files: `{', '.join(r['symbol'] for r in tick_missing) or 'none'}`",
        "",
        "## Detection Conditions",
        "",
        "- `trend`: corrected TRENDING_SWING/TRENDING_GRIND plus DRIFT FAST; structural side; enter continuation at 15m signal close; structural stop behind impulse candle; ride with `structure_k3` for up to 32 bars.",
        "- `impulse`: corrected TRENDING_IMPULSE; high-speed move; real tape trigger only, first tick reaching `0.30%` directional move within `300s` from 15m open; structural ride with `structure_k1`. No tick trigger means skipped, not approximated.",
        "- `fade`: corrected RANGING; fade side near BB/range boundary; target BB middle; stop outside BB boundary plus buffer; short hold.",
        "",
        "## Engine Metrics",
        "",
    ]
    lines.extend(render_table(["engine", "events", "filled", "net", "WR", "capture", "available", "edge", "hold", "dir match", "verdict"], main_table))
    lines.extend(["", "## Side Split", ""])
    lines.extend(render_table(["engine", "side", "filled", "net", "WR", "capture"], side_table))
    lines.extend(["", "## Early/Late Split", ""])
    lines.extend(render_table(["engine", "period", "filled", "net", "WR", "capture"], period_table))
    lines.extend(["", "## Volatility Tier Split", ""])
    lines.extend(render_table(["engine", "tier", "filled", "net", "WR", "capture"], tier_table))
    lines.extend(
        [
            "",
            "## Per-Engine Notes",
            "",
            "- Trend is judged by ride/capture and hold time, not by one-candle TP. It is still sensitive to side quality and late trend entries.",
            "- Impulse is the only branch that requires tape. The report separates missing tick trigger/coverage from failed price action.",
            "- Fade is judged by mean reversion to BB middle and range-side symmetry. It can pass net while still failing robustness if one side or period dominates.",
            "",
            "## Verdict",
            "",
            "The three engines should stay separated. The shared impulse detector is too blunt: trend needs ride logic, impulse needs tick-level entry, and range needs BB fade metrics. GO/NO-GO is kept strict; thin or asymmetric positives remain research candidates.",
            "",
            "## GPT Hypotheses",
            "",
            "- Edge-vs-capture is the right primary split: if `edge_exists` is high and capture is low, execution is the problem; if both are low, the setup has no edge in this sample.",
            "- Impulse cannot be honestly evaluated for early entry where replay-period ticks are missing; those rows should not be converted to candle proxies.",
            "- Fade looks structurally different from trend/impulse and should keep its own BB-middle target metrics.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")
    candle_sets, decisions, events, start_ms, end_ms = load_replay()
    candle_cov = candle_coverage(candle_sets)
    coverage = tick_coverage(CONFIG["universe_symbols"], start_ms, end_ms)
    log(f"coverage: candles={candle_cov['loaded_symbols']} tick_dirs={fmt(coverage['dir_coverage'], '%')} tick_period_files={fmt(coverage['period_file_coverage'], '%')}")
    rows, tick_cache = build_engine_rows(events, candle_sets, start_ms, end_ms)
    summary = summarize(rows)
    generate_cases(rows, candle_sets)
    REPORT_MD.write_text(render_report(summary, coverage, candle_cov, start_ms, end_ms), encoding="utf-8")
    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": CONFIG,
        "period": {"start": iso_from_ms(start_ms), "end": iso_from_ms(end_ms)},
        "decision_count": len(decisions),
        "event_count": len(events),
        "row_count": len(rows),
        "coverage": coverage,
        "candle_coverage": candle_cov,
        "tick_symbols_loaded": sorted(tick_cache),
        "summary": summary,
        "case_dir": str(CASE_DIR),
    }
    SUMMARY_JSON.write_text(json.dumps(json_safe(payload), ensure_ascii=True, indent=2, allow_nan=False), encoding="utf-8")
    log(f"saved {REPORT_MD}")
    log(f"saved {SUMMARY_JSON}")
    log(f"saved cases to {CASE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
