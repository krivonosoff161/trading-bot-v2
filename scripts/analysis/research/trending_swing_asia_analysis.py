from __future__ import annotations

import itertools
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from ws_truth_analysis import (
    LABELS_PATH,
    ROOT,
    SIGNALS_PATH,
    SNAPSHOT_PATH,
    calc_exit_r,
    normalize_outcome,
    parse_ts,
    read_jsonl,
    safe_float,
)


OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
REPORT_PATH = ROOT / "docs" / "trending_swing_asia_report_19_05_2026.md"
DATA_PATH = OUT_DIR / "trending_swing_asia_19_05_2026.json"


WIN = "TP"
LOSS = "SL"


def fmt(value: float, digits: int = 2, suffix: str = "") -> str:
    if not math.isfinite(value):
        return "n/a"
    return f"{value:.{digits}f}{suffix}"


def fmt_pct(value: float) -> str:
    return "n/a" if not math.isfinite(value) else f"{value:.1f}%"


def session(hour: int | None) -> str:
    if hour is None:
        return "UNKNOWN"
    if 0 <= hour <= 5:
        return "Asia"
    if 6 <= hour <= 12:
        return "EU"
    if 13 <= hour <= 20:
        return "US"
    return "Late"


def session_eu_us(hour: int | None) -> str:
    return "EU_US" if hour is not None and 6 <= hour <= 20 else ("Asia" if hour is not None and 0 <= hour <= 5 else "Late")


def get_nested(row: dict[str, Any], *path: str) -> Any:
    cur: Any = row
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def outcome_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decisive = [r for r in rows if r["decisive"]]
    wins = [r for r in decisive if r["outcome"] == WIN]
    losses = [r for r in decisive if r["outcome"] == LOSS]
    vals = [r["exit_r"] for r in decisive if math.isfinite(r["exit_r"])]
    return {
        "n_total": len(rows),
        "n": len(decisive),
        "tp": len(wins),
        "sl": len(losses),
        "time": sum(1 for r in rows if r["outcome"] == "TIME"),
        "wr": len(wins) / len(decisive) * 100 if decisive else float("nan"),
        "avg_r": sum(vals) / len(vals) if vals else float("nan"),
        "avg_tp_r": (
            sum(r["exit_r"] for r in wins if math.isfinite(r["exit_r"]))
            / max(1, sum(1 for r in wins if math.isfinite(r["exit_r"])))
            if wins
            else float("nan")
        ),
        "avg_sl_r": (
            sum(r["exit_r"] for r in losses if math.isfinite(r["exit_r"]))
            / max(1, sum(1 for r in losses if math.isfinite(r["exit_r"])))
            if losses
            else float("nan")
        ),
    }


def split_stats(
    rows: list[dict[str, Any]],
    name: str,
    pred: Callable[[dict[str, Any]], bool | None],
    with_label: str,
    without_label: str,
) -> dict[str, Any]:
    with_rows: list[dict[str, Any]] = []
    without_rows: list[dict[str, Any]] = []
    missing = 0
    for row in rows:
        value = pred(row)
        if value is None:
            missing += 1
        elif value:
            with_rows.append(row)
        else:
            without_rows.append(row)
    ws = outcome_stats(with_rows)
    ns = outcome_stats(without_rows)
    return {
        "feature": name,
        "with_label": with_label,
        "n_with": ws["n"],
        "wr_with": ws["wr"],
        "avg_r_with": ws["avg_r"],
        "without_label": without_label,
        "n_without": ns["n"],
        "wr_without": ns["wr"],
        "avg_r_without": ns["avg_r"],
        "gap": ws["wr"] - ns["wr"] if math.isfinite(ws["wr"]) and math.isfinite(ns["wr"]) else float("nan"),
        "missing": missing,
    }


def recommendation(row: dict[str, Any]) -> str:
    n_with = row["n_with"]
    n_without = row["n_without"]
    gap = row["gap"]
    if n_with < 5 or n_without < 5:
        return "need more data"
    if abs(gap) >= 25:
        return "filter candidate"
    if abs(gap) >= 15:
        return "watch"
    return "do not filter yet"


def load_dataset() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    signals = {row["id"]: row for row in read_jsonl(SIGNALS_PATH) if row.get("id")}
    labels = {row["signal_id"]: row for row in read_jsonl(LABELS_PATH) if row.get("signal_id")}
    snapshots = {
        row["signal_id"]: row
        for row in read_jsonl(SNAPSHOT_PATH)
        if row.get("signal_id") and row.get("source") == "ws_main_screener"
    }

    rows: list[dict[str, Any]] = []
    snapshot_rows: list[dict[str, Any]] = []
    for sid, label in labels.items():
        signal = signals.get(sid)
        if not signal:
            continue
        ts = parse_ts(signal.get("ts") or label.get("ts"))
        outcome_raw = str(label.get("outcome") or "").upper()
        outcome = normalize_outcome(outcome_raw)
        side = str(signal.get("side") or "")
        entry = safe_float(signal.get("entry"))
        sl = safe_float(signal.get("sl"))
        exit_price = safe_float(label.get("exit_price"))
        base = {
            "signal_id": sid,
            "ts": signal.get("ts") or label.get("ts"),
            "hour_utc": ts.hour if ts else None,
            "session": session(ts.hour if ts else None),
            "session_eu_us": session_eu_us(ts.hour if ts else None),
            "symbol": signal.get("symbol") or label.get("symbol"),
            "regime": signal.get("regime") or label.get("regime"),
            "style": signal.get("trade_style"),
            "side": side,
            "entry": entry,
            "sl": sl,
            "tp1": safe_float(signal.get("tp1")),
            "tp2": safe_float(signal.get("tp2")),
            "exit_price": exit_price,
            "outcome_raw": outcome_raw,
            "outcome": outcome,
            "decisive": outcome in {WIN, LOSS},
            "exit_r": calc_exit_r(side, entry, sl, exit_price, outcome_raw),
        }
        rows.append(base)

        snap = snapshots.get(sid)
        if not snap:
            continue
        ctx = snap.get("context") or {}
        eng = snap.get("engine_vars") or {}
        ind_15 = get_nested(snap, "indicators", "15m") or {}
        ind_1h = get_nested(snap, "indicators", "1h") or {}
        ind_4h = get_nested(snap, "indicators", "4h") or {}
        enriched = dict(base)
        enriched.update(
            {
                "adx_1h": safe_float(ctx.get("adx_1h", eng.get("adx_1h"))),
                "adx_4h": safe_float(ctx.get("adx_4h", eng.get("adx_4h", ind_4h.get("adx")))),
                "adx_4h_rising": bool(ctx.get("adx_4h_rising", eng.get("adx_4h_rising", False))),
                "bb_expanding": bool(ctx.get("bb_expanding", eng.get("bb_expanding", False))),
                "slope_1h": safe_float(ctx.get("slope_1h")),
                "slope_15m": safe_float(ctx.get("slope_15m")),
                "vol_ratio_sig": safe_float(ctx.get("vol_ratio_sig", eng.get("vol_ratio_sig"))),
                "day_position": safe_float(ctx.get("day_position", eng.get("day_position"))),
                "daily_range_pct": safe_float(ctx.get("daily_range_pct", eng.get("daily_range_pct"))),
                "funding_val": safe_float(ctx.get("funding_val", eng.get("funding_val"))),
                "funding_block": bool(ctx.get("funding_block", eng.get("funding_block", False))),
                "rsi_1h": safe_float(eng.get("rsi_1h")),
                "rsi_15m": safe_float(eng.get("rsi_15m")),
                "rsi_5m": safe_float(eng.get("rsi_5m")),
                "di_spread_1h": safe_float(eng.get("di_spread_1h")),
                "di_spread_4h": safe_float(eng.get("di_spread_4h")),
                "bb_pct_b_15m": safe_float(ind_15.get("bb_pct_b")),
                "bb_width_pct_15m": safe_float(ind_15.get("bb_width_pct")),
                "atr_pct_15m": safe_float(ind_15.get("atr_pct")),
                "bb_width_pct_1h": safe_float(ind_1h.get("bb_width_pct")),
                "bb_width_4h": safe_float(ind_4h.get("bb_width")),
            }
        )
        snapshot_rows.append(enriched)
    return rows, snapshot_rows


def mean(rows: list[dict[str, Any]], key: str) -> float:
    vals = [r[key] for r in rows if math.isfinite(safe_float(r.get(key)))]
    return sum(vals) / len(vals) if vals else float("nan")


def share(rows: list[dict[str, Any]], key: str) -> float:
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    return sum(1 for v in vals if bool(v)) / len(vals) * 100 if vals else float("nan")


def group_by(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    out: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        out[tuple(row.get(k) for k in keys)].append(row)
    return out


def md_stats(s: dict[str, Any]) -> str:
    return f"{s['n']} | {fmt_pct(s['wr'])} | {fmt(s['avg_r'])}"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, snapshot_rows = load_dataset()
    decisive = [r for r in rows if r["decisive"]]
    ts_snap = [
        r
        for r in snapshot_rows
        if r["regime"] == "TRENDING" and r["style"] == "SWING" and r["decisive"]
    ]

    splitters = [
        split_stats(ts_snap, "bb_expanding", lambda r: bool(r["bb_expanding"]), "true", "false"),
        split_stats(
            ts_snap,
            "adx_4h 25-40",
            lambda r: None if not math.isfinite(r["adx_4h"]) else 25 <= r["adx_4h"] < 40,
            "25<=adx<40",
            "other",
        ),
        split_stats(
            ts_snap,
            "adx_4h 40-55",
            lambda r: None if not math.isfinite(r["adx_4h"]) else 40 <= r["adx_4h"] < 55,
            "40<=adx<55",
            "other",
        ),
        split_stats(
            ts_snap,
            "adx_4h 55+",
            lambda r: None if not math.isfinite(r["adx_4h"]) else r["adx_4h"] >= 55,
            "adx>=55",
            "other",
        ),
        split_stats(ts_snap, "adx_4h_rising", lambda r: bool(r["adx_4h_rising"]), "true", "false"),
        split_stats(
            ts_snap,
            "abs(slope_1h)>=30",
            lambda r: None if not math.isfinite(r["slope_1h"]) else abs(r["slope_1h"]) >= 30,
            "abs>=30",
            "abs<30",
        ),
        split_stats(
            ts_snap,
            "rsi_15m<60",
            lambda r: None if not math.isfinite(r["rsi_15m"]) else r["rsi_15m"] < 60,
            "<60",
            ">=60",
        ),
        split_stats(
            ts_snap,
            "bb_pct_b_15m<70",
            lambda r: None if not math.isfinite(r["bb_pct_b_15m"]) else r["bb_pct_b_15m"] < 70,
            "<70",
            ">=70",
        ),
        split_stats(
            ts_snap,
            "day_position<0.7",
            lambda r: None if not math.isfinite(r["day_position"]) else r["day_position"] < 0.7,
            "<0.7",
            ">=0.7",
        ),
        split_stats(
            ts_snap,
            "session EU/US",
            lambda r: True if r["session_eu_us"] == "EU_US" else (False if r["session_eu_us"] == "Asia" else None),
            "EU/US",
            "Asia",
        ),
        split_stats(
            ts_snap,
            "daily_range_pct>5",
            lambda r: None if not math.isfinite(r["daily_range_pct"]) else r["daily_range_pct"] > 5,
            ">5",
            "<=5",
        ),
    ]
    splitters_sorted = sorted(splitters, key=lambda r: abs(r["gap"]) if math.isfinite(r["gap"]) else -1, reverse=True)

    conds: list[tuple[str, Callable[[dict[str, Any]], bool]]] = [
        ("bb_expanding=true", lambda r: bool(r["bb_expanding"])),
        ("adx_4h_rising=true", lambda r: bool(r["adx_4h_rising"])),
        ("abs(slope_1h)>=30", lambda r: math.isfinite(r["slope_1h"]) and abs(r["slope_1h"]) >= 30),
        ("rsi_15m<60", lambda r: math.isfinite(r["rsi_15m"]) and r["rsi_15m"] < 60),
        ("bb_pct_b_15m<70", lambda r: math.isfinite(r["bb_pct_b_15m"]) and r["bb_pct_b_15m"] < 70),
        ("day_position<0.7", lambda r: math.isfinite(r["day_position"]) and r["day_position"] < 0.7),
        ("daily_range_pct>5", lambda r: math.isfinite(r["daily_range_pct"]) and r["daily_range_pct"] > 5),
        ("session=EU_US", lambda r: r["session_eu_us"] == "EU_US"),
        ("adx_4h<55", lambda r: math.isfinite(r["adx_4h"]) and r["adx_4h"] < 55),
        ("adx_4h>=40", lambda r: math.isfinite(r["adx_4h"]) and r["adx_4h"] >= 40),
    ]
    combo_rows = []
    for size in (2, 3):
        for combo in itertools.combinations(conds, size):
            names = [c[0] for c in combo]
            preds = [c[1] for c in combo]
            kept = [r for r in ts_snap if all(pred(r) for pred in preds)]
            s = outcome_stats(kept)
            if s["n"] >= 5:
                combo_rows.append({"combo": " AND ".join(names), **s})
    combo_rows.sort(key=lambda r: (r["wr"], r["avg_r"], r["n"]), reverse=True)
    combo_rows = combo_rows[:5]

    session_matrix = []
    for key, items in sorted(group_by(decisive, ("session", "regime", "style")).items()):
        s = outcome_stats(items)
        session_matrix.append({"session": key[0], "regime": key[1], "style": key[2], **s})

    asia_snap = [r for r in snapshot_rows if r["session"] == "Asia"]
    eu_us_snap = [r for r in snapshot_rows if r["session_eu_us"] == "EU_US"]
    indicator_keys = ["adx_4h", "vol_ratio_sig", "daily_range_pct", "day_position", "rsi_15m", "bb_pct_b_15m", "slope_1h"]
    asia_indicators = [
        {
            "feature": key,
            "asia_n": sum(1 for r in asia_snap if math.isfinite(safe_float(r.get(key)))),
            "asia_avg": mean(asia_snap, key),
            "eu_us_n": sum(1 for r in eu_us_snap if math.isfinite(safe_float(r.get(key)))),
            "eu_us_avg": mean(eu_us_snap, key),
            "gap": mean(asia_snap, key) - mean(eu_us_snap, key),
        }
        for key in indicator_keys
    ]
    asia_indicators.append(
        {
            "feature": "bb_expanding_share",
            "asia_n": len(asia_snap),
            "asia_avg": share(asia_snap, "bb_expanding"),
            "eu_us_n": len(eu_us_snap),
            "eu_us_avg": share(eu_us_snap, "bb_expanding"),
            "gap": share(asia_snap, "bb_expanding") - share(eu_us_snap, "bb_expanding"),
        }
    )

    ts_all_decisive = [r for r in decisive if r["regime"] == "TRENDING" and r["style"] == "SWING"]
    ts_asia = [r for r in ts_all_decisive if r["session"] == "Asia"]
    ts_eu_us = [r for r in ts_all_decisive if r["session_eu_us"] == "EU_US"]

    def eval_skip(name: str, pred: Callable[[dict[str, Any]], bool], scope_rows: list[dict[str, Any]]) -> dict[str, Any]:
        removed = [r for r in scope_rows if pred(r)]
        kept = [r for r in scope_rows if not pred(r)]
        return {
            "name": name,
            "removed_n": len(removed),
            "removed_decisive_n": outcome_stats(removed)["n"],
            "removed_wr": outcome_stats(removed)["wr"],
            "kept_n": outcome_stats(kept)["n"],
            "kept_wr": outcome_stats(kept)["wr"],
            "kept_avg_r": outcome_stats(kept)["avg_r"],
        }

    filters = [
        eval_skip(
            "IF regime=TRENDING AND style=SWING AND adx_4h_rising=false -> skip",
            lambda r: r["signal_id"] in {x["signal_id"] for x in ts_snap if not x["adx_4h_rising"]},
            ts_all_decisive,
        ),
        eval_skip(
            "IF regime=TRENDING AND style=SWING AND 40<=adx_4h<55 -> skip",
            lambda r: r["signal_id"] in {x["signal_id"] for x in ts_snap if math.isfinite(x["adx_4h"]) and 40 <= x["adx_4h"] < 55},
            ts_all_decisive,
        ),
        eval_skip(
            "IF regime=TRENDING AND style=SWING AND bb_expanding=false -> skip",
            lambda r: r["signal_id"] in {x["signal_id"] for x in ts_snap if not x["bb_expanding"]},
            ts_all_decisive,
        ),
        eval_skip(
            "IF session=Asia AND regime=TRENDING -> skip",
            lambda r: r["session"] == "Asia" and r["regime"] == "TRENDING",
            decisive,
        ),
        eval_skip(
            "IF session=Asia AND regime=TRENDING AND style=SWING -> skip",
            lambda r: r["session"] == "Asia" and r["regime"] == "TRENDING" and r["style"] == "SWING",
            decisive,
        ),
        eval_skip(
            "IF session=Asia AND daily_range_pct<=5 -> skip",
            lambda r: r["signal_id"] in {x["signal_id"] for x in snapshot_rows if x["session"] == "Asia" and math.isfinite(x["daily_range_pct"]) and x["daily_range_pct"] <= 5},
            decisive,
        ),
    ]

    payload = {
        "source_files": [str(SIGNALS_PATH.relative_to(ROOT)), str(LABELS_PATH.relative_to(ROOT)), str(SNAPSHOT_PATH.relative_to(ROOT))],
        "counts": {
            "labels_joined_to_ws_signals": len(rows),
            "decisive": len(decisive),
            "snapshot_joined": len(snapshot_rows),
            "trending_swing_snapshot_decisive": len(ts_snap),
            "trending_swing_all_decisive": len(ts_all_decisive),
        },
        "trending_swing_stats": outcome_stats(ts_all_decisive),
        "trending_swing_snapshot_stats": outcome_stats(ts_snap),
        "splitters": splitters,
        "top_combos": combo_rows,
        "session_matrix": session_matrix,
        "asia_indicators": asia_indicators,
        "interaction": {
            "trending_swing_asia": outcome_stats(ts_asia),
            "trending_swing_eu_us": outcome_stats(ts_eu_us),
        },
        "filters": filters,
    }
    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    lines = [
        "# TRENDING x SWING + Asia WS-Only Analysis",
        "",
        "Scope: WS-only signals. Outcomes come from `logs/signals/main_signals_labels.jsonl`; trade metadata/R comes from `logs/signals/main_signals.jsonl`; full context comes from `logs/signals/signal_snapshot.jsonl` when present. Archive REST data is not used.",
        "",
        "Session buckets are non-overlapping: Asia `00-05`, EU `06-12`, US `13-20`, Late `21-23` UTC.",
        "",
        "## Coverage",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| labels joined to WS signals | {len(rows)} |",
        f"| decisive TP/SL | {len(decisive)} |",
        f"| joined ws_main snapshots | {len(snapshot_rows)} |",
        f"| TRENDING x SWING decisive, all WS metadata | {len(ts_all_decisive)} |",
        f"| TRENDING x SWING decisive with snapshot context | {len(ts_snap)} |",
        "",
        "R note: SL is `-1R`; TP uses price-based R where valid, with fallback `TP1=+0.5R`, `TP2=+1.0R`.",
        "",
        "## TRENDING x SWING R Decomposition",
        "",
        "| scope | n | WR | avg_R | avg_TP_R | avg_SL_R |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    ts_stats = outcome_stats(ts_all_decisive)
    snap_stats = outcome_stats(ts_snap)
    lines.append(f"| all decisive metadata | {ts_stats['n']} | {fmt_pct(ts_stats['wr'])} | {fmt(ts_stats['avg_r'])} | {fmt(ts_stats['avg_tp_r'])} | {fmt(ts_stats['avg_sl_r'])} |")
    lines.append(f"| snapshot context subset | {snap_stats['n']} | {fmt_pct(snap_stats['wr'])} | {fmt(snap_stats['avg_r'])} | {fmt(snap_stats['avg_tp_r'])} | {fmt(snap_stats['avg_sl_r'])} |")

    lines.extend(
        [
            "",
            "## TRENDING x SWING Splitters",
            "",
            "| feature | with | n | WR | avg_R | without | n | WR | avg_R | WR gap | missing | recommendation |",
            "| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in splitters_sorted:
        lines.append(
            f"| {row['feature']} | {row['with_label']} | {row['n_with']} | {fmt_pct(row['wr_with'])} | {fmt(row['avg_r_with'])} | "
            f"{row['without_label']} | {row['n_without']} | {fmt_pct(row['wr_without'])} | {fmt(row['avg_r_without'])} | "
            f"{fmt(row['gap'], 1, '%')} | {row['missing']} | {recommendation(row)} |"
        )

    lines.extend(["", "## Top Combined Keep Conditions", "", "| condition | n | WR | avg_R | avg_TP_R |", "| --- | ---: | ---: | ---: | ---: |"])
    for row in combo_rows:
        lines.append(f"| {row['combo']} | {row['n']} | {fmt_pct(row['wr'])} | {fmt(row['avg_r'])} | {fmt(row['avg_tp_r'])} |")

    lines.extend(["", "## Session x Regime x Style", "", "| session | regime | style | n | WR | avg_R | TP | SL |", "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"])
    for row in session_matrix:
        lines.append(f"| {row['session']} | {row['regime']} | {row['style']} | {row['n']} | {fmt_pct(row['wr'])} | {fmt(row['avg_r'])} | {row['tp']} | {row['sl']} |")

    lines.extend(["", "## Asia vs EU/US Snapshot Context", "", "| feature | Asia n | Asia avg | EU/US n | EU/US avg | Asia-EU/US |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for row in asia_indicators:
        suffix = "%" if row["feature"] == "bb_expanding_share" else ""
        lines.append(
            f"| {row['feature']} | {row['asia_n']} | {fmt(row['asia_avg'], 2, suffix)} | "
            f"{row['eu_us_n']} | {fmt(row['eu_us_avg'], 2, suffix)} | {fmt(row['gap'], 2, suffix)} |"
        )

    inter_asia = outcome_stats(ts_asia)
    inter_eu_us = outcome_stats(ts_eu_us)
    lines.extend(
        [
            "",
            "## Interaction: TRENDING x SWING In Asia",
            "",
            "| bucket | n | WR | avg_R | TP | SL |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
            f"| TRENDING x SWING Asia | {inter_asia['n']} | {fmt_pct(inter_asia['wr'])} | {fmt(inter_asia['avg_r'])} | {inter_asia['tp']} | {inter_asia['sl']} |",
            f"| TRENDING x SWING EU/US | {inter_eu_us['n']} | {fmt_pct(inter_eu_us['wr'])} | {fmt(inter_eu_us['avg_r'])} | {inter_eu_us['tp']} | {inter_eu_us['sl']} |",
            "",
            "## Candidate Skip Filters",
            "",
            "| filter | removed decisive | removed WR | kept n | kept WR | kept avg_R |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in filters:
        lines.append(
            f"| {row['name']} | {row['removed_decisive_n']} | {fmt_pct(row['removed_wr'])} | "
            f"{row['kept_n']} | {fmt_pct(row['kept_wr'])} | {fmt(row['kept_avg_r'])} |"
        )

    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- Context splitters use only the 59 joined `ws_main_screener` snapshots; missing context is not assumed random.",
            "- Buckets with `n < 10` are preliminary; use them as guardrail hypotheses, not as proven production filters.",
            "- `main_signals_labels.jsonl` currently has no `mfe_r`, `mae_r`, or `elapsed_m`; R is reconstructed from WS signal metadata and label exit price.",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"saved {REPORT_PATH}")
    print(f"saved {DATA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
