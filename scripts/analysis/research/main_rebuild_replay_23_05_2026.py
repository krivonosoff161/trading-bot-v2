"""Main rebuild replay - 23.05.2026.

Adds the one dimension the exit re-run never varied: ENTRY TIMING.

The exit re-run (22.05) fixed the exit (ride/structural) but always entered on the
15m impulse-bar CLOSE, which is the late entry the diagnosis blames. Here we sweep
entry_mode in {close, mid, open} against the same ride/structural exits, structural
stop (widened for SWING), fee and slippage from the exit-rerun harness.

- close = current production behaviour (enter on 15m close, ride from next bar).
- open  = earliest bound (enter at impulse-bar open, ride from same bar). Overstates
          the gain; the live tick layer lands between mid and open.
- mid   = realistic proxy (enter mid impulse bar).

Direction, regime labels, peak guard, universe come from Phase A/B unchanged. We do
NOT touch production code. Acceptance = NET money > 0 after fee, both sides, n>=20.

Run:
    python scripts/analysis/research/main_rebuild_replay_23_05_2026.py
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import regime_coverage_research_21_05_2026 as phase_a
import regime_model_phaseB_21_05_2026 as phase_b
import regime_exit_rerun_22_05_2026 as base


SUFFIX = "23_05_2026"
OUT_DIR = base.OUT_DIR
REPORT_MD = OUT_DIR / f"main_rebuild_replay_report_{SUFFIX}.md"
SUMMARY_JSON = OUT_DIR / f"main_rebuild_replay_summary_{SUFFIX}.json"
RUN_LOG = OUT_DIR / f"main_rebuild_replay_run_{SUFFIX}.log"

ENTRY_MODES = ["close", "mid", "open"]
SWING_MIN_STOP_PCT = 0.80  # widen swing stop vs base 0.35 (do not get tagged by trend pullback)


def log(message: str) -> None:
    print(message)
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def entry_price_and_start(rows: list[list[Any]], idx: int, mode: str) -> tuple[float, int]:
    o = base.safe_float(rows[idx][1])
    c = base.safe_float(rows[idx][4])
    if mode == "close":
        return c, idx + 1            # ride from the bar after the impulse close (current)
    if mode == "open":
        return o, idx                # earliest bound, include the impulse bar itself
    return (o + c) / 2.0, idx         # mid


def adaptive_stop(rows: list[list[Any]], idx: int, side: str, move_type: str) -> float:
    entry_raw = base.safe_float(rows[idx][4])
    low = base.safe_float(rows[idx][3])
    high = base.safe_float(rows[idx][2])
    buffer_dist = entry_raw * base.CONFIG["structural_stop_buffer_pct"] / 100
    min_pct = SWING_MIN_STOP_PCT if move_type == "SWING" else base.CONFIG["min_stop_pct"]
    min_dist = entry_raw * min_pct / 100
    if side == "long":
        return min(low - buffer_dist, entry_raw - min_dist)
    return max(high + buffer_dist, entry_raw + min_dist)


def simulate(rows: list[list[Any]], idx: int, side: str, move_type: str, mode: str, param: float, entry_mode: str) -> dict[str, Any]:
    entry_raw, jstart = entry_price_and_start(rows, idx, entry_mode)
    entry = base.slipped_entry(entry_raw, side)
    stop = adaptive_stop(rows, idx, side, move_type)
    end = min(len(rows) - 1, idx + base.hold_bars(move_type))
    best = 0.0
    worst = 0.0
    best_since_entry = 0.0
    outcome = "TIME"
    exit_price = base.safe_float(rows[end][4])
    exit_idx = end
    tp = None
    if mode == "scaled_tp":
        dist_pct = max(base.impulse_body_pct(rows, idx) * param, base.CONFIG["min_stop_pct"])
        tp = entry * (1 + dist_pct / 100) if side == "long" else entry * (1 - dist_pct / 100)
    for j in range(jstart, end + 1):
        row = rows[j]
        fav = base.favorable_price(row, side)
        adv = base.adverse_price(row, side)
        close = base.safe_float(row[4])
        best = max(best, base.dir_return(entry, fav, side))
        worst = min(worst, base.dir_return(entry, adv, side))
        best_since_entry = max(best_since_entry, base.dir_return(entry, fav, side))
        if base.stop_hit(row, stop, side):
            outcome = "SL"
            exit_price = stop
            exit_idx = j
            break
        if mode == "structure":
            if base.structure_break(rows, j, side, int(param)):
                outcome = f"STRUCT_K{int(param)}"
                exit_price = close
                exit_idx = j
                break
        elif mode == "giveback":
            current = base.dir_return(entry, close, side)
            giveback = best_since_entry - current
            if best_since_entry > 0 and giveback >= best_since_entry * param / 100:
                outcome = f"GIVEBACK_{int(param)}"
                exit_price = close
                exit_idx = j
                break
        elif mode == "scaled_tp" and tp is not None:
            hit_tp = base.safe_float(row[2]) >= tp if side == "long" else base.safe_float(row[3]) <= tp
            if hit_tp:
                outcome = f"TP_MOVE_{int(param * 100)}"
                exit_price = tp
                exit_idx = j
                break
    gross = base.dir_return(entry, exit_price, side)
    return {
        "filled": True,
        "entry": entry,
        "side": side,
        "stop": stop,
        "outcome": outcome,
        "gross_pct": gross,
        "net_pct": gross - base.CONFIG["fee_pct"],
        "mfe_pct": best,
        "mae_pct": worst,
        "capture_pct": gross / best * 100 if best > 0 else float("nan"),
    }


def build_rows(events, candle_sets, start_close_ms, end_close_ms) -> list[dict[str, Any]]:
    rows = []
    specs = base.exit_specs()
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
        base_row = {
            "symbol": symbol,
            "ts": event["start_ts"],
            "idx": idx,
            "cell": cell,
            "model": model or "no_model",
            "tier": tier,
            "period": phase_b.event_period(event, start_close_ms, end_close_ms),
            "move_type": event["move_type"],
            "event_direction": event["direction"],
            "skip_reason": "",
        }
        if model is None or model == "trend_grind_watch":
            base_row["skip_reason"] = "no_model" if model is None else "grind_watch"
            rows.append(base_row)
            continue
        candle_rows = candle_set.rows["15m"]
        side = phase_b.side_from_structure(candle_rows, idx, model)
        if side is None:
            base_row["skip_reason"] = "no_structural_side"
            rows.append(base_row)
            continue
        engine = event.get("start_engine") or event.get("engine") or {}
        guard, guard_reason = phase_b.peak_guard(candle_rows, idx, side, engine)
        base_row["model_side"] = side
        if guard:
            base_row["skip_reason"] = f"peak_guard:{guard_reason}"
            rows.append(base_row)
            continue
        diagnostics = base.movement_diagnostics(candle_rows, idx, side, event["move_type"])
        old_sim = phase_b.simulate_model_exit(candle_rows, idx, side, model, event["move_type"])
        for entry_mode in ENTRY_MODES:
            for exit_name, mode, param in specs:
                sim = simulate(candle_rows, idx, side, event["move_type"], mode, param, entry_mode)
                row = dict(base_row)
                row["exit_name"] = f"{entry_mode}+{exit_name}"  # encode entry mode so base.summarize/go_no_go work unchanged
                row["entry_mode"] = entry_mode
                row["base_exit"] = exit_name
                row["diagnostics"] = diagnostics
                row["old_fixed_tp"] = old_sim
                row["new_exit"] = sim
                row["old_net_pct"] = base.safe_float(old_sim.get("net_pct"))
                row["new_net_pct"] = base.safe_float(sim.get("net_pct"))
                row["delta_net_pct"] = row["new_net_pct"] - row["old_net_pct"]
                new_gross = base.safe_float(sim.get("gross_pct"))
                old_gross = base.safe_float(old_sim.get("gross_pct"))
                old_mfe = base.safe_float(old_sim.get("mfe_pct"))
                row["new_capture_pct"] = max(new_gross, 0.0) / base.safe_float(sim.get("mfe_pct")) * 100 if base.safe_float(sim.get("mfe_pct")) > 0 else float("nan")
                row["old_capture_pct"] = max(old_gross, 0.0) / old_mfe * 100 if old_mfe > 0 else float("nan")
                avail = diagnostics["available_from_impulse_pct"]
                row["available_capture_pct"] = max(new_gross, 0.0) / avail * 100 if avail > 0 else float("nan")
                rows.append(row)
    return rows


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")
    _, candle_sets, _, events, start_close_ms, end_close_ms = base.load_replay()
    rows = build_rows(events, candle_sets, start_close_ms, end_close_ms)
    summary = base.summarize(rows)
    main_rows = base.main_summary_rows(summary)

    # Best exit per (cell, entry_mode): exit_name is "<entry_mode>+<exit>"
    best_by_cell_entry: dict[tuple[str, str], dict[str, Any]] = {}
    for row in main_rows:
        entry_mode = row["exit"].split("+")[0]
        key = (row["cell"], entry_mode)
        cur = best_by_cell_entry.get(key)
        if cur is None or base.safe_float(row["avg_new_net_pct"]) > base.safe_float(cur["avg_new_net_pct"]):
            best_by_cell_entry[key] = row

    # ---- console + report ----
    headers = ["cell", "entry", "best exit", "filled", "OLD net", "NEW net", "delta", "WR", "verdict"]
    table = []
    for (cell, entry_mode), row in sorted(best_by_cell_entry.items()):
        table.append([
            cell, entry_mode, row["exit"].split("+", 1)[1], row["filled"],
            base.fmt(row["avg_old_net_pct"], "%"), base.fmt(row["avg_new_net_pct"], "%"),
            base.fmt(row["avg_delta_net_pct"], "%"), base.fmt(row["win_rate"], "%"),
            base.go_no_go(row, summary),
        ])

    log("")
    log("=== BEST EXIT PER (CELL x ENTRY MODE) ===")
    log(" | ".join(headers))
    for r in table:
        log(" | ".join(str(x) for x in r))

    lines = [
        f"# Main Rebuild Replay - {SUFFIX}",
        "",
        f"Replay `{base.iso_from_ms(start_close_ms)}` -> `{base.iso_from_ms(end_close_ms)}`. "
        "Same direction/regime/peak-guard/universe/fee/slippage as the exit re-run. "
        "New axis: entry_mode (close=current late entry, mid, open=early bound). "
        "Stop is structural, widened for SWING. Acceptance = NET>0 after fee, both sides, n>=20.",
        "",
        "## Best Exit Per Cell x Entry Mode",
        "",
    ]
    lines.extend(base.render_table(headers, table))

    # entry-timing isolation: fix exit = structure_k2, compare entry modes
    lines.extend(["", "## Entry-Timing Effect (exit fixed = structure_k2)", ""])
    iso_rows = []
    for cell in sorted({r["cell"] for r in main_rows}):
        for entry_mode in ENTRY_MODES:
            match = [r for r in main_rows if r["cell"] == cell and r["exit"] == f"{entry_mode}+structure_k2"]
            if match:
                r = match[0]
                iso_rows.append([cell, entry_mode, r["filled"], base.fmt(r["avg_new_net_pct"], "%"),
                                 base.fmt(r["avg_delta_net_pct"], "%"), base.fmt(r["avg_entry_lag_pct"], "%"),
                                 base.fmt(r["avg_new_capture_pct"], "%")])
    lines.extend(base.render_table(["cell", "entry", "filled", "new net", "delta", "entry lag", "capture"], iso_rows))

    # side split for the best (cell, entry) rows
    lines.extend(["", "## Side Split For Best (Cell x Entry)", ""])
    side_rows = []
    for (cell, entry_mode), best_row in sorted(best_by_cell_entry.items()):
        for r in summary:
            if (r["cell"] == cell and r["exit"] == best_row["exit"] and r["tier"] == "all"
                    and r["period"] == "all" and r["side"] in {"long", "short"} and r["filled"] > 0):
                side_rows.append([cell, entry_mode, best_row["exit"].split("+", 1)[1], r["side"],
                                  r["filled"], base.fmt(r["avg_new_net_pct"], "%")])
    lines.extend(base.render_table(["cell", "entry", "exit", "side", "filled", "new net"], side_rows))

    lines.extend([
        "",
        "## Read",
        "",
        "- If NEW net turns positive only as entry_mode moves close->mid->open, the binding "
        "constraint is entry timing, and a tick early-entry layer is the unlock.",
        "- `open` overstates the gain (enters at impulse-bar open); the live tick layer lands "
        "between mid and open. Treat `mid` as the conservative read.",
        "- A cell that stays NO-GO even at `open` is not an entry problem - it is direction or "
        "no-edge, do not chase it with a faster entry.",
    ])
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "entry_modes": ENTRY_MODES,
        "swing_min_stop_pct": SWING_MIN_STOP_PCT,
        "period": {"start": base.iso_from_ms(start_close_ms), "end": base.iso_from_ms(end_close_ms)},
        "event_count": len(events),
        "best_by_cell_entry": {f"{c}|{e}": base.json_safe(r) for (c, e), r in best_by_cell_entry.items()},
        "summary": base.json_safe(base.main_summary_rows(summary)),
    }
    SUMMARY_JSON.write_text(json.dumps(payload, ensure_ascii=True, indent=2, allow_nan=False), encoding="utf-8")
    log("")
    log(f"saved {REPORT_MD}")
    log(f"saved {SUMMARY_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
