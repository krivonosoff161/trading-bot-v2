from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
LABELS_PATH = ROOT / "logs" / "signals" / "main_signals_labels.jsonl"
SIGNALS_PATH = ROOT / "logs" / "signals" / "main_signals.jsonl"
SNAPSHOT_PATH = ROOT / "logs" / "signals" / "signal_snapshot.jsonl"
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
REPORT_PATH = ROOT / "docs" / "snapshot_coverage_audit.md"
DATA_PATH = OUT_DIR / "snapshot_coverage_audit.json"


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


def pct(part: int, total: int) -> str:
    return "n/a" if total <= 0 else f"{part / total * 100:.1f}%"


def table(counter: Counter[str], title: str) -> list[str]:
    lines = [f"## {title}", "", "| Key | Missing context |", "| --- | ---: |"]
    if not counter:
        lines.append("| none | 0 |")
    for key, value in counter.most_common():
        lines.append(f"| {key or 'UNKNOWN'} | {value} |")
    lines.append("")
    return lines


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    labels = {row["signal_id"]: row for row in read_jsonl(LABELS_PATH) if row.get("signal_id")}
    signals = {row["id"]: row for row in read_jsonl(SIGNALS_PATH) if row.get("id")}
    raw_snapshots = {row["signal_id"]: row for row in read_jsonl(SNAPSHOT_PATH) if row.get("signal_id")}
    snapshots = {
        row["signal_id"]: row
        for row in read_jsonl(SNAPSHOT_PATH)
        if row.get("signal_id") and row.get("source") == "ws_main_screener"
    }
    excluded_snapshots = [row for row in raw_snapshots.values() if row.get("source") != "ws_main_screener"]

    labeled_ids = set(labels)
    with_snapshot = labeled_ids & set(snapshots)
    missing_ids = sorted(labeled_ids - set(snapshots), key=lambda sid: labels[sid].get("ts", ""))

    missing_by_regime: Counter[str] = Counter()
    missing_by_style: Counter[str] = Counter()
    missing_by_pair: Counter[str] = Counter()
    missing_rows: list[dict[str, Any]] = []
    for sid in missing_ids:
        label = labels[sid]
        signal = signals.get(sid, {})
        regime = signal.get("regime") or label.get("regime") or "UNKNOWN"
        style = signal.get("trade_style") or "UNKNOWN"
        symbol = signal.get("symbol") or label.get("symbol") or "UNKNOWN"
        missing_by_regime[str(regime)] += 1
        missing_by_style[str(style)] += 1
        missing_by_pair[str(symbol)] += 1
        missing_rows.append(
            {
                "signal_id": sid,
                "ts": label.get("ts") or signal.get("ts"),
                "symbol": symbol,
                "regime": regime,
                "style": style,
                "outcome": label.get("outcome"),
            }
        )

    payload = {
        "labeled_ws_signals": len(labeled_ids),
        "snapshots_total_all_sources": len(raw_snapshots),
        "snapshots_total_ws_main_screener": len(snapshots),
        "excluded_non_ws_main_snapshots": len(excluded_snapshots),
        "with_snapshot": len(with_snapshot),
        "missing_context": len(missing_ids),
        "coverage_pct": (len(with_snapshot) / len(labeled_ids) * 100) if labeled_ids else None,
        "missing_by_regime": dict(missing_by_regime),
        "missing_by_style": dict(missing_by_style),
        "missing_by_pair": dict(missing_by_pair),
        "missing_rows": missing_rows,
    }
    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    lines = [
        "# Snapshot Coverage Audit - WS Main",
        "",
        "Source files:",
        "",
        f"- labels: `{LABELS_PATH.relative_to(ROOT)}`",
        f"- signals: `{SIGNALS_PATH.relative_to(ROOT)}`",
        f"- snapshots: `{SNAPSHOT_PATH.relative_to(ROOT)}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| labeled WS signals | {len(labeled_ids)} |",
        f"| raw snapshots, all sources | {len(raw_snapshots)} |",
        f"| excluded non-ws_main snapshots | {len(excluded_snapshots)} |",
        f"| matching snapshots | {len(with_snapshot)} |",
        f"| missing context | {len(missing_ids)} |",
        f"| snapshot coverage | {pct(len(with_snapshot), len(labeled_ids))} |",
        "",
        "Context-based analysis below is partial by construction. Missing snapshots must not be assumed random.",
        "The two excluded snapshots are `source=ws_scanner` and do not join to `main_signals_labels.jsonl`; they are not WS-main truth.",
        "",
    ]
    lines.extend(table(missing_by_regime, "Missing By Regime"))
    lines.extend(table(missing_by_style, "Missing By Style"))
    lines.extend(table(missing_by_pair, "Missing By Pair"))
    lines.extend(
        [
            "## Missing Rows",
            "",
            "| signal_id | ts | symbol | regime | style | outcome |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in missing_rows:
        lines.append(
            f"| {row['signal_id']} | {row.get('ts') or ''} | {row['symbol']} | "
            f"{row['regime']} | {row['style']} | {row.get('outcome') or ''} |"
        )
    if not missing_rows:
        lines.append("| none | - | - | - | - | - |")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"saved {REPORT_PATH}")
    print(f"saved {DATA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
