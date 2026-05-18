from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]

LIVE_MAIN_SIGNALS = ROOT / "logs" / "signals" / "main_signals.jsonl"
LIVE_MAIN_LABELS = ROOT / "logs" / "signals" / "main_signals_labels.jsonl"
SIGNAL_SNAPSHOT = ROOT / "logs" / "signals" / "signal_snapshot.jsonl"

ARCHIVE_SIGNAL_LOG = ROOT / "logs_archive" / "09.05.2026" / "signals" / "signal_log.jsonl"
ARCHIVE_SIGNAL_LABELS = ROOT / "logs_archive" / "09.05.2026" / "signals" / "signal_labels.jsonl"
MONTH_SIGNAL_LOG = ROOT / "logs_archive" / "signals" / "signal_log_2026-05.jsonl"

OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
REPORT_PATH = OUT_DIR / "main_block1_report.md"
DATA_PATH = OUT_DIR / "main_block1_dataset.json"

WIN_OUTCOMES = {"TP", "TP1", "TP2"}
LOSS_OUTCOMES = {"SL", "STOP"}
TIME_OUTCOMES = {"TIME", "TIME_EXIT", "EXPIRE"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def parse_ts(value: str | None = None, *, ts_ms: int | None = None) -> datetime | None:
    if ts_ms is not None:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
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


def base_symbol(symbol: str) -> str:
    return symbol.removesuffix("-SWAP")


def normalize_outcome(value: Any) -> str:
    raw = str(value or "").upper()
    if raw in WIN_OUTCOMES:
        return "TP"
    if raw in LOSS_OUTCOMES:
        return "SL"
    if raw in TIME_OUTCOMES:
        return "TIME"
    return raw


def calc_exit_r(side: str, entry: float, sl: float, exit_price: float) -> float:
    risk = abs(entry - sl)
    if not math.isfinite(risk) or risk <= 0:
        return float("nan")
    if side == "buy":
        return (exit_price - entry) / risk
    return (entry - exit_price) / risk


def bucket_adx_4h(value: float) -> str:
    if not math.isfinite(value):
        return "na"
    if value < 20:
        return "<20"
    if value < 30:
        return "20-30"
    if value < 40:
        return "30-40"
    return "40+"


def bucket_vol_ratio(value: float) -> str:
    if not math.isfinite(value):
        return "na"
    if value < 0.8:
        return "<0.8"
    if value < 1.2:
        return "0.8-1.2"
    if value < 2.0:
        return "1.2-2.0"
    if value < 3.0:
        return "2.0-3.0"
    return "3.0+"


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return float("nan"), float("nan")
    phat = wins / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n) / denom
    return center - margin, center + margin


def load_live_rows() -> list[dict[str, Any]]:
    signals = {row["id"]: row for row in read_jsonl(LIVE_MAIN_SIGNALS) if row.get("id")}
    labels = {row["signal_id"]: row for row in read_jsonl(LIVE_MAIN_LABELS) if row.get("signal_id")}

    snapshots: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(SIGNAL_SNAPSHOT):
        if row.get("source") not in {"ws_main_screener", "ws_scanner"}:
            continue
        signal_id = row.get("signal_id")
        if signal_id:
            snapshots[signal_id] = row

    rows: list[dict[str, Any]] = []
    for signal_id, sig in signals.items():
        lab = labels.get(signal_id)
        if not lab:
            continue
        snap = snapshots.get(signal_id, {})
        context = snap.get("context") or {}
        engine = snap.get("engine_vars") or {}
        ts = parse_ts(sig.get("ts"))
        entry = safe_float(sig.get("entry"))
        sl = safe_float(sig.get("sl"))
        exit_price = safe_float(lab.get("exit_price"))
        outcome = normalize_outcome(lab.get("outcome"))
        exit_r = calc_exit_r(str(sig.get("side")), entry, sl, exit_price) if math.isfinite(exit_price) else float("nan")
        if outcome == "SL":
            exit_r = -1.0
        rows.append(
            {
                "signal_id": signal_id,
                "source": "live_main",
                "ts": sig.get("ts"),
                "date": ts.strftime("%Y-%m-%d") if ts else "",
                "hour_utc": ts.hour if ts else None,
                "symbol": sig.get("symbol"),
                "pair": base_symbol(str(sig.get("symbol"))),
                "side": sig.get("side"),
                "regime": sig.get("regime"),
                "style": sig.get("trade_style"),
                "outcome": outcome,
                "decisive": outcome in {"TP", "SL"},
                "win": outcome == "TP",
                "entry": entry,
                "sl": sl,
                "tp1": safe_float(sig.get("tp1")),
                "tp2": safe_float(sig.get("tp2")),
                "exit_price": exit_price,
                "exit_r": exit_r,
                "hold_min": safe_float(lab.get("hold_min")),
                "mfe_r": float("nan"),
                "mae_r": float("nan"),
                "vol_ratio": safe_float(context.get("vol_ratio_sig", sig.get("vol_ratio"))),
                "adx_1h": safe_float(context.get("adx_1h", sig.get("adx_1h"))),
                "adx_4h": safe_float(context.get("adx_4h", engine.get("adx_4h"))),
                "slope_15m": safe_float(context.get("slope_15m")),
                "slope_1h": safe_float(context.get("slope_1h")),
            }
        )
    return rows


def load_archive_rows() -> list[dict[str, Any]]:
    signals: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(ARCHIVE_SIGNAL_LOG):
        signal_id = row.get("signal_id")
        if signal_id:
            signals[signal_id] = row
    for row in read_jsonl(MONTH_SIGNAL_LOG):
        signal_id = row.get("signal_id")
        if signal_id:
            signals.setdefault(signal_id, row)

    rows: list[dict[str, Any]] = []
    for lab in read_jsonl(ARCHIVE_SIGNAL_LABELS):
        signal_id = lab.get("signal_id")
        sig = signals.get(signal_id)
        if not sig:
            continue
        ts = parse_ts(ts_ms=int(sig.get("ts_ms", 0)))
        outcome = normalize_outcome(lab.get("outcome"))
        rows.append(
            {
                "signal_id": signal_id,
                "source": "archive_scanner",
                "ts": ts.isoformat().replace("+00:00", "Z") if ts else "",
                "date": ts.strftime("%Y-%m-%d") if ts else "",
                "hour_utc": ts.hour if ts else None,
                "symbol": sig.get("symbol"),
                "pair": base_symbol(str(sig.get("symbol"))),
                "side": sig.get("side"),
                "regime": sig.get("regime"),
                "style": sig.get("style") or sig.get("trade_style"),
                "outcome": outcome,
                "decisive": outcome in {"TP", "SL"},
                "win": outcome == "TP",
                "entry": safe_float(sig.get("close")),
                "sl": safe_float(sig.get("sl")),
                "tp1": safe_float(sig.get("tp")),
                "tp2": safe_float(sig.get("tp2")),
                "exit_price": safe_float(lab.get("exit_price")),
                "exit_r": safe_float(lab.get("exit_r")),
                "hold_min": safe_float(lab.get("elapsed_m")),
                "mfe_r": safe_float(lab.get("mfe_r")),
                "mae_r": safe_float(lab.get("mae_r")),
                "vol_ratio": safe_float(sig.get("vol_ratio")),
                "adx_1h": safe_float(sig.get("adx_1h")),
                "adx_4h": safe_float(sig.get("adx_4h")),
                "slope_15m": float("nan"),
                "slope_1h": float("nan"),
            }
        )
    return rows


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        signal_id = row["signal_id"]
        prev = merged.get(signal_id)
        if prev is None:
            merged[signal_id] = row
            continue
        if prev["source"] == "archive_scanner" and row["source"] == "live_main":
            merged[signal_id] = row
    return list(merged.values())


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decisive = [row for row in rows if row["decisive"]]
    wins = sum(1 for row in decisive if row["win"])
    losses = sum(1 for row in decisive if row["outcome"] == "SL")
    avg_r_vals = [row["exit_r"] for row in decisive if math.isfinite(row["exit_r"])]
    avg_r = sum(avg_r_vals) / len(avg_r_vals) if avg_r_vals else float("nan")
    std_r = (
        (sum((value - avg_r) ** 2 for value in avg_r_vals) / len(avg_r_vals)) ** 0.5
        if avg_r_vals and math.isfinite(avg_r)
        else float("nan")
    )
    gross_profit = sum(value for value in avg_r_vals if value > 0)
    gross_loss = abs(sum(value for value in avg_r_vals if value < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else float("nan"))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for row in sorted(decisive, key=lambda item: item["ts"]):
        if not math.isfinite(row["exit_r"]):
            continue
        equity += row["exit_r"]
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    lo, hi = wilson_ci(wins, len(decisive))
    return {
        "n_all": len(rows),
        "n_decisive": len(decisive),
        "wins": wins,
        "losses": losses,
        "wr": wins / len(decisive) * 100 if decisive else float("nan"),
        "avg_r": avg_r,
        "std_r": std_r,
        "profit_factor": profit_factor,
        "max_dd": max_dd,
        "ci_low": lo * 100 if math.isfinite(lo) else float("nan"),
        "ci_high": hi * 100 if math.isfinite(hi) else float("nan"),
    }


def verdict(n_decisive: int) -> str:
    if n_decisive >= 30:
        return "solid"
    if n_decisive >= 10:
        return "preliminary"
    return "N/A"


def format_stats(label: str, payload: dict[str, Any]) -> str:
    wr = "n/a" if not math.isfinite(payload["wr"]) else f"{payload['wr']:.1f}%"
    avg_r = "n/a" if not math.isfinite(payload["avg_r"]) else f"{payload['avg_r']:+.2f}R"
    std_r = "n/a" if not math.isfinite(payload["std_r"]) else f"{payload['std_r']:.2f}"
    pf = "n/a" if not math.isfinite(payload["profit_factor"]) else f"{payload['profit_factor']:.2f}"
    max_dd = "n/a" if not math.isfinite(payload["max_dd"]) else f"{payload['max_dd']:+.2f}R"
    ci = (
        "n/a"
        if not math.isfinite(payload["ci_low"])
        else f"{payload['ci_low']:.1f}-{payload['ci_high']:.1f}%"
    )
    return (
        f"- {label}: n={payload['n_decisive']}, WR={wr}, avg_R={avg_r}, std_R={std_r}, "
        f"PF={pf}, max_DD={max_dd}, verdict={verdict(payload['n_decisive'])}, 95%CI={ci}"
    )


def matrix_row(label: str, payload: dict[str, Any]) -> str:
    wr = "n/a" if not math.isfinite(payload["wr"]) else f"{payload['wr']:.1f}%"
    avg_r = "n/a" if not math.isfinite(payload["avg_r"]) else f"{payload['avg_r']:+.2f}"
    std_r = "n/a" if not math.isfinite(payload["std_r"]) else f"{payload['std_r']:.2f}"
    pf = "n/a" if not math.isfinite(payload["profit_factor"]) else f"{payload['profit_factor']:.2f}"
    max_dd = "n/a" if not math.isfinite(payload["max_dd"]) else f"{payload['max_dd']:+.2f}"
    return f"| {label} | {payload['n_decisive']} | {wr} | {avg_r} | {std_r} | {pf} | {max_dd} | {verdict(payload['n_decisive'])} |"


def bucket_report(rows: list[dict[str, Any]], field: str, min_n: int = 10) -> tuple[list[str], list[str]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = row.get(field)
        if key in (None, ""):
            continue
        groups[str(key)].append(row)

    good: list[str] = []
    bad: list[str] = []
    for key, bucket_rows in sorted(groups.items()):
        summary = stats(bucket_rows)
        if summary["n_decisive"] < min_n or not math.isfinite(summary["wr"]):
            continue
        line = format_stats(f"{field}={key}", summary)
        if summary["wr"] >= 75.0:
            good.append(line)
        if summary["wr"] <= 30.0:
            bad.append(line)
    return good, bad


def time_breakdown(rows: list[dict[str, Any]]) -> list[str]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["hour_utc"] is None:
            continue
        groups[int(row["hour_utc"])].append(row)
    lines: list[str] = []
    for hour, hour_rows in sorted(groups.items()):
        summary = stats(hour_rows)
        if summary["n_decisive"] < 5:
            continue
        lines.append(format_stats(f"hour={hour:02d}", summary))
    return lines


def block_style_focus(rows: list[dict[str, Any]]) -> list[str]:
    focus = [row for row in rows if row["style"] == "SWING" and row["regime"] == "TRENDING"]
    decisive = [row for row in focus if row["decisive"]]
    if not decisive:
        return ["- No SWING x TRENDING decisive rows."]

    base = stats(focus)
    lines = [format_stats("SWING x TRENDING baseline", base)]

    thresholds = [0.5, 0.6, 0.8, 1.0]
    for threshold in thresholds:
        subset = [row for row in decisive if math.isfinite(row["tp1"]) and math.isfinite(row["entry"]) and math.isfinite(row["sl"]) and calc_exit_r(row["side"], row["entry"], row["sl"], row["tp1"]) >= threshold]
        if not subset:
            continue
        lines.append(format_stats(f"TP1_R>={threshold:.1f}", stats(subset)))

    hourly_good, hourly_bad = bucket_report(focus, "hour_utc", min_n=5)
    if hourly_good:
        lines.append("- Positive hour buckets:")
        lines.extend(hourly_good[:5])
    if hourly_bad:
        lines.append("- Negative hour buckets:")
        lines.extend(hourly_bad[:5])
    return lines


def regime_style_matrix(rows: list[dict[str, Any]]) -> list[str]:
    regimes = ["DRIFT", "TRENDING", "RANGING", "CHOPPY"]
    styles = ["FAST", "SWING", "FADE"]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["regime"]), str(row["style"]))].append(row)

    lines = [
        "| Bucket | n | WR | avg_R | std_R | PF | max_DD | verdict |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for regime in regimes:
        for style in styles:
            payload = stats(groups.get((regime, style), []))
            lines.append(matrix_row(f"{regime} x {style}", payload))
    return lines


def worst_pairs_in_bucket(rows: list[dict[str, Any]], regime: str, style: str, limit: int = 5) -> list[str]:
    bucket = [row for row in rows if row["regime"] == regime and row["style"] == style and row["decisive"]]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in bucket:
        groups[str(row["pair"])].append(row)

    ranked: list[tuple[float, int, str, dict[str, Any]]] = []
    for pair, pair_rows in groups.items():
        payload = stats(pair_rows)
        avg_r = payload["avg_r"] if math.isfinite(payload["avg_r"]) else 999.0
        ranked.append((avg_r, -payload["n_decisive"], pair, payload))
    ranked.sort()

    lines: list[str] = []
    for _, _, pair, payload in ranked[:limit]:
        lines.append(format_stats(pair, payload))
    return lines


def compare_sources(rows: list[dict[str, Any]]) -> list[str]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["regime"]), str(row["style"]))].append(row)

    lines = [
        "| Bucket | live_n | live_WR | live_avg_R | archive_n | archive_WR | archive_avg_R | bias_note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for key in sorted(groups):
        bucket_rows = groups[key]
        live_rows = [row for row in bucket_rows if row["source"] == "live_main"]
        archive_rows = [row for row in bucket_rows if row["source"] == "archive_scanner"]
        live_stats = stats(live_rows)
        archive_stats = stats(archive_rows)
        live_wr = live_stats["wr"]
        archive_wr = archive_stats["wr"]
        live_avg = live_stats["avg_r"]
        archive_avg = archive_stats["avg_r"]

        live_wr_text = "n/a" if not math.isfinite(live_wr) else f"{live_wr:.1f}%"
        archive_wr_text = "n/a" if not math.isfinite(archive_wr) else f"{archive_wr:.1f}%"
        live_avg_text = "n/a" if not math.isfinite(live_avg) else f"{live_avg:+.2f}"
        archive_avg_text = "n/a" if not math.isfinite(archive_avg) else f"{archive_avg:+.2f}"

        note = "insufficient split sample"
        if live_stats["n_decisive"] >= 10 and archive_stats["n_decisive"] >= 10 and math.isfinite(live_wr) and math.isfinite(archive_wr):
            wr_gap = abs(live_wr - archive_wr)
            avg_gap = abs(live_avg - archive_avg) if math.isfinite(live_avg) and math.isfinite(archive_avg) else 0.0
            if wr_gap >= 15 or avg_gap >= 0.20:
                note = "bias likely, treat live as current truth"
            else:
                note = "no major live/archive bias"
        lines.append(
            f"| {key[0]} x {key[1]} | {live_stats['n_decisive']} | {live_wr_text} | {live_avg_text} | "
            f"{archive_stats['n_decisive']} | {archive_wr_text} | {archive_avg_text} | {note} |"
        )
    return lines


def advanced_checks(rows: list[dict[str, Any]]) -> list[str]:
    archive_rows = [row for row in rows if row["source"] == "archive_scanner"]
    decisive = [row for row in archive_rows if row["decisive"]]
    time_rows = [row for row in archive_rows if row["outcome"] == "TIME"]
    sl_rows = [row for row in archive_rows if row["outcome"] == "SL"]

    lines: list[str] = []
    if decisive:
        slope_bins = {
            "slope15m<-10": [row for row in decisive if math.isfinite(row["slope_15m"]) and row["slope_15m"] < -10],
            "-10..10": [row for row in decisive if math.isfinite(row["slope_15m"]) and -10 <= row["slope_15m"] <= 10],
            ">10": [row for row in decisive if math.isfinite(row["slope_15m"]) and row["slope_15m"] > 10],
        }
        for label, bucket_rows in slope_bins.items():
            if len(bucket_rows) >= 5:
                lines.append(format_stats(label, stats(bucket_rows)))

    time_mfe = [row for row in time_rows if math.isfinite(row["mfe_r"]) and row["mfe_r"] > 0.5]
    lines.append(f"- TIME with MFE>0.5R: {len(time_mfe)} / {len(time_rows)} archive TIME rows.")

    sl_soft_mae = [row for row in sl_rows if math.isfinite(row["mae_r"]) and row["mae_r"] < 0.5]
    lines.append(f"- SL with MAE<0.5R before stop: {len(sl_soft_mae)} / {len(sl_rows)} archive SL rows.")
    lines.append("- Live ws_main labels do not contain MFE/MAE, so advanced metrics here are archive-only unless recomputed from candles.")
    return lines


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = dedupe_rows(load_archive_rows() + load_live_rows())
    rows.sort(key=lambda row: row["ts"])

    payload = {
        "row_count": len(rows),
        "decisive_count": sum(1 for row in rows if row["decisive"]),
        "sources": {
            "archive_scanner": sum(1 for row in rows if row["source"] == "archive_scanner"),
            "live_main": sum(1 for row in rows if row["source"] == "live_main"),
        },
    }
    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# Block 1 Main Analysis")
    lines.append("")
    lines.append(format_stats("Unified baseline", stats(rows)))
    lines.append(f"- Source mix: archive_scanner={payload['sources']['archive_scanner']}, live_main={payload['sources']['live_main']}")
    lines.append("")

    for regime in ["DRIFT", "TRENDING", "RANGING", "CHOPPY"]:
        regime_rows = [row for row in rows if row["regime"] == regime]
        if not regime_rows:
            continue
        lines.append(f"## Regime {regime}")
        lines.append(format_stats("baseline", stats(regime_rows)))
        for field in ["hour_utc", "pair", "side"]:
            good, bad = bucket_report(regime_rows, field)
            if good:
                lines.append(f"- strong {field} buckets:")
                lines.extend(good[:8])
            if bad:
                lines.append(f"- anti {field} buckets:")
                lines.extend(bad[:8])

        adx_rows = []
        for row in regime_rows:
            row = dict(row)
            row["adx_4h_bucket"] = bucket_adx_4h(row["adx_4h"])
            row["vol_ratio_bucket"] = bucket_vol_ratio(row["vol_ratio"])
            adx_rows.append(row)
        for field in ["adx_4h_bucket", "vol_ratio_bucket"]:
            good, bad = bucket_report(adx_rows, field)
            if good:
                lines.append(f"- strong {field} buckets:")
                lines.extend(good[:8])
            if bad:
                lines.append(f"- anti {field} buckets:")
                lines.extend(bad[:8])
        seasonality = time_breakdown(regime_rows)
        if seasonality:
            lines.append("- hourly seasonality:")
            lines.extend(seasonality[:12])
        lines.append("")

    lines.append("## Style Focus")
    style_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        style_groups[(str(row["style"]), str(row["regime"]))].append(row)
    for key in sorted(style_groups):
        lines.append(format_stats(f"{key[0]} x {key[1]}", stats(style_groups[key])))
    lines.append("")
    lines.append("## Regime x Style Matrix")
    lines.extend(regime_style_matrix(rows))
    lines.append("")
    lines.extend(block_style_focus(rows))
    lines.append("")
    lines.append("## Worst Pairs: SWING x TRENDING")
    lines.extend(worst_pairs_in_bucket(rows, "TRENDING", "SWING", limit=5))
    lines.append("")
    lines.append("## Live vs Archive")
    lines.extend(compare_sources(rows))
    lines.append("")

    lines.append("## Advanced")
    lines.extend(advanced_checks(rows))
    lines.append("")
    lines.append("## Coverage Notes")
    lines.append("- Archive scanner labels provide MFE/MAE; live ws_main labels currently do not.")
    lines.append("- adx_4h and slope_15m for live rows come from signal_snapshot context/engine_vars.")
    lines.append("- Unified dataset is deduped by signal_id with live_main preferred over archive copy if both exist.")
    lines.append("- Buckets use decisive rows only for WR and avg_R.")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"saved {REPORT_PATH}")
    print(f"saved {DATA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
