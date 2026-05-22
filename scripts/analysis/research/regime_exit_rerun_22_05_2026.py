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
import regime_model_phaseB_21_05_2026 as phase_b


SUFFIX = "22_05_2026"
ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
CASE_DIR = OUT_DIR / f"regime_exit_rerun_cases_{SUFFIX}"
LATE_CASE_DIR = OUT_DIR / f"regime_exit_rerun_late_cases_{SUFFIX}"
REPORT_MD = OUT_DIR / f"regime_exit_rerun_report_{SUFFIX}.md"
SUMMARY_JSON = OUT_DIR / f"regime_exit_rerun_summary_{SUFFIX}.json"
RUN_LOG = OUT_DIR / f"regime_exit_rerun_run_{SUFFIX}.log"

CONFIG = {
    "fee_pct": 0.20,
    "entry_slippage_pct": 0.03,
    "universe_symbols": [
        "ADA-USDT-SWAP",
        "BILL-USDT-SWAP",
        "BOME-USDT-SWAP",
        "BONK-USDT-SWAP",
        "BSB-USDT-SWAP",
        "BTC-USDT-SWAP",
        "CHZ-USDT-SWAP",
        "DOGE-USDT-SWAP",
        "EDEN-USDT-SWAP",
        "ETH-USDT-SWAP",
        "FLOKI-USDT-SWAP",
        "GALA-USDT-SWAP",
        "HMSTR-USDT-SWAP",
        "LINEA-USDT-SWAP",
        "MEME-USDT-SWAP",
        "MEW-USDT-SWAP",
        "NEIRO-USDT-SWAP",
        "NOT-USDT-SWAP",
        "PENGU-USDT-SWAP",
        "PEPE-USDT-SWAP",
        "PUMP-USDT-SWAP",
        "RLS-USDT-SWAP",
        "SAHARA-USDT-SWAP",
        "SATS-USDT-SWAP",
        "SHIB-USDT-SWAP",
        "SOL-USDT-SWAP",
        "SPACE-USDT-SWAP",
        "TURBO-USDT-SWAP",
        "XRP-USDT-SWAP",
    ],
    "structural_stop_buffer_pct": 0.10,
    "min_stop_pct": 0.35,
    "structure_k": [1, 2, 3],
    "giveback_pct": [30, 40, 50],
    "scaled_tp_fracs": [0.50, 0.75, 1.00],
    "fast_hold_bars": 4,
    "swing_hold_bars": 16,
    "case_limit_per_cell": 8,
    "late_case_limit": 10,
    "edge_threshold_fast_pct": 0.80,
    "edge_threshold_swing_pct": 1.40,
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


def dir_return(entry: float, price: float, side: str) -> float:
    return phase_b.dir_return(entry, price, side)


def load_replay() -> tuple[list[str], dict[str, phase_a.CandleSet], list[dict[str, Any]], list[dict[str, Any]], int, int]:
    universe = list(CONFIG["universe_symbols"])
    strategy_cfg = phase_a.load_strategy_config()
    start_close_ms, end_close_ms = phase_a.cached_reference_window(universe)
    candle_sets: dict[str, phase_a.CandleSet] = {}
    all_decisions: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    log(f"exit rerun window {iso_from_ms(start_close_ms)} -> {iso_from_ms(end_close_ms)}")
    for symbol in universe:
        candle_set = phase_a.load_symbol_candles(symbol, start_close_ms, end_close_ms)
        if candle_set is None:
            log(f"{symbol}: skipped (missing candles)")
            continue
        candle_sets[symbol] = candle_set
        decisions, events = phase_a.replay_symbol(symbol, candle_set, strategy_cfg, start_close_ms, end_close_ms)
        for row in decisions:
            new_regime, reason = phase_b.corrected_regime(row)
            row["corrected_regime"] = new_regime
            row["corrected_reason"] = reason
        for event in events:
            start_row = event.get("start_engine") or event.get("engine") or {}
            new_regime, reason = phase_b.corrected_regime(start_row)
            event["old_regime"] = event["regime"]
            event["corrected_regime"] = new_regime
            event["corrected_reason"] = reason
        all_decisions.extend(decisions)
        all_events.extend(events)
        log(f"{symbol}: decisions={len(decisions)} moves={len(events)}")
    return universe, candle_sets, all_decisions, all_events, start_close_ms, end_close_ms


def hold_bars(move_type: str) -> int:
    return CONFIG["fast_hold_bars"] if move_type == "FAST" else CONFIG["swing_hold_bars"]


def slipped_entry(price: float, side: str) -> float:
    slip = CONFIG["entry_slippage_pct"] / 100
    return price * (1 + slip) if side == "long" else price * (1 - slip)


def structural_stop(rows: list[list[Any]], idx: int, side: str) -> float:
    entry_raw = safe_float(rows[idx][4])
    low = safe_float(rows[idx][3])
    high = safe_float(rows[idx][2])
    buffer_dist = entry_raw * CONFIG["structural_stop_buffer_pct"] / 100
    min_dist = entry_raw * CONFIG["min_stop_pct"] / 100
    if side == "long":
        stop = low - buffer_dist
        return min(stop, entry_raw - min_dist)
    stop = high + buffer_dist
    return max(stop, entry_raw + min_dist)


def favorable_price(row: list[Any], side: str) -> float:
    return safe_float(row[2]) if side == "long" else safe_float(row[3])


def adverse_price(row: list[Any], side: str) -> float:
    return safe_float(row[3]) if side == "long" else safe_float(row[2])


def stop_hit(row: list[Any], stop: float, side: str) -> bool:
    return safe_float(row[3]) <= stop if side == "long" else safe_float(row[2]) >= stop


def structure_break(rows: list[list[Any]], j: int, side: str, k: int) -> bool:
    if j - k < 0:
        return False
    close = safe_float(rows[j][4])
    prev = rows[j - k : j]
    if side == "long":
        level = min(safe_float(row[3]) for row in prev)
        return close < level
    level = max(safe_float(row[2]) for row in prev)
    return close > level


def impulse_body_pct(rows: list[list[Any]], idx: int) -> float:
    o = safe_float(rows[idx][1])
    c = safe_float(rows[idx][4])
    return abs(c - o) / o * 100 if o > 0 else float("nan")


def movement_diagnostics(rows: list[list[Any]], idx: int, side: str, move_type: str) -> dict[str, Any]:
    impulse_open = safe_float(rows[idx][1])
    entry_raw = safe_float(rows[idx][4])
    end = min(len(rows) - 1, idx + hold_bars(move_type))
    best = 0.0
    worst = 0.0
    best_idx = idx
    worst_idx = idx
    for j in range(idx, end + 1):
        fav = favorable_price(rows[j], side)
        adv = adverse_price(rows[j], side)
        fav_ret = dir_return(impulse_open, fav, side)
        adv_ret = dir_return(impulse_open, adv, side)
        if fav_ret > best:
            best = fav_ret
            best_idx = j
        if adv_ret < worst:
            worst = adv_ret
            worst_idx = j
    threshold = CONFIG["edge_threshold_fast_pct"] if move_type == "FAST" else CONFIG["edge_threshold_swing_pct"]
    return {
        "impulse_open": impulse_open,
        "entry_raw": entry_raw,
        "entry_lag_pct": dir_return(impulse_open, entry_raw, side),
        "available_from_impulse_pct": best,
        "adverse_from_impulse_pct": worst,
        "available_peak_idx": best_idx,
        "adverse_idx": worst_idx,
        "edge_exists": best >= threshold,
        "mae_before_mfe": worst_idx < best_idx and abs(worst) > CONFIG["fee_pct"],
    }


def simulate_exit_mode(
    rows: list[list[Any]],
    idx: int,
    side: str,
    move_type: str,
    mode: str,
    param: float,
) -> dict[str, Any]:
    entry_raw = safe_float(rows[idx][4])
    entry = slipped_entry(entry_raw, side)
    stop = structural_stop(rows, idx, side)
    end = min(len(rows) - 1, idx + hold_bars(move_type))
    best = 0.0
    worst = 0.0
    best_since_entry = 0.0
    outcome = "TIME"
    exit_price = safe_float(rows[end][4])
    exit_idx = end
    tp = None
    if mode == "scaled_tp":
        dist_pct = max(impulse_body_pct(rows, idx) * param, CONFIG["min_stop_pct"])
        tp = entry * (1 + dist_pct / 100) if side == "long" else entry * (1 - dist_pct / 100)
    for j in range(idx + 1, end + 1):
        row = rows[j]
        fav = favorable_price(row, side)
        adv = adverse_price(row, side)
        close = safe_float(row[4])
        best = max(best, dir_return(entry, fav, side))
        worst = min(worst, dir_return(entry, adv, side))
        best_since_entry = max(best_since_entry, dir_return(entry, fav, side))
        if stop_hit(row, stop, side):
            outcome = "SL"
            exit_price = stop
            exit_idx = j
            break
        if mode == "structure":
            if structure_break(rows, j, side, int(param)):
                outcome = f"STRUCT_K{int(param)}"
                exit_price = close
                exit_idx = j
                break
        elif mode == "giveback":
            current = dir_return(entry, close, side)
            giveback = best_since_entry - current
            if best_since_entry > 0 and giveback >= best_since_entry * param / 100:
                outcome = f"GIVEBACK_{int(param)}"
                exit_price = close
                exit_idx = j
                break
        elif mode == "scaled_tp" and tp is not None:
            hit_tp = safe_float(row[2]) >= tp if side == "long" else safe_float(row[3]) <= tp
            if hit_tp:
                outcome = f"TP_MOVE_{int(param * 100)}"
                exit_price = tp
                exit_idx = j
                break
    gross = dir_return(entry, exit_price, side)
    return {
        "filled": True,
        "entry": entry,
        "entry_raw": entry_raw,
        "side": side,
        "stop": stop,
        "tp": tp,
        "exit_price": exit_price,
        "exit_idx": exit_idx,
        "outcome": outcome,
        "gross_pct": gross,
        "net_pct": gross - CONFIG["fee_pct"],
        "mfe_pct": best,
        "mae_pct": worst,
        "capture_pct": gross / best * 100 if best > 0 else float("nan"),
    }


def exit_specs() -> list[tuple[str, str, float]]:
    specs = []
    for k in CONFIG["structure_k"]:
        specs.append((f"structure_k{k}", "structure", float(k)))
    for giveback in CONFIG["giveback_pct"]:
        specs.append((f"giveback_{giveback}", "giveback", float(giveback)))
    for frac in CONFIG["scaled_tp_fracs"]:
        specs.append((f"scaled_tp_{int(frac * 100)}", "scaled_tp", float(frac)))
    return specs


def build_trade_rows(
    events: list[dict[str, Any]],
    candle_sets: dict[str, phase_a.CandleSet],
    start_close_ms: int,
    end_close_ms: int,
) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        symbol = event["symbol"]
        candle_set = candle_sets.get(symbol)
        if not candle_set:
            continue
        idx = phase_b.candle_idx(candle_set, "15m", int(event["start_open_ms"]))
        if idx is None:
            continue
        model = phase_b.model_for_event(event)
        cell = phase_b.event_cell(event)
        tier = phase_b.volatility_tier(symbol, candle_set)
        base = {
            "symbol": symbol,
            "ts": event["start_ts"],
            "idx": idx,
            "cell": cell,
            "model": model or "no_model",
            "tier": tier,
            "period": phase_b.event_period(event, start_close_ms, end_close_ms),
            "move_type": event["move_type"],
            "event_direction": event["direction"],
            "event_move_pct": safe_float(event["move_pct"]),
            "event_peak_bars": event["peak_bars_15m"],
            "skip_reason": "",
        }
        if model is None:
            base["skip_reason"] = "no_model_for_cell"
            rows.append(base)
            continue
        if model == "trend_grind_watch":
            base["skip_reason"] = "grind_watch_no_trade"
            rows.append(base)
            continue
        side = phase_b.side_from_structure(candle_set.rows["15m"], idx, model)
        if side is None:
            base["skip_reason"] = "no_structural_side"
            rows.append(base)
            continue
        engine = event.get("start_engine") or event.get("engine") or {}
        guard, guard_reason = phase_b.peak_guard(candle_set.rows["15m"], idx, side, engine)
        base["model_side"] = side
        base["side_match"] = side == event["direction"]
        if guard:
            base["skip_reason"] = f"peak_guard:{guard_reason}"
            rows.append(base)
            continue
        candle_rows = candle_set.rows["15m"]
        diagnostics = movement_diagnostics(candle_rows, idx, side, event["move_type"])
        old_sim = phase_b.simulate_model_exit(candle_rows, idx, side, model, event["move_type"])
        base["diagnostics"] = diagnostics
        base["old_fixed_tp"] = old_sim
        for exit_name, mode, param in exit_specs():
            sim = simulate_exit_mode(candle_rows, idx, side, event["move_type"], mode, param)
            row = dict(base)
            row["exit_name"] = exit_name
            row["exit_mode"] = mode
            row["exit_param"] = param
            row["new_exit"] = sim
            row["old_net_pct"] = safe_float(old_sim.get("net_pct"))
            row["new_net_pct"] = safe_float(sim.get("net_pct"))
            row["delta_net_pct"] = row["new_net_pct"] - row["old_net_pct"]
            old_gross = safe_float(old_sim.get("gross_pct"))
            old_mfe = safe_float(old_sim.get("mfe_pct"))
            new_gross = safe_float(sim.get("gross_pct"))
            row["old_capture_pct"] = max(old_gross, 0.0) / old_mfe * 100 if old_mfe > 0 else float("nan")
            row["new_capture_pct"] = max(new_gross, 0.0) / safe_float(sim.get("mfe_pct")) * 100 if safe_float(sim.get("mfe_pct")) > 0 else float("nan")
            row["available_capture_pct"] = max(new_gross, 0.0) / diagnostics["available_from_impulse_pct"] * 100 if diagnostics["available_from_impulse_pct"] > 0 else float("nan")
            rows.append(row)
    return rows


def empty_accum() -> dict[str, Any]:
    return {
        "n": 0,
        "filled": 0,
        "wins": 0,
        "old_net": [],
        "new_net": [],
        "delta": [],
        "new_capture": [],
        "old_capture": [],
        "available_capture": [],
        "available": [],
        "entry_lag": [],
        "edge_exists": 0,
        "mae_before_mfe": 0,
        "outcomes": Counter(),
        "side_known": 0,
        "side_matches": 0,
        "skips": Counter(),
    }


def update_accum(acc: dict[str, Any], row: dict[str, Any]) -> None:
    acc["n"] += 1
    if row.get("model_side") in {"long", "short"} and row.get("event_direction") in {"long", "short"}:
        acc["side_known"] += 1
        acc["side_matches"] += 1 if row["model_side"] == row["event_direction"] else 0
    if row.get("skip_reason"):
        acc["skips"][row["skip_reason"]] += 1
        return
    sim = row.get("new_exit") or {}
    old = row.get("old_fixed_tp") or {}
    diag = row.get("diagnostics") or {}
    if not sim.get("filled"):
        acc["skips"]["no_fill"] += 1
        return
    acc["filled"] += 1
    new_net = safe_float(sim.get("net_pct"))
    old_net = safe_float(old.get("net_pct"))
    acc["wins"] += 1 if new_net > 0 else 0
    acc["new_net"].append(new_net)
    acc["old_net"].append(old_net)
    acc["delta"].append(new_net - old_net)
    acc["new_capture"].append(safe_float(row.get("new_capture_pct")))
    acc["old_capture"].append(safe_float(row.get("old_capture_pct")))
    acc["available_capture"].append(safe_float(row.get("available_capture_pct")))
    acc["available"].append(safe_float(diag.get("available_from_impulse_pct")))
    acc["entry_lag"].append(safe_float(diag.get("entry_lag_pct")))
    acc["edge_exists"] += 1 if diag.get("edge_exists") else 0
    acc["mae_before_mfe"] += 1 if diag.get("mae_before_mfe") else 0
    acc["outcomes"][sim.get("outcome") or "UNKNOWN"] += 1


def finalize_accum(acc: dict[str, Any]) -> dict[str, Any]:
    filled = len([v for v in acc["new_net"] if math.isfinite(v)])
    return {
        "n": acc["n"],
        "filled": filled,
        "fill_rate": pct(filled, acc["n"]),
        "avg_old_net_pct": average(acc["old_net"]),
        "avg_new_net_pct": average(acc["new_net"]),
        "avg_delta_net_pct": average(acc["delta"]),
        "win_rate": pct(acc["wins"], filled),
        "avg_old_capture_pct": average(acc["old_capture"]),
        "avg_new_capture_pct": average(acc["new_capture"]),
        "avg_available_capture_pct": average(acc["available_capture"]),
        "avg_available_from_impulse_pct": average(acc["available"]),
        "avg_entry_lag_pct": average(acc["entry_lag"]),
        "edge_exists_rate": pct(acc["edge_exists"], filled),
        "mae_before_mfe_rate": pct(acc["mae_before_mfe"], filled),
        "side_match_rate": pct(acc["side_matches"], acc["side_known"]),
        "outcomes": dict(acc["outcomes"]),
        "skips": dict(acc["skips"]),
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accum: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(empty_accum)
    for row in rows:
        exit_name = row.get("exit_name", "no_exit")
        keys = [
            (row["cell"], row["model"], exit_name, "all", "both", "all"),
            (row["cell"], row["model"], exit_name, row.get("tier") or "unknown", "both", "all"),
            (row["cell"], row["model"], exit_name, "all", row.get("model_side") or "none", "all"),
            (row["cell"], row["model"], exit_name, "all", "both", row.get("period") or "all"),
        ]
        for key in keys:
            update_accum(accum[key], row)
    out = []
    for key, acc in accum.items():
        cell, model, exit_name, tier, side, period = key
        out.append(
            {
                "cell": cell,
                "model": model,
                "exit": exit_name,
                "tier": tier,
                "side": side,
                "period": period,
                **finalize_accum(acc),
            }
        )
    out.sort(key=lambda r: (r["cell"], r["model"], r["exit"], r["tier"], r["side"], r["period"]))
    return out


def main_summary_rows(summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in summary
        if row["tier"] == "all" and row["side"] == "both" and row["period"] == "all" and row["filled"] > 0
    ]


def best_exits(summary: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in main_summary_rows(summary):
        key = (row["cell"], row["model"])
        current = best.get(key)
        if current is None or safe_float(row["avg_new_net_pct"]) > safe_float(current["avg_new_net_pct"]):
            best[key] = row
    return best


def go_no_go(row: dict[str, Any], summary: list[dict[str, Any]]) -> str:
    if row["filled"] < 20:
        return "NO-GO: sample<20"
    if safe_float(row["avg_new_net_pct"]) <= 0:
        return "NO-GO: net<=0"
    side_rows = [
        r for r in summary
        if r["cell"] == row["cell"]
        and r["model"] == row["model"]
        and r["exit"] == row["exit"]
        and r["tier"] == "all"
        and r["period"] == "all"
        and r["side"] in {"long", "short"}
    ]
    if len(side_rows) < 2 or any(safe_float(r["avg_new_net_pct"]) <= 0 or r["filled"] < 20 for r in side_rows):
        return "NO-GO: side split fails"
    period_rows = [
        r for r in summary
        if r["cell"] == row["cell"]
        and r["model"] == row["model"]
        and r["exit"] == row["exit"]
        and r["tier"] == "all"
        and r["side"] == "both"
        and r["period"] in {"early", "late"}
    ]
    if len(period_rows) < 2 or any(safe_float(r["avg_new_net_pct"]) <= 0 or r["filled"] < 10 for r in period_rows):
        return "NO-GO: early/late split fails"
    return "GO"


def rows_for_exit(rows: list[dict[str, Any]], exit_name: str) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("exit_name") == exit_name and not row.get("skip_reason")]


def render_case(path: Path, row: dict[str, Any], candle_set: phase_a.CandleSet) -> None:
    rows = candle_set.rows["15m"]
    idx = int(row["idx"])
    sim = row["new_exit"]
    old = row["old_fixed_tp"]
    diag = row["diagnostics"]
    end_idx = min(len(rows) - 1, max(int(sim["exit_idx"]), int(diag["available_peak_idx"])) + 6)
    start_idx = max(0, idx - 8)
    subset = rows[start_idx : end_idx + 1]
    x0 = idx - start_idx
    exit_x = int(sim["exit_idx"]) - start_idx
    peak_x = int(diag["available_peak_idx"]) - start_idx
    fig, ax = plt.subplots(figsize=(13, 6))
    for i, cndl in enumerate(subset):
        o, h, l, c = map(safe_float, cndl[1:5])
        color = "#15936b" if c >= o else "#c23b3b"
        ax.vlines(i, l, h, color=color, linewidth=0.9)
        ax.add_patch(patches.Rectangle((i - 0.35, min(o, c)), 0.7, max(abs(c - o), (h - l) * 0.02), color=color, alpha=0.78))
    ax.scatter([x0], [diag["impulse_open"]], color="#6f42c1", s=52, zorder=8, label="impulse start")
    ax.scatter([x0], [sim["entry_raw"]], color="#0b5bd3", s=46, zorder=8, label="model entry")
    ax.scatter([peak_x], [favorable_price(rows[int(diag["available_peak_idx"])], row["model_side"])], color="#f0ad00", s=44, zorder=8, label="MFE peak")
    ax.axhline(sim["stop"], color="#d62728", linestyle="--", linewidth=0.8, label="struct stop")
    if old.get("tp") is not None:
        ax.axhline(old["tp"], color="#2ca02c", linestyle=":", linewidth=0.8, label="old fixed TP")
    ax.axvline(exit_x, color="#111111", linestyle=":", linewidth=1.0, label=row["exit_name"])
    ax.set_title(
        f"{row['cell']} {row['symbol']} {row['ts']} {row['model_side']} | old {fmt(row['old_net_pct'], '%')} new {fmt(row['new_net_pct'], '%')} lag {fmt(diag['entry_lag_pct'], '%')} cap {fmt(row['new_capture_pct'], '%')}",
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


def generate_cases(rows: list[dict[str, Any]], summary: list[dict[str, Any]], candle_sets: dict[str, phase_a.CandleSet]) -> None:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    LATE_CASE_DIR.mkdir(parents=True, exist_ok=True)
    for folder in [CASE_DIR, LATE_CASE_DIR]:
        for old in folder.glob(f"*_{SUFFIX}.png"):
            old.unlink()
    best = best_exits(summary)
    for (cell, model), best_row in best.items():
        candidates = [
            row for row in rows
            if row.get("cell") == cell and row.get("model") == model and row.get("exit_name") == best_row["exit"] and not row.get("skip_reason")
        ]
        candidates.sort(key=lambda row: safe_float(row.get("delta_net_pct")), reverse=True)
        for i, row in enumerate(candidates[: CONFIG["case_limit_per_cell"]], start=1):
            candle_set = candle_sets.get(row["symbol"])
            if not candle_set:
                continue
            safe_ts = row["ts"].replace(":", "").replace("-", "").replace("Z", "")
            path = CASE_DIR / f"{cell.lower()}_{best_row['exit']}_{i:02d}_{row['symbol']}_{safe_ts}_{SUFFIX}.png"
            render_case(path, row, candle_set)
    late = [
        row for row in rows
        if not row.get("skip_reason")
        and row.get("exit_mode") in {"structure", "giveback"}
        and (row.get("new_exit") or {}).get("outcome") in {"TIME", "SL"}
        and safe_float((row.get("diagnostics") or {}).get("entry_lag_pct")) > 0.5
    ]
    late.sort(key=lambda row: safe_float((row.get("diagnostics") or {}).get("entry_lag_pct")), reverse=True)
    for i, row in enumerate(late[: CONFIG["late_case_limit"]], start=1):
        candle_set = candle_sets.get(row["symbol"])
        if not candle_set:
            continue
        safe_ts = row["ts"].replace(":", "").replace("-", "").replace("Z", "")
        path = LATE_CASE_DIR / f"late_{i:02d}_{row['symbol']}_{safe_ts}_{row['exit_name']}_{SUFFIX}.png"
        render_case(path, row, candle_set)


def render_report(rows: list[dict[str, Any]], summary: list[dict[str, Any]], start_close_ms: int, end_close_ms: int) -> str:
    best = best_exits(summary)
    best_rows = []
    for key, row in sorted(best.items()):
        best_rows.append(
            [
                row["cell"],
                row["model"],
                row["exit"],
                row["filled"],
                fmt(row["avg_old_net_pct"], "%"),
                fmt(row["avg_new_net_pct"], "%"),
                fmt(row["avg_delta_net_pct"], "%"),
                fmt(row["avg_old_capture_pct"], "%"),
                fmt(row["avg_new_capture_pct"], "%"),
                fmt(row["avg_entry_lag_pct"], "%"),
                fmt(row["edge_exists_rate"], "%"),
                go_no_go(row, summary),
            ]
        )
    top_grid = sorted(main_summary_rows(summary), key=lambda r: safe_float(r["avg_new_net_pct"]), reverse=True)[:20]
    grid_rows = [
        [
            r["cell"],
            r["model"],
            r["exit"],
            r["filled"],
            fmt(r["avg_new_net_pct"], "%"),
            fmt(r["avg_delta_net_pct"], "%"),
            fmt(r["avg_new_capture_pct"], "%"),
            fmt(r["win_rate"], "%"),
            fmt(r["mae_before_mfe_rate"], "%"),
        ]
        for r in top_grid
    ]
    side_rows = []
    period_rows = []
    tier_rows = []
    for best_row in best.values():
        side_rows.extend(
            [
                [r["cell"], r["exit"], r["side"], r["filled"], fmt(r["avg_new_net_pct"], "%"), fmt(r["avg_delta_net_pct"], "%")]
                for r in summary
                if r["cell"] == best_row["cell"]
                and r["model"] == best_row["model"]
                and r["exit"] == best_row["exit"]
                and r["tier"] == "all"
                and r["period"] == "all"
                and r["side"] in {"long", "short"}
                and r["filled"] > 0
            ]
        )
        period_rows.extend(
            [
                [r["cell"], r["exit"], r["period"], r["filled"], fmt(r["avg_new_net_pct"], "%"), fmt(r["avg_delta_net_pct"], "%")]
                for r in summary
                if r["cell"] == best_row["cell"]
                and r["model"] == best_row["model"]
                and r["exit"] == best_row["exit"]
                and r["tier"] == "all"
                and r["side"] == "both"
                and r["period"] in {"early", "late"}
                and r["filled"] > 0
            ]
        )
        tier_rows.extend(
            [
                [r["cell"], r["exit"], r["tier"], r["filled"], fmt(r["avg_new_net_pct"], "%"), fmt(r["avg_delta_net_pct"], "%")]
                for r in summary
                if r["cell"] == best_row["cell"]
                and r["model"] == best_row["model"]
                and r["exit"] == best_row["exit"]
                and r["tier"] != "all"
                and r["side"] == "both"
                and r["period"] == "all"
                and r["filled"] > 0
            ]
        )
    outcome_counts = Counter((row.get("new_exit") or {}).get("outcome") for row in rows if row.get("new_exit"))
    lines = [
        "# Regime Exit Re-run - 22.05.2026",
        "",
        f"Replay period: `{iso_from_ms(start_close_ms)}` to `{iso_from_ms(end_close_ms)}`. Entry, direction, regime labels, peak guard, universe, fee and slippage are kept from Phase B. Only exit and initial stop are changed.",
        "",
        "## Exit Models Tested",
        "",
        "- `old fixed TP`: yesterday's baseline, fixed `1.1R/1.4R` off ATR stop.",
        "- `structure_k1/2/3`: structural stop behind impulse-bar extreme plus buffer, then ride until a closed 15m candle breaks the previous k-bar swing level.",
        "- `giveback_30/40/50`: structural initial stop, then exit after giving back X% of best favorable excursion.",
        "- `scaled_tp_50/75/100`: TP distance is scaled to the impulse candle body, not ATR.",
        "",
        "## Best Exit Per Cell",
        "",
    ]
    lines.extend(render_table(["cell", "model", "best exit", "filled", "old net", "new net", "delta", "old cap", "new cap", "entry lag", "edge exists", "verdict"], best_rows))
    lines.extend(["", "## Top Exit Grid", ""])
    lines.extend(render_table(["cell", "model", "exit", "filled", "new net", "delta", "capture", "WR", "MAE before MFE"], grid_rows))
    lines.extend(["", "## Side Split For Best Exits", ""])
    lines.extend(render_table(["cell", "exit", "side", "filled", "new net", "delta"], side_rows))
    lines.extend(["", "## Early/Late Split For Best Exits", ""])
    lines.extend(render_table(["cell", "exit", "period", "filled", "new net", "delta"], period_rows))
    lines.extend(["", "## Volatility Tier Split For Best Exits", ""])
    lines.extend(render_table(["cell", "exit", "tier", "filled", "new net", "delta"], tier_rows))
    lines.extend(
        [
            "",
            "## Execution Diagnostics",
            "",
            f"- new-exit outcomes: `{dict(outcome_counts)}`",
            "- `entry lag` is directional move already passed from impulse-bar open to model entry close.",
            "- `edge exists` separates cases where the movement existed from cases where there was not enough movement in the model side.",
            "- `MAE before MFE` flags stop/noise arriving before the favorable move.",
            "",
            "## Verdict",
            "",
            "Ride-style exits improve the measurement in several cells, especially where the old TP clipped the first ATR-sized fragment. The strict production criterion is still applied without relaxing side or time stability. Cells that improve only on one side or one volatility tier are research candidates, not config changes.",
            "",
            "## GPT Hypotheses",
            "",
            "- If a cell has high `edge exists` but low capture, the problem is execution timing/exit, not absence of setup edge.",
            "- Structure exits should help high-volatility impulse cells more than majors because their impulse size is larger than fee and stop noise.",
            "- Giveback exits can over-hold flat/no-edge moves; those should be separated by `edge exists` and entry-lag diagnostics.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")
    _, candle_sets, _, events, start_close_ms, end_close_ms = load_replay()
    rows = build_trade_rows(events, candle_sets, start_close_ms, end_close_ms)
    summary = summarize(rows)
    generate_cases(rows, summary, candle_sets)
    REPORT_MD.write_text(render_report(rows, summary, start_close_ms, end_close_ms), encoding="utf-8")
    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": CONFIG,
        "period": {"start": iso_from_ms(start_close_ms), "end": iso_from_ms(end_close_ms)},
        "event_count": len(events),
        "row_count": len(rows),
        "summary": summary,
        "best_exits": list(best_exits(summary).values()),
        "case_dir": str(CASE_DIR),
        "late_case_dir": str(LATE_CASE_DIR),
    }
    SUMMARY_JSON.write_text(json.dumps(json_safe(payload), ensure_ascii=True, indent=2, allow_nan=False), encoding="utf-8")
    log(f"saved {REPORT_MD}")
    log(f"saved {SUMMARY_JSON}")
    log(f"saved cases to {CASE_DIR}")
    log(f"saved late cases to {LATE_CASE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
