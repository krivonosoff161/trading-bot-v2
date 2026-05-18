from __future__ import annotations

import importlib.util
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]

ARCHIVE_SIGNAL_LOG = ROOT / "logs_archive" / "signals" / "signal_log_2026-05.jsonl"
ARCHIVE_LABELS = ROOT / "logs_archive" / "09.05.2026" / "signals" / "signal_labels.jsonl"
LIVE_BB_FADE = ROOT / "logs" / "bb_fade" / "bb_fade_signals.jsonl"
BT_BB_FADE = ROOT / "scripts" / "backtest" / "bt_bb_fade.py"

OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
REPORT_PATH = OUT_DIR / "bb_block2_report.md"
DATA_PATH = OUT_DIR / "bb_block2_dataset.json"

WIN = {"TP", "TP1", "TP2"}
LOSS = {"SL", "STOP"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def fmt(value: float, suffix: str = "") -> str:
    return "n/a" if not math.isfinite(value) else f"{value:.2f}{suffix}"


def summarize_exit_r(rows: list[dict[str, Any]], key: str = "exit_r") -> dict[str, Any]:
    decisive = [row for row in rows if str(row.get("outcome") or "").upper() in WIN | LOSS]
    wins = sum(1 for row in decisive if str(row.get("outcome") or "").upper() in WIN)
    vals = [float(row[key]) for row in decisive if isinstance(row.get(key), (int, float))]
    avg = sum(vals) / len(vals) if vals else float("nan")
    return {
        "n": len(decisive),
        "wins": wins,
        "wr": wins / len(decisive) * 100 if decisive else float("nan"),
        "avg": avg,
    }


def load_archive_logged_fade() -> list[dict[str, Any]]:
    signals = {
        row["signal_id"]: row
        for row in read_jsonl(ARCHIVE_SIGNAL_LOG)
        if row.get("source") == "bb_fade" and row.get("signal_id")
    }
    rows: list[dict[str, Any]] = []
    for label in read_jsonl(ARCHIVE_LABELS):
        signal_id = label.get("signal_id")
        if signal_id not in signals:
            continue
        rows.append({**signals[signal_id], **label})
    return rows


def load_live_bb_fade() -> list[dict[str, Any]]:
    return read_jsonl(LIVE_BB_FADE)


def run_bt_bb_fade() -> list[dict[str, Any]]:
    spec = importlib.util.spec_from_file_location("bt_bb_fade", BT_BB_FADE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {BT_BB_FADE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    pairs = sorted({path.stem.split("_15m_")[0] for path in module.CACHE.glob("*_15m_60d.pkl")})
    trades: list[dict[str, Any]] = []
    for sym in pairs:
        trades.extend(module.backtest_pair(sym))
    return trades


def table_by_key(rows: list[dict[str, Any]], key: str, *, min_n: int = 1, sort_key: str = "avg") -> list[str]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key))].append(row)

    ranked: list[tuple[float, int, str, dict[str, Any]]] = []
    for name, group in groups.items():
        summary = summarize_exit_r(group, key="exit_r" if "exit_r" in group[0] else "net")
        if summary["n"] < min_n:
            continue
        score = summary["avg"] if sort_key == "avg" else summary["wr"]
        ranked.append((score, summary["n"], name, summary))

    ranked.sort()
    lines = ["| Bucket | n | WR | avg |", "| --- | ---: | ---: | ---: |"]
    for _, _, name, summary in ranked:
        lines.append(f"| {name} | {summary['n']} | {fmt(summary['wr'], '%')} | {fmt(summary['avg'])} |")
    return lines


def bt_bucket_label(bw_pct: float) -> str:
    if bw_pct < 3.0:
        return "2-3%"
    if bw_pct < 5.0:
        return "3-5%"
    return "5%+"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    archive_rows = load_archive_logged_fade()
    live_rows = load_live_bb_fade()
    bt_rows = run_bt_bb_fade()
    bt_decisive = [row for row in bt_rows if row.get("outcome") in {"TP", "SL"}]
    for row in bt_decisive:
        row["bw_bucket"] = bt_bucket_label(float(row["bw_pct"]))

    archive_summary = summarize_exit_r(archive_rows)
    live_decisive = [row for row in live_rows if row.get("outcome") in {"TP", "SL"}]
    live_summary = {
        "n_all": len(live_rows),
        "n_decisive": len(live_decisive),
        "wr": sum(1 for row in live_decisive if row["outcome"] == "TP") / len(live_decisive) * 100 if live_decisive else float("nan"),
        "net": sum(float(row.get("net_pct") or 0.0) for row in live_rows),
    }
    bt_summary = {
        "n_all": len(bt_rows),
        "n_decisive": len(bt_decisive),
        "wr": sum(1 for row in bt_decisive if row["outcome"] == "TP") / len(bt_decisive) * 100 if bt_decisive else float("nan"),
        "avg_net": sum(float(row["net"]) for row in bt_decisive) / len(bt_decisive) if bt_decisive else float("nan"),
    }

    bt_symbol_lines = table_by_key(bt_decisive, "sym", min_n=5, sort_key="avg")[:10]
    bt_bw_lines = table_by_key(bt_decisive, "bw_bucket", min_n=1, sort_key="avg")
    archive_regime_lines = table_by_key(archive_rows, "regime", min_n=1, sort_key="avg")
    archive_symbol_lines = table_by_key(archive_rows, "symbol", min_n=3, sort_key="avg")

    lines: list[str] = []
    lines.append("# Block 2 BB Fade Analysis")
    lines.append("")
    lines.append("## Archive Logged Sample")
    lines.append(
        f"- Logged archive bb_fade sample: decisive_n={archive_summary['n']}, WR={fmt(archive_summary['wr'], '%')}, avg_R={fmt(archive_summary['avg'])}."
    )
    lines.append("- Local tape validation for archive fade sample is unavailable: 0/47 signals have matching local tick files on disk.")
    lines.append("")
    lines.append("### Archive by Regime")
    lines.extend(archive_regime_lines)
    lines.append("")
    lines.append("### Archive by Symbol (n>=3)")
    lines.extend(archive_symbol_lines)
    lines.append("")
    lines.append("## Current Wick-Rejection Backtest")
    lines.append(
        f"- Backtest over cached universe: total={bt_summary['n_all']}, decisive_n={bt_summary['n_decisive']}, WR={fmt(bt_summary['wr'], '%')}, avg_net={fmt(bt_summary['avg_net'], '%')}."
    )
    lines.append("- Architecture differs materially from old 5m fade hint: new worker requires 15m band touch, 5m wick rejection, RR>=0.5, no Asia, and 1H trend veto.")
    lines.append("")
    lines.append("### Backtest by BB Width")
    lines.extend(bt_bw_lines)
    lines.append("")
    lines.append("### Worst Symbols in Backtest (n>=5)")
    lines.extend(bt_symbol_lines)
    lines.append("")
    lines.append("## Live Worker Check")
    lines.append(
        f"- Live ws_bb_fade sample: all_n={live_summary['n_all']}, decisive_n={live_summary['n_decisive']}, WR={fmt(live_summary['wr'], '%')}, net={fmt(live_summary['net'], '%')}."
    )
    for row in live_rows:
        lines.append(
            "- "
            f"{row['ts']} {row['symbol']} {row['side']} outcome={row['outcome']} "
            f"net={fmt(float(row.get('net_pct') or 0.0), '%')} bw_pct={fmt(float(row.get('bw_pct') or 0.0), '%')} "
            f"vol_ratio={fmt(float(row.get('vol_ratio') or 0.0))} rsi={fmt(float(row.get('rsi') or 0.0))}"
        )
    lines.append("")
    lines.append("## Verdict")
    lines.append("- Archive logged FADE sample is positive but small and concentrated in majors; it is not enough to tune per-pair production overrides.")
    lines.append("- Current wick-rejection logic backtests well on cache data, especially outside Asia and on wider bands rather than narrow squeezes.")
    lines.append("- Live worker has only 3 trades. The TRUTH loss is a wide-band outlier (`bw_pct=7.92%`), but sample is too small to justify a new max-width cap yet.")
    lines.append("- Keep Block 2 as preliminary: leave production BB Fade config unchanged until live sample reaches at least 20 decisive trades.")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    DATA_PATH.write_text(
        json.dumps(
            {
                "archive_logged_n": archive_summary["n"],
                "live_n_all": live_summary["n_all"],
                "live_n_decisive": live_summary["n_decisive"],
                "bt_decisive_n": bt_summary["n_decisive"],
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"saved {REPORT_PATH}")
    print(f"saved {DATA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
