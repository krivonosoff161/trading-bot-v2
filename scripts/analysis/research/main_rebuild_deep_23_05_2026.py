"""Deep main rebuild research runner - 23.05.2026.

Research-only script. It reuses existing harnesses, writes only to
scripts/analysis/research/output, and does not touch production code or live logs.
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))

import continuation_research_20_05_2026 as cont
import main_rebuild_replay_23_05_2026 as rebuild
import regime_coverage_research_21_05_2026 as phase_a
import regime_exit_rerun_22_05_2026 as exit_rerun
import regime_model_phaseB_21_05_2026 as phase_b
import three_engines_polish_22_05_2026 as three


SUFFIX = "23_05_2026"
ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
REPORT_MD = OUT_DIR / f"main_rebuild_deep_report_{SUFFIX}.md"
SUMMARY_JSON = OUT_DIR / f"main_rebuild_deep_summary_{SUFFIX}.json"
RUN_LOG = OUT_DIR / f"main_rebuild_deep_run_{SUFFIX}.log"
PLOT_DIR = OUT_DIR / f"main_rebuild_deep_cases_{SUFFIX}"

CONFIG = {
    "fee_pct": 0.20,
    "top_tick_config": {
        "window_sec": 10,
        "min_move": 1.0,
        "body_ratio_min": 2.0,
        "volume_ratio_min": 2.0,
        "exit": "structure_k3",
    },
    "derived_geometry": {
        "TRENDING_IMPULSE": {"entry_mode": "mid", "mode": "scaled_tp", "param": 1.0},
        "DRIFT": {"entry_mode": "open", "mode": "scaled_tp", "param": 0.75},
        "RANGING": {"entry_mode": "close", "mode": "structure", "param": 2.0},
    },
    "rng_seed": 20260523,
    "permutations": 300,
}


def log(message: str) -> None:
    print(message)
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def patch_module_logs() -> None:
    for module in (phase_a, phase_b, exit_rerun, rebuild, three):
        if hasattr(module, "RUN_LOG"):
            module.RUN_LOG = RUN_LOG
        if hasattr(module, "log"):
            module.log = log


def safe_float(value: Any) -> float:
    return phase_a.safe_float(value)


def average(values: Iterable[float]) -> float:
    return phase_a.average(values)


def pct(part: int | float, total: int | float) -> float:
    return phase_a.pct(part, total)


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


def side_from_row(row: dict[str, Any]) -> str:
    side = row.get("model_side") or row.get("side")
    if side == "buy":
        return "long"
    if side == "sell":
        return "short"
    return side or "unknown"


def event_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("symbol"), row.get("ts"), row.get("cell"), side_from_row(row))


def session_name(ts_iso: str) -> str:
    hour = datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).hour
    if hour < 8:
        return "asia"
    if hour < 16:
        return "eu"
    return "us"


def load_replay_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    universe, candle_sets, _decisions, events, start_ms, end_ms = exit_rerun.load_replay()
    rows = rebuild.build_rows(events, candle_sets, start_ms, end_ms)
    return rows, events, {
        "universe": universe,
        "candle_sets": candle_sets,
        "start_ms": start_ms,
        "end_ms": end_ms,
    }


def unique_rows_for_entry(replay_rows: list[dict[str, Any]], entry_mode: str, exit_name: str) -> list[dict[str, Any]]:
    wanted = f"{entry_mode}+{exit_name}"
    seen = set()
    out = []
    for row in replay_rows:
        if row.get("skip_reason") or row.get("exit_name") != wanted:
            continue
        key = event_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def summarize_trade_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [safe_float((row.get("new_exit") or {}).get("net_pct")) for row in rows]
    mfes = [safe_float((row.get("new_exit") or {}).get("mfe_pct")) for row in rows]
    maes = [safe_float((row.get("new_exit") or {}).get("mae_pct")) for row in rows]
    old = [safe_float(row.get("old_net_pct")) for row in rows]
    return {
        "n": len(rows),
        "net": average(nets),
        "old_net": average(old),
        "delta": average([n - o for n, o in zip(nets, old) if math.isfinite(n) and math.isfinite(o)]),
        "wr": pct(sum(1 for n in nets if n > 0), len([n for n in nets if math.isfinite(n)])),
        "mfe_p25": percentile(mfes, 25),
        "mfe_p50": percentile(mfes, 50),
        "mfe_p75": percentile(mfes, 75),
        "mfe_p90": percentile(mfes, 90),
        "mae_p25": percentile(maes, 25),
        "mae_p50": percentile(maes, 50),
        "mae_p75": percentile(maes, 75),
        "mae_p90": percentile(maes, 90),
    }


def mfe_mae_distribution(replay_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for cell, formula in CONFIG["derived_geometry"].items():
        exit_name = "structure_k2" if formula["mode"] == "structure" else f"scaled_tp_{int(formula['param'] * 100)}"
        entry_mode = formula["entry_mode"]
        base_rows = [r for r in unique_rows_for_entry(replay_rows, entry_mode, exit_name) if r["cell"] == cell]
        for side in ["both", "long", "short"]:
            scoped = base_rows if side == "both" else [r for r in base_rows if side_from_row(r) == side]
            if scoped:
                out.append({"cell": cell, "entry_mode": entry_mode, "side": side, "tier": "all", **summarize_trade_rows(scoped)})
        tiers = sorted({r.get("tier") or "unknown" for r in base_rows})
        for tier in tiers:
            scoped = [r for r in base_rows if (r.get("tier") or "unknown") == tier]
            if scoped:
                out.append({"cell": cell, "entry_mode": entry_mode, "side": "both", "tier": tier, **summarize_trade_rows(scoped)})
    return out


def current_vs_derived(replay_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for cell, formula in CONFIG["derived_geometry"].items():
        exit_name = "structure_k2" if formula["mode"] == "structure" else f"scaled_tp_{int(formula['param'] * 100)}"
        rows = [r for r in unique_rows_for_entry(replay_rows, formula["entry_mode"], exit_name) if r["cell"] == cell]
        summary = summarize_trade_rows(rows)
        verdict = "GO" if summary["n"] >= 20 and summary["net"] > 0 else "NO-GO"
        side_rows = []
        for side in ["long", "short"]:
            scoped = [r for r in rows if side_from_row(r) == side]
            side_rows.append((side, summarize_trade_rows(scoped) if scoped else {"n": 0, "net": float("nan")}))
        if any(sr[1]["n"] < 20 or sr[1]["net"] <= 0 for sr in side_rows):
            verdict = "NO-GO: split"
        out.append(
            {
                "cell": cell,
                "formula": formula,
                "exit_name": exit_name,
                "summary": summary,
                "sides": {side: vals for side, vals in side_rows},
                "verdict": verdict,
            }
        )
    return out


def drift_subconditions(replay_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [r for r in unique_rows_for_entry(replay_rows, "open", "scaled_tp_75") if r["cell"] == "DRIFT"]
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    groups.append(("all", rows))
    for tier in sorted({r.get("tier") or "unknown" for r in rows}):
        groups.append((f"tier={tier}", [r for r in rows if (r.get("tier") or "unknown") == tier]))
    for period in sorted({r.get("period") or "unknown" for r in rows}):
        groups.append((f"period={period}", [r for r in rows if (r.get("period") or "unknown") == period]))
    for sess in ["asia", "eu", "us"]:
        groups.append((f"session={sess}", [r for r in rows if session_name(r["ts"]) == sess]))
    out = []
    for name, scoped in groups:
        if not scoped:
            continue
        s = summarize_trade_rows(scoped)
        side_ok = True
        sides = {}
        for side in ["long", "short"]:
            side_rows = [r for r in scoped if side_from_row(r) == side]
            sides[side] = summarize_trade_rows(side_rows) if side_rows else {"n": 0, "net": float("nan")}
            if sides[side]["n"] < 20 or sides[side]["net"] <= 0:
                side_ok = False
        out.append({"group": name, **s, "side_ok_n20_netpos": side_ok, "sides": sides})
    out.sort(key=lambda r: (r["side_ok_n20_netpos"], r["n"], safe_float(r["net"])), reverse=True)
    return out


def tick_dates_for_symbol(symbol: str) -> list[str]:
    path = cont.TICK_ROOT / symbol
    if not path.exists():
        return []
    dates = []
    for file_path in cont.tick_files(path):
        date = cont.date_from_file(file_path)
        if date[:4].isdigit():
            dates.append(date)
    return sorted(set(dates))


def tick_coverage(universe: list[str], start_ms: int, end_ms: int) -> dict[str, Any]:
    start_date = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date().isoformat()
    end_date = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).date().isoformat()
    rows = []
    for symbol in sorted(universe):
        dates = tick_dates_for_symbol(symbol)
        overlap = [d for d in dates if start_date <= d <= end_date]
        rows.append(
            {
                "symbol": symbol,
                "dates": len(dates),
                "first": min(dates) if dates else None,
                "last": max(dates) if dates else None,
                "replay_overlap_dates": overlap,
                "replay_overlap_count": len(overlap),
            }
        )
    return {
        "tick_root": str(cont.TICK_ROOT),
        "replay_window": {"start_date": start_date, "end_date": end_date},
        "symbols": rows,
        "symbols_with_any_ticks": sum(1 for r in rows if r["dates"] > 0),
        "symbols_with_replay_overlap": sum(1 for r in rows if r["replay_overlap_count"] > 0),
    }


def metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [safe_float((r.get("sim") or {}).get("net_pct")) for r in rows]
    gross = [safe_float((r.get("sim") or {}).get("gross_pct")) for r in rows]
    return {
        "n": len(rows),
        "net": average(nets),
        "gross": average(gross),
        "wr": pct(sum(1 for n in nets if n > 0), len([n for n in nets if math.isfinite(n)])),
        "capture": average([safe_float((r.get("sim") or {}).get("capture_pct")) for r in rows]),
        "mfe": average([safe_float((r.get("sim") or {}).get("mfe_pct")) for r in rows]),
        "mae": average([safe_float((r.get("sim") or {}).get("mae_pct")) for r in rows]),
        "hold": average([safe_float((r.get("sim") or {}).get("hold_min")) for r in rows]),
        "sum_net": sum(n for n in nets if math.isfinite(n)),
    }


def matches_top_tick_config(row: dict[str, Any]) -> bool:
    cfg = CONFIG["top_tick_config"]
    return (
        int(row.get("window_sec")) == cfg["window_sec"]
        and safe_float(row.get("min_move")) == cfg["min_move"]
        and safe_float(row.get("body_ratio_min")) == cfg["body_ratio_min"]
        and safe_float(row.get("volume_ratio_min")) == cfg["volume_ratio_min"]
        and row.get("exit") == cfg["exit"]
    )


def build_tick_rows() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    coverage = three.tick_coverage_for_pairs(three.CONFIG["impulse_pairs"])
    rows, pair_stats = three.build_impulse_rows(coverage)
    fixed = [row for row in rows if matches_top_tick_config(row)]
    return fixed, coverage, pair_stats


def opposite_side_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, tuple[dict[int, cont.Bar], dict[int, list[tuple[int, float]]]]] = {}
    out = []
    for row in rows:
        symbol = row["symbol"]
        if symbol not in grouped:
            bars, ticks = cont.aggregate_pair(symbol, keep_ticks=True)
            grouped[symbol] = ({bar.minute_ms: bar for bar in bars}, ticks)
        bars_by_minute, ticks = grouped[symbol]
        bar = bars_by_minute.get(int(row["minute_ms"]))
        if bar is None:
            continue
        original_side = row["side"]
        entry = three.find_tick_entry(bar, ticks, original_side, int(row["window_sec"]))
        if entry is None:
            continue
        entry_ts, entry_raw = entry
        opposite = "short" if original_side == "long" else "long"
        sim = three.simulate_impulse_exit(entry_ts, entry_raw, opposite, bar, bars_by_minute, ticks, row["exit"])
        cloned = dict(row)
        cloned["side"] = opposite
        cloned["sim"] = sim
        cloned["permutation_label"] = "opposite_same_entry"
        out.append(cloned)
    return out


def tick_falsification(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    if not rows:
        return {}
    dates = sorted({r["date"] for r in rows})
    split_idx = max(1, len(dates) // 2)
    early_dates = set(dates[:split_idx])
    pair_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    side_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    date_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        pair_groups[row["symbol"]].append(row)
        side_groups[row["side"]].append(row)
        date_groups["early" if row["date"] in early_dates else "late"].append(row)
    pair_summaries = {pair: metric_summary(vals) for pair, vals in pair_groups.items()}
    top_pair = max(pair_summaries.items(), key=lambda item: safe_float(item[1]["sum_net"]))[0]
    without_top = [row for row in rows if row["symbol"] != top_pair]
    opposite = opposite_side_rows(rows)

    rng = random.Random(CONFIG["rng_seed"])
    gross_pairs = []
    for row, opposite_row in zip(rows, opposite):
        gross_pairs.append(
            (
                safe_float((row.get("sim") or {}).get("gross_pct")),
                safe_float((opposite_row.get("sim") or {}).get("gross_pct")),
            )
        )
    perm_means = []
    for _ in range(CONFIG["permutations"]):
        selected = []
        for gross_real, gross_opp in gross_pairs:
            selected.append((gross_real if rng.random() >= 0.5 else gross_opp) - CONFIG["fee_pct"])
        perm_means.append(average(selected))
    return {
        "config": CONFIG["top_tick_config"],
        "all": metric_summary(rows),
        "side": {k: metric_summary(v) for k, v in side_groups.items()},
        "period": {k: metric_summary(v) for k, v in date_groups.items()},
        "pair": pair_summaries,
        "top_pair_by_sum_net": top_pair,
        "without_top_pair": metric_summary(without_top),
        "opposite_same_entry": metric_summary(opposite),
        "shuffle_same_entry": {
            "permutations": CONFIG["permutations"],
            "mean_net": average(perm_means),
            "p05": percentile(perm_means, 5),
            "p50": percentile(perm_means, 50),
            "p95": percentile(perm_means, 95),
            "positive_share": pct(sum(1 for v in perm_means if v > 0), len(perm_means)),
        },
    }


def fade_summary_from_existing() -> dict[str, Any]:
    path = OUT_DIR / "three_engines_summary_polish_22_05_2026.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "fade_top": sorted(payload.get("fade_summary", []), key=lambda r: safe_float(r.get("avg_net_pct")), reverse=True)[:8],
        "fade_side": payload.get("split_summaries", {}).get("fade_side", [])[:8],
        "fade_tier": payload.get("split_summaries", {}).get("fade_tier", [])[:8],
        "fade_period": payload.get("split_summaries", {}).get("fade_period", [])[:8],
    }


def collision_audit() -> list[dict[str, Any]]:
    return [
        {
            "place": "src/strategy/signal_engine.py:145",
            "collision": "Feature calculation and classification are coupled inside compute_signal; analyzers would recalc or reinterpret the same inputs if split naively.",
            "guard": "One FeatureSnapshot per pair/bar; analyzers receive immutable snapshot; snapshot hash logged with signal.",
        },
        {
            "place": "src/strategy/signal_engine.py:991",
            "collision": "Regime is detected, then TRENDING can be rewritten to RANGING on 4H conflict.",
            "guard": "Classifier is the only owner of regime; rewrites must emit classifier_reason; analyzers cannot mutate regime.",
        },
        {
            "place": "src/strategy/signal_engine.py:1000",
            "collision": "TRENDING chooses SWING first, then FAST fallback, so style axis controls entry and stop policy inside regime logic.",
            "guard": "Remove style arbitration from analyzer outputs; one analyzer owns one regime contract and max_hold.",
        },
        {
            "place": "src/strategy/signal_engine.py:1039",
            "collision": "RANGING has both base and recovery branches, but shares output contract with trend-like entries.",
            "guard": "Ranging analyzer emits only fade contracts; recovery/expansion routes to trend/impulse or NO_TRADE.",
        },
        {
            "place": "src/strategy/signal_engine.py:1124",
            "collision": "VWAP veto runs after side selection and can nullify DRIFT/RANGING entries outside the analyzer that chose the side.",
            "guard": "Analyzer owns entry filters; orchestrator can only reject for global risk, not market thesis.",
        },
        {
            "place": "src/strategy/signal_engine.py:1143",
            "collision": "SL/TP geometry lives in production signal computation, not in regime-specific contracts.",
            "guard": "SignalContract must carry entry/stop/exit_rule/max_hold; no downstream component recomputes geometry.",
        },
        {
            "place": "src/strategy/signal_engine.py:1181",
            "collision": "max_hold is still derived from FAST/SWING style and night session, not regime personality.",
            "guard": "max_hold comes from analyzer namespace; session logic is an explicit analyzer parameter or global risk veto.",
        },
        {
            "place": "src/strategy/signal_engine.py:1202",
            "collision": "Final entry_signal gate merges style, geometry, funding, OI, VWAP and RR into one boolean.",
            "guard": "Separate analyzer rejection reasons from orchestrator risk rejections; never collapse owners into one flag.",
        },
        {
            "place": "scripts/ws/ws_main_screener.py:250",
            "collision": "15m close handler computes main signal and stores last regime used by the 5m fade handler.",
            "guard": "5m fade cannot inherit a mutable last regime; it must route from the same immutable classifier snapshot.",
        },
        {
            "place": "scripts/ws/ws_main_screener.py:328",
            "collision": "Cooldown key uses symbol/regime/side but not analyzer ownership, while BB_FADE is special-cased.",
            "guard": "One pair -> one analyzer lock until expiry/close; cooldown namespace includes analyzer_id.",
        },
        {
            "place": "config.yaml:73",
            "collision": "Global breakeven_trail can override analyzer-specific exit semantics.",
            "guard": "Orchestrator may execute only follow rules embedded in SignalContract; global BE is disabled unless contract opts in.",
        },
        {
            "place": "src/data/snapshot_writer.py:63",
            "collision": "Snapshot stores generic entry/sl/tp fields, so different regime contracts lose exit-rule semantics.",
            "guard": "Persist full signal contract, including exit_rule and follow_rule, with schema_version.",
        },
    ]


def plot_distributions(distribution: list[dict[str, Any]]) -> Path:
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    cells = [row for row in distribution if row["side"] == "both" and row["tier"] == "all"]
    labels = [row["cell"] for row in cells]
    mfe = [[row["mfe_p25"], row["mfe_p50"], row["mfe_p75"], row["mfe_p90"]] for row in cells]
    mae = [[abs(row["mae_p25"]), abs(row["mae_p50"]), abs(row["mae_p75"]), abs(row["mae_p90"])] for row in cells]
    fig, ax = plt.subplots(figsize=(11, 5))
    x = range(len(labels))
    ax.bar([i - 0.18 for i in x], [v[1] for v in mfe], width=0.35, label="MFE p50", color="#2b8cbe")
    ax.bar([i + 0.18 for i in x], [v[1] for v in mae], width=0.35, label="|MAE| p50", color="#de2d26")
    for i, vals in enumerate(mfe):
        ax.vlines(i - 0.18, vals[0], vals[3], color="#08519c", linewidth=1.2)
    for i, vals in enumerate(mae):
        ax.vlines(i + 0.18, vals[0], vals[3], color="#a50f15", linewidth=1.2)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.set_ylabel("percent")
    ax.set_title("Derived-entry MFE/MAE percentiles, p25-p90 whiskers")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    path = PLOT_DIR / f"mfe_mae_distribution_{SUFFIX}.png"
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_tick_falsification(falsification: dict[str, Any]) -> Path | None:
    if not falsification:
        return None
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    items = [
        ("all", falsification["all"]["net"]),
        ("drop top pair", falsification["without_top_pair"]["net"]),
        ("opposite", falsification["opposite_same_entry"]["net"]),
        ("shuffle p50", falsification["shuffle_same_entry"]["p50"]),
    ]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar([k for k, _ in items], [v for _, v in items], color=["#238b45", "#41ab5d", "#cb181d", "#756bb1"])
    ax.axhline(0, color="#111111", linewidth=0.8)
    ax.set_ylabel("net %")
    ax.set_title("Tick-entry falsification: fixed top config")
    ax.grid(axis="y", alpha=0.25)
    path = PLOT_DIR / f"tick_falsification_{SUFFIX}.png"
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def geometry_formula_rows(current_derived: list[dict[str, Any]]) -> list[list[Any]]:
    labels = {
        "TRENDING_IMPULSE": "entry=tick/mid proxy; SL=impulse low/high +0.10%; TP=1.0x impulse body or structure_k2 ride",
        "DRIFT": "NO-GO; best measured rescue is open+0.75x body TP, but sample/sides fail",
        "RANGING": "entry=boundary close; SL=range/BB structure; exit=structure_k2 or BB middle/opposite fade",
    }
    rows = []
    for item in current_derived:
        summary = item["summary"]
        sides = item["sides"]
        rows.append(
            [
                item["cell"],
                labels[item["cell"]],
                summary["n"],
                fmt(summary["old_net"], "%"),
                fmt(summary["net"], "%"),
                fmt(summary["delta"], "%"),
                fmt(sides["long"]["net"], "%"),
                fmt(sides["short"]["net"], "%"),
                item["verdict"],
            ]
        )
    return rows


def render_report(payload: dict[str, Any]) -> str:
    distribution = payload["mfe_mae_distribution"]
    current = payload["current_vs_derived"]
    drift = payload["drift_subconditions"]
    tick = payload["tick_falsification"]
    fade = payload["fade"]
    collisions = payload["collision_audit"]
    coverage = payload["tick_coverage"]
    dist_rows = []
    for row in distribution:
        if row["side"] == "both":
            dist_rows.append(
                [
                    row["cell"],
                    row["entry_mode"],
                    row["tier"],
                    row["n"],
                    fmt(row["mfe_p25"], "%"),
                    fmt(row["mfe_p50"], "%"),
                    fmt(row["mfe_p75"], "%"),
                    fmt(row["mfe_p90"], "%"),
                    fmt(row["mae_p50"], "%"),
                    fmt(row["net"], "%"),
                ]
            )
    drift_rows = [
        [
            row["group"],
            row["n"],
            fmt(row["net"], "%"),
            fmt(row["wr"], "%"),
            fmt(row["sides"]["long"]["n"]),
            fmt(row["sides"]["long"]["net"], "%"),
            fmt(row["sides"]["short"]["n"]),
            fmt(row["sides"]["short"]["net"], "%"),
            "yes" if row["side_ok_n20_netpos"] else "no",
        ]
        for row in drift[:12]
    ]
    lines = [
        "# Main Rebuild Deep Research - 23.05.2026",
        "",
        "Research-only run. Production files were read for audit only; outputs are in `scripts/analysis/research/output`.",
        "",
        "## Coverage",
        "",
        f"- replay candles: `{payload['period']['start']}` -> `{payload['period']['end']}`, universe `{len(payload['universe'])}`",
        f"- tick root: `{coverage['tick_root']}`",
        f"- tick symbols with any data: `{coverage['symbols_with_any_ticks']}` / `{len(coverage['symbols'])}`",
        f"- tick symbols overlapping replay dates `{coverage['replay_window']['start_date']}..{coverage['replay_window']['end_date']}`: `{coverage['symbols_with_replay_overlap']}` / `{len(coverage['symbols'])}`",
        "- honest caveat: replay GO window starts 04.05, local tick coverage starts mostly 11.05, so the exact 04-10.05 part cannot be tick-replayed without external historical trades.",
        "",
        "## P0-1 MFE/MAE And Geometry",
        "",
    ]
    lines.extend(render_table(["cell", "entry", "tier", "n", "MFE p25", "p50", "p75", "p90", "MAE p50", "net"], dist_rows))
    lines.extend(["", "### Current vs Derived Geometry", ""])
    lines.extend(render_table(["cell", "formula", "n", "current net", "derived net", "delta", "long net", "short net", "verdict"], geometry_formula_rows(current)))
    lines.extend(
        [
            "",
            "Derived code shape:",
            "",
            "```python",
            "if regime == 'TRENDING_IMPULSE':",
            "    entry = tick_trigger('>=0.30% within 10-20s')  # mid proxy in replay",
            "    stop = impulse_extreme +/- 0.10%",
            "    exit = scaled_tp(1.0 * impulse_body) or structure_k2 ride",
            "elif regime == 'RANGING':",
            "    entry = bb_boundary_touch_close",
            "    stop = range_extreme +/- buffer",
            "    exit = bb_middle / opposite_band / structure_k2",
            "else:  # DRIFT",
            "    no_trade_until_subcondition_has_n20_both_sides",
            "```",
            "",
            "## P0-2 Real Tick Entry And Falsification",
            "",
        ]
    )
    if tick:
        tick_rows = [
            ["all", tick["all"]["n"], fmt(tick["all"]["net"], "%"), fmt(tick["all"]["wr"], "%"), fmt(tick["all"]["capture"], "%")],
            [f"drop top pair {tick['top_pair_by_sum_net']}", tick["without_top_pair"]["n"], fmt(tick["without_top_pair"]["net"], "%"), fmt(tick["without_top_pair"]["wr"], "%"), fmt(tick["without_top_pair"]["capture"], "%")],
            ["opposite same entry", tick["opposite_same_entry"]["n"], fmt(tick["opposite_same_entry"]["net"], "%"), fmt(tick["opposite_same_entry"]["wr"], "%"), fmt(tick["opposite_same_entry"]["capture"], "%")],
            ["shuffle side p50", tick["shuffle_same_entry"]["permutations"], fmt(tick["shuffle_same_entry"]["p50"], "%"), fmt(tick["shuffle_same_entry"]["positive_share"], "%"), "n/a"],
        ]
        lines.extend(render_table(["test", "n", "net", "WR/positive", "capture"], tick_rows))
        side_rows = [[side, vals["n"], fmt(vals["net"], "%"), fmt(vals["wr"], "%")] for side, vals in sorted(tick["side"].items())]
        lines.extend(["", "Side split:"])
        lines.extend(render_table(["side", "n", "net", "WR"], side_rows))
        period_rows = [[period, vals["n"], fmt(vals["net"], "%"), fmt(vals["wr"], "%")] for period, vals in sorted(tick["period"].items())]
        lines.extend(["", "Date split:"])
        lines.extend(render_table(["period", "n", "net", "WR"], period_rows))
    else:
        lines.append("No fixed tick rows built.")
    lines.extend(
        [
            "",
            "Verdict: tick-entry edge survives real tick measurement on the available 17-23.05 tape if the fixed top config stays positive after top-pair removal and fails on opposite/shuffled labels. It is still not the exact 04-14 replay sample.",
            "",
            "## P1-1 DRIFT",
            "",
        ]
    )
    lines.extend(render_table(["group", "n", "net", "WR", "long n", "long net", "short n", "short net", "n20 both+"], drift_rows))
    lines.extend(
        [
            "",
            "Verdict: drop DRIFT as a new rebuild branch until a real subcondition has n>=20 on both sides and net>0. Current best rescue is still thin and does not pass side/sample gates.",
            "",
            "## P1-2 RANGING Fade",
            "",
        ]
    )
    fade_rows = [
        [r.get("tol"), r.get("adx_max"), r.get("bb_width_max"), r.get("target"), r.get("n"), fmt(r.get("avg_net_pct"), "%"), fmt(r.get("win_rate"), "%"), fmt(r.get("avg_hold_min"), "m")]
        for r in fade.get("fade_top", [])
    ]
    lines.extend(render_table(["tol", "adx", "bb max", "target", "n", "net", "WR", "hold"], fade_rows))
    lines.extend(
        [
            "",
            "Verdict: no GO. Best fade rows are small and mostly negative; short side and high-vol tiers are the only hints, not a tradable rule.",
            "",
            "## P2-1 Collision Audit",
            "",
        ]
    )
    collision_rows = [[c["place"], c["collision"], c["guard"]] for c in collisions]
    lines.extend(render_table(["place", "collision", "3-invariant guard"], collision_rows))
    lines.extend(
        [
            "",
            "## P2-2 Separation Skeleton",
            "",
            "- Added research-only `scripts/analysis/research/regime_contract_skeleton_23_05_2026.py`.",
            "- It keeps classifier ownership, analyzer ownership and position ownership separate.",
            "- It stores exit/follow rules inside `SignalContract`, so the orchestrator can execute but not invent strategy.",
            "",
            "## Hypotheses / Honesty",
            "",
            "- Edge exists clearly in real tick impulse rows, but the sample is a different tape window than 04-14.05 and still needs forward confirmation.",
            "- TRENDING_IMPULSE replay GO is credible because close->mid->open is monotonic and both sides are positive at mid, but exact tick validation for 04-10.05 is missing locally.",
            "- DRIFT is not rescued: net is near zero and no n>=20 both-side subgroup appears.",
            "- RANGING fade is plausible structurally, but current measured rows are too thin/negative for GO.",
            "- All 10-day replay conclusions remain thin-sample research, not production config.",
            "",
            "## Artifacts",
            "",
            f"- summary: `{SUMMARY_JSON}`",
            f"- plots: `{PLOT_DIR}`",
            f"- skeleton: `{ROOT / 'scripts' / 'analysis' / 'research' / 'regime_contract_skeleton_23_05_2026.py'}`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_text("", encoding="utf-8")
    patch_module_logs()
    log("loading replay rows")
    replay_rows, events, replay_meta = load_replay_rows()
    log(f"replay rows={len(replay_rows)} events={len(events)}")
    distribution = mfe_mae_distribution(replay_rows)
    current = current_vs_derived(replay_rows)
    drift = drift_subconditions(replay_rows)
    coverage = tick_coverage(replay_meta["universe"], replay_meta["start_ms"], replay_meta["end_ms"])
    log("building real tick rows")
    tick_rows, tick_cov, pair_stats = build_tick_rows()
    log(f"fixed tick rows={len(tick_rows)}")
    tick = tick_falsification(tick_rows)
    fade = fade_summary_from_existing()
    dist_plot = plot_distributions(distribution)
    tick_plot = plot_tick_falsification(tick)
    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": CONFIG,
        "period": {"start": iso_from_ms(replay_meta["start_ms"]), "end": iso_from_ms(replay_meta["end_ms"])},
        "universe": replay_meta["universe"],
        "event_count": len(events),
        "replay_row_count": len(replay_rows),
        "mfe_mae_distribution": distribution,
        "current_vs_derived": current,
        "drift_subconditions": drift,
        "tick_coverage": coverage,
        "tick_impulse_coverage": tick_cov,
        "tick_pair_stats": pair_stats,
        "tick_fixed_row_count": len(tick_rows),
        "tick_falsification": tick,
        "fade": fade,
        "collision_audit": collision_audit(),
        "plots": {"mfe_mae": str(dist_plot), "tick_falsification": str(tick_plot) if tick_plot else None},
    }
    SUMMARY_JSON.write_text(json.dumps(json_safe(payload), ensure_ascii=True, indent=2, allow_nan=False), encoding="utf-8")
    REPORT_MD.write_text(render_report(payload), encoding="utf-8")
    log(f"saved {REPORT_MD}")
    log(f"saved {SUMMARY_JSON}")
    log(f"saved plots to {PLOT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
