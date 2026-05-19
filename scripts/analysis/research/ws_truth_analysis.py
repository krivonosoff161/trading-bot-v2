from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
SIGNALS_PATH = ROOT / "logs" / "signals" / "main_signals.jsonl"
LABELS_PATH = ROOT / "logs" / "signals" / "main_signals_labels.jsonl"
SNAPSHOT_PATH = ROOT / "logs" / "signals" / "signal_snapshot.jsonl"
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
TRUTH_REPORT_PATH = ROOT / "docs" / "ws_truth_report.md"
PATTERN_REPORT_PATH = ROOT / "docs" / "ws_pattern_mining_report.md"
DATA_PATH = OUT_DIR / "ws_truth_dataset.json"

WIN_OUTCOMES = {"TP", "TP1", "TP2"}
LOSS_OUTCOMES = {"SL", "STOP"}
TIME_OUTCOMES = {"TIME", "TIME_EXIT", "EXPIRE"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if math.isfinite(out) else float("nan")


def normalize_outcome(value: Any) -> str:
    raw = str(value or "").upper()
    if raw in WIN_OUTCOMES:
        return "TP"
    if raw in LOSS_OUTCOMES:
        return "SL"
    if raw in TIME_OUTCOMES:
        return "TIME"
    return raw


def fallback_tp_r(outcome: str) -> float:
    if outcome == "TP2":
        return 1.0
    return 0.5


def calc_exit_r(side: str, entry: float, sl: float, exit_price: float, outcome_raw: str) -> float:
    raw = str(outcome_raw or "").upper()
    outcome = normalize_outcome(raw)
    if outcome == "SL":
        return -1.0
    risk = abs(entry - sl)
    if risk <= 0 or not all(math.isfinite(v) for v in (risk, entry, exit_price)):
        return fallback_tp_r(raw) if outcome == "TP" else float("nan")
    if side == "buy":
        value = (exit_price - entry) / risk
    else:
        value = (entry - exit_price) / risk
    if outcome == "TP" and value <= 0:
        return fallback_tp_r(raw)
    return value


def fmt(value: float, suffix: str = "") -> str:
    if not math.isfinite(value):
        return "n/a"
    return f"{value:.2f}{suffix}"


def fmt_wr(value: float) -> str:
    return "n/a" if not math.isfinite(value) else f"{value:.1f}%"


def sample_note(n: int) -> str:
    if n < 5:
        return "N/A - no conclusion"
    if n < 10:
        return "preliminary, not actionable"
    return "usable"


def stats(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    decisive = [row for row in rows if row["decisive"]]
    wins = sum(1 for row in decisive if row["win"])
    vals = [row["exit_r"] for row in decisive if math.isfinite(row["exit_r"])]
    gross_profit = sum(v for v in vals if v > 0)
    gross_loss = abs(sum(v for v in vals if v < 0))
    return {
        "n": len(decisive),
        "wins": wins,
        "wr": wins / len(decisive) * 100 if decisive else float("nan"),
        "avg_r": sum(vals) / len(vals) if vals else float("nan"),
        "pf": gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else float("nan")),
    }


def bucket_adx_4h(value: float) -> str:
    if not math.isfinite(value):
        return "na"
    if value < 25:
        return "<25"
    if value < 40:
        return "25-40"
    return "40+"


def bucket_vol_ratio(value: float) -> str:
    if not math.isfinite(value):
        return "na"
    if value < 1.2:
        return "<1.2"
    if value < 2.0:
        return "1.2-2"
    return "2+"


def bucket_hour(hour: int | None) -> str:
    if hour is None:
        return "na"
    if 0 <= hour < 7:
        return "asia_00_06"
    if 7 <= hour < 13:
        return "eu_07_12"
    if 13 <= hour < 21:
        return "us_13_20"
    return "late_21_23"


def session_bucket(hour: int | None) -> str:
    if hour is None:
        return "na"
    return "EU_US" if 7 <= hour < 21 else "ASIA_LATE"


def get_micro(snapshot: dict[str, Any], key: str) -> float:
    micro = snapshot.get("microstructure") or {}
    if not micro:
        micro = ((snapshot.get("engine_vars") or {}).get("micro") or {})
    return safe_float(micro.get(key))


def load_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    signals = {row["id"]: row for row in read_jsonl(SIGNALS_PATH) if row.get("id")}
    labels = {row["signal_id"]: row for row in read_jsonl(LABELS_PATH) if row.get("signal_id")}
    snapshots = {
        row["signal_id"]: row
        for row in read_jsonl(SNAPSHOT_PATH)
        if row.get("signal_id") and row.get("source") == "ws_main_screener"
    }

    rows: list[dict[str, Any]] = []
    snapshot_rows: list[dict[str, Any]] = []
    for sid, signal in signals.items():
        label = labels.get(sid)
        if not label:
            continue
        ts = parse_ts(signal.get("ts") or label.get("ts"))
        outcome_raw = str(label.get("outcome") or "").upper()
        outcome = normalize_outcome(outcome_raw)
        side = str(signal.get("side") or "")
        entry = safe_float(signal.get("entry"))
        sl = safe_float(signal.get("sl"))
        exit_price = safe_float(label.get("exit_price"))
        row = {
            "signal_id": sid,
            "ts": signal.get("ts") or label.get("ts"),
            "hour_utc": ts.hour if ts else None,
            "symbol": signal.get("symbol") or label.get("symbol"),
            "regime": signal.get("regime") or label.get("regime"),
            "style": signal.get("trade_style"),
            "side": side,
            "outcome": outcome,
            "decisive": outcome in {"TP", "SL"},
            "win": outcome == "TP",
            "entry": entry,
            "sl": sl,
            "exit_price": exit_price,
            "exit_r": calc_exit_r(side, entry, sl, exit_price, outcome_raw),
        }
        rows.append(row)

        snap = snapshots.get(sid)
        if snap:
            context = snap.get("context") or {}
            engine = snap.get("engine_vars") or {}
            enriched = dict(row)
            enriched.update(
                {
                    "adx_1h": safe_float(context.get("adx_1h", engine.get("adx_1h"))),
                    "adx_4h": safe_float(context.get("adx_4h", engine.get("adx_4h"))),
                    "vol_ratio_sig": safe_float(context.get("vol_ratio_sig", engine.get("vol_ratio_sig"))),
                    "slope_1h": safe_float(context.get("slope_1h")),
                    "slope_15m": safe_float(context.get("slope_15m")),
                    "rsi_1h": safe_float(engine.get("rsi_1h")),
                    "rsi_15m": safe_float(engine.get("rsi_15m")),
                    "bb_expanding": bool(context.get("bb_expanding", engine.get("bb_expanding", False))),
                    "day_position": safe_float(context.get("day_position", engine.get("day_position"))),
                    "obi_top5": get_micro(snap, "obi_top5"),
                    "trade_delta_100": get_micro(snap, "trade_delta_100"),
                }
            )
            snapshot_rows.append(enriched)
    return rows, snapshot_rows


def grouped(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    out: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        out[tuple(row.get(key) for key in keys)].append(row)
    return out


def add_stats_table(lines: list[str], title: str, groups: dict[Any, list[dict[str, Any]]], label_names: list[str]) -> None:
    label_cols = " | ".join(label_names)
    sep_cols = " | ".join(["---"] * len(label_names))
    lines.extend([f"## {title}", "", f"| {label_cols} | n | WR | avg_R | PF | note |", f"| {sep_cols} | ---: | ---: | ---: | ---: | --- |"])
    def sort_key(item: tuple[Any, list[dict[str, Any]]]) -> tuple:
        key = item[0]
        key_tuple = key if isinstance(key, tuple) else (key,)
        out = []
        for value in key_tuple:
            out.append((0, value) if isinstance(value, int) else (1, str(value)))
        return tuple(out)

    for key, items in sorted(groups.items(), key=sort_key):
        key_tuple = key if isinstance(key, tuple) else (key,)
        s = stats(items)
        labels = " | ".join(str(v) for v in key_tuple)
        pf = "inf" if math.isinf(s["pf"]) else fmt(s["pf"])
        lines.append(f"| {labels} | {s['n']} | {fmt_wr(s['wr'])} | {fmt(s['avg_r'])} | {pf} | {sample_note(s['n'])} |")
    lines.append("")


def truth_report(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# WS Truth Report - Block 1",
        "",
        "Scope: only `logs/signals/main_signals.jsonl` joined with `logs/signals/main_signals_labels.jsonl`.",
        "Archive REST data is excluded from all metrics and summary statements.",
        "",
        f"- total labeled WS signals: {len(rows)}",
        f"- decisive TP/SL signals: {stats(rows)['n']}",
        f"- TIME/non-decisive signals: {sum(1 for row in rows if not row['decisive'])}",
        "",
        "R note: price-based R is used when valid. For rounded micro-price TP rows where price precision makes R non-positive, the fallback is `TP1=+0.5R`, `TP2=+1.0R`; `SL=-1R`.",
        "",
        "Rule: `n < 5` means no conclusion; `5 <= n < 10` means preliminary, not actionable.",
        "",
    ]
    add_stats_table(lines, "Regime x Style", grouped(rows, ("regime", "style")), ["regime", "style"])
    add_stats_table(lines, "By Pair", grouped(rows, ("symbol",)), ["symbol"])
    add_stats_table(lines, "By UTC Hour", grouped(rows, ("hour_utc",)), ["hour_utc"])
    return "\n".join(lines) + "\n"


def pattern_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("regime"),
        row.get("style"),
        bucket_adx_4h(row.get("adx_4h", float("nan"))),
        bucket_vol_ratio(row.get("vol_ratio_sig", float("nan"))),
        bucket_hour(row.get("hour_utc")),
    )


def split_eval(rows: list[dict[str, Any]], name: str, predicate) -> dict[str, Any]:
    yes = [row for row in rows if predicate(row)]
    no = [row for row in rows if not predicate(row)]
    sy = stats(yes)
    sn = stats(no)
    return {
        "feature": name,
        "yes_n": sy["n"],
        "yes_wr": sy["wr"],
        "yes_avg_r": sy["avg_r"],
        "no_n": sn["n"],
        "no_wr": sn["wr"],
        "no_avg_r": sn["avg_r"],
        "wr_gap": sy["wr"] - sn["wr"] if math.isfinite(sy["wr"]) and math.isfinite(sn["wr"]) else float("nan"),
    }


def pattern_report(snapshot_rows: list[dict[str, Any]]) -> str:
    decisive = [row for row in snapshot_rows if row["decisive"]]
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in decisive:
        groups[pattern_key(row)].append(row)
    ranked = [(key, stats(items)) for key, items in groups.items() if stats(items)["n"] >= 3]
    top = sorted([item for item in ranked if item[1]["wr"] > 70.0], key=lambda kv: (-kv[1]["wr"], -kv[1]["n"], -kv[1]["avg_r"]))
    worst = sorted([item for item in ranked if item[1]["wr"] < 40.0], key=lambda kv: (kv[1]["wr"], -kv[1]["n"], kv[1]["avg_r"]))

    splits = [
        split_eval(decisive, "adx_4h >= 40", lambda row: safe_float(row.get("adx_4h")) >= 40),
        split_eval(decisive, "vol_ratio_sig >= 2", lambda row: safe_float(row.get("vol_ratio_sig")) >= 2),
        split_eval(decisive, "abs(slope_1h) >= 30", lambda row: abs(safe_float(row.get("slope_1h"))) >= 30),
        split_eval(decisive, "EU/US session", lambda row: session_bucket(row.get("hour_utc")) == "EU_US"),
        split_eval(decisive, "bb_expanding = true", lambda row: bool(row.get("bb_expanding"))),
        split_eval(decisive, "obi_top5 >= 0.5", lambda row: safe_float(row.get("obi_top5")) >= 0.5),
    ]
    splits = sorted(splits, key=lambda row: abs(row["wr_gap"]) if math.isfinite(row["wr_gap"]) else -1, reverse=True)

    # Conservative candidate: only use simple gates with at least five surviving decisive rows.
    candidates = [
        ("adx_4h >= 40", lambda row: safe_float(row.get("adx_4h")) >= 40),
        ("vol_ratio_sig >= 2", lambda row: safe_float(row.get("vol_ratio_sig")) >= 2),
        ("abs(slope_1h) >= 30", lambda row: abs(safe_float(row.get("slope_1h"))) >= 30),
        ("EU/US session", lambda row: session_bucket(row.get("hour_utc")) == "EU_US"),
        ("bb_expanding = true", lambda row: bool(row.get("bb_expanding"))),
        ("obi_top5 >= 0.5", lambda row: safe_float(row.get("obi_top5")) >= 0.5),
    ]
    candidate_rows = []
    for name, pred in candidates:
        kept = [row for row in decisive if pred(row)]
        cut = len(decisive) - len(kept)
        s = stats(kept)
        if s["n"] >= 5:
            candidate_rows.append((name, cut, s))
    candidate_rows = sorted(candidate_rows, key=lambda item: (item[2]["wr"], item[2]["avg_r"]), reverse=True)

    lines = [
        "# WS Pattern Mining Report",
        "",
        "Scope: `signal_snapshot.jsonl` joined with `main_signals_labels.jsonl` by `signal_id`.",
        "Only matching `source=ws_main_screener` snapshots are used. This is partial coverage, not the full 85-signal truth set.",
        "",
        f"- snapshot rows joined to labels: {len(snapshot_rows)}",
        f"- decisive TP/SL snapshot rows: {len(decisive)}",
        "",
        "## High-WR Buckets",
        "",
        "| regime | style | adx_4h_bucket | vol_ratio_bucket | hour_bucket | n | WR | avg_R |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for key, s in top[:20]:
        lines.append(f"| {' | '.join(str(v) for v in key)} | {s['n']} | {fmt_wr(s['wr'])} | {fmt(s['avg_r'])} |")
    if not top:
        lines.append("| none | - | - | - | - | 0 | n/a | n/a |")
    lines.extend(["", "## Low-WR Buckets", "", "| regime | style | adx_4h_bucket | vol_ratio_bucket | hour_bucket | n | WR | avg_R |", "| --- | --- | --- | --- | --- | ---: | ---: | ---: |"])
    for key, s in worst[:20]:
        lines.append(f"| {' | '.join(str(v) for v in key)} | {s['n']} | {fmt_wr(s['wr'])} | {fmt(s['avg_r'])} |")
    if not worst:
        lines.append("| none | - | - | - | - | 0 | n/a | n/a |")

    lines.extend(["", "## Single-Feature Separation", "", "| Feature | yes_n | yes_WR | yes_avg_R | no_n | no_WR | no_avg_R | WR gap |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for row in splits:
        lines.append(
            f"| {row['feature']} | {row['yes_n']} | {fmt_wr(row['yes_wr'])} | {fmt(row['yes_avg_r'])} | "
            f"{row['no_n']} | {fmt_wr(row['no_wr'])} | {fmt(row['no_avg_r'])} | {fmt(row['wr_gap'], 'pp')} |"
        )

    lines.extend(
        [
            "",
            "## Candidate Gate Backtest On Snapshot-Covered Rows",
            "",
            "| Gate | decisive cut | kept_n | kept_WR | kept_avg_R | note |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for name, cut, s in candidate_rows:
        lines.append(f"| {name} | {cut} | {s['n']} | {fmt_wr(s['wr'])} | {fmt(s['avg_r'])} | {sample_note(s['n'])} |")
    if not candidate_rows:
        lines.append("| none | 0 | 0 | n/a | n/a | no candidate has enough sample |")

    lines.extend(
        [
            "",
            "## Filtering Concept",
            "",
            "Add a non-invasive context gate after `compute_signal()` returns `ENTRY` and before the signal is logged/sent.",
            "The gate should read only fields already present in `SignalResult.context`, `SignalResult.engine_vars`, and `SignalResult.microstructure`.",
            "",
            "Implementation sketch:",
            "",
            "1. Keep current candle-rule engine unchanged.",
            "2. Add `strategy.context_gate.enabled` and per-bucket thresholds in `config.yaml`.",
            "3. In `ws_main_screener.py`, after `result.entry_signal == \"ENTRY\"`, call a small `context_gate_allows(result)` helper.",
            "4. If rejected, log to `signal_log_notrade.jsonl` with `drop_reason=context_gate:<reason>` and do not write `main_signals.jsonl`.",
            "5. Re-run this script after every new label batch; do not promote any gate with `kept_n < 10`.",
            "",
            "Do not hard-code the current snapshot-mined gate yet. The raw snapshot file has 61 rows, but only 59 are matching `ws_main_screener` rows and only 43 are decisive TP/SL rows, so it is hypothesis-grade only.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, snapshot_rows = load_rows()
    DATA_PATH.write_text(json.dumps({"rows": rows, "snapshot_rows": snapshot_rows}, ensure_ascii=True, indent=2), encoding="utf-8")
    TRUTH_REPORT_PATH.write_text(truth_report(rows), encoding="utf-8")
    PATTERN_REPORT_PATH.write_text(pattern_report(snapshot_rows), encoding="utf-8")
    print(f"saved {TRUTH_REPORT_PATH}")
    print(f"saved {PATTERN_REPORT_PATH}")
    print(f"saved {DATA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
