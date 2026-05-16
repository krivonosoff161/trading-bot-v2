"""Test simple post-filters on labeled main screener signals.

Reads:
  logs/signals/main_signals.jsonl
  logs/signals/main_signals_labels.jsonl
"""
import argparse
import json
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SIGNALS = ROOT / "logs" / "signals" / "main_signals.jsonl"
DEFAULT_LABELS = ROOT / "logs" / "signals" / "main_signals_labels.jsonl"

WIN_OUTCOMES = {"TP1", "TP2"}
LOSS_OUTCOMES = {"SL"}
COUNTED_OUTCOMES = WIN_OUTCOMES | LOSS_OUTCOMES


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"skip invalid json: {path}:{line_no}: {exc}")
    return rows


def as_float(value, default: float = 1.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n", ""}:
            return False
    return default


def signal_key(row: dict) -> str | None:
    key = row.get("id") or row.get("signal_id")
    return str(key) if key else None


def label_key(row: dict) -> str | None:
    key = row.get("signal_id") or row.get("id")
    return str(key) if key else None


def load_joined(signals_path: Path, labels_path: Path) -> tuple[list[dict], dict]:
    signals = {}
    duplicate_signals = 0
    for row in load_jsonl(signals_path):
        key = signal_key(row)
        if not key:
            continue
        duplicate_signals += int(key in signals)
        signals[key] = row

    joined = []
    duplicate_labels = 0
    seen_labels = set()
    unmatched_labels = 0
    time_labels = 0

    for label in load_jsonl(labels_path):
        key = label_key(label)
        if not key:
            continue
        duplicate_labels += int(key in seen_labels)
        seen_labels.add(key)

        signal = signals.get(key)
        if signal is None:
            unmatched_labels += 1
            continue

        outcome = str(label.get("outcome", "")).upper()
        if outcome == "TIME":
            time_labels += 1
            continue
        if outcome not in COUNTED_OUTCOMES:
            continue

        merged = dict(signal)
        merged.update(label)
        merged["_signal_id"] = key
        merged["outcome"] = outcome
        merged["vol_ratio"] = as_float(signal.get("vol_ratio"), default=1.0)
        merged["fvg_confirmed"] = as_bool(signal.get("fvg_confirmed"), default=False)
        merged["regime"] = str(signal.get("regime", "")).upper()
        joined.append(merged)

    stats = {
        "signals": len(signals),
        "labels": len(seen_labels),
        "joined_counted": len(joined),
        "time_excluded": time_labels,
        "unmatched_labels": unmatched_labels,
        "duplicate_signals": duplicate_signals,
        "duplicate_labels": duplicate_labels,
    }
    return joined, stats


def metrics(rows: list[dict], baseline_n: int) -> str:
    n = len(rows)
    wins = sum(1 for row in rows if row["outcome"] in WIN_OUTCOMES)
    sl = sum(1 for row in rows if row["outcome"] in LOSS_OUTCOMES)
    wr = wins / n * 100 if n else 0.0
    return f"n={n} (было {baseline_n})  WR={wr:.1f}%  SL={sl}"


def run_filter(name: str, rows: list[dict], keep: Callable[[dict], bool], baseline_n: int) -> str:
    filtered = [row for row in rows if keep(row)]
    return f"{name}: {metrics(filtered, baseline_n)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Test main signal filters on labeled outcomes.")
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--details", action="store_true", help="Print join/exclusion details.")
    args = parser.parse_args()

    rows, stats = load_joined(args.signals, args.labels)
    baseline_n = len(rows)

    if args.details:
        print(
            "details: "
            f"signals={stats['signals']} labels={stats['labels']} "
            f"joined_counted={stats['joined_counted']} time_excluded={stats['time_excluded']} "
            f"unmatched_labels={stats['unmatched_labels']} "
            f"duplicate_signals={stats['duplicate_signals']} duplicate_labels={stats['duplicate_labels']}"
        )

    print(run_filter("Baseline", rows, lambda row: True, baseline_n))
    print(
        run_filter(
            "Filter A",
            rows,
            lambda row: not (row["regime"] == "TRENDING" and row["vol_ratio"] < 1.5),
            baseline_n,
        )
    )
    print(run_filter("Filter B", rows, lambda row: not row["fvg_confirmed"], baseline_n))
    print(
        run_filter(
            "Filter C",
            rows,
            lambda row: (
                not (row["regime"] == "TRENDING" and row["vol_ratio"] < 1.5)
                and not row["fvg_confirmed"]
            ),
            baseline_n,
        )
    )
    print(run_filter("Filter D", rows, lambda row: row["vol_ratio"] > 2.5, baseline_n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
