from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "scripts" / "backtest" / "results"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = [float(row["pnl_pct"]) for row in rows if float(row.get("pnl_pct", 0.0)) > 0]
    losses = [float(row["pnl_pct"]) for row in rows if float(row.get("pnl_pct", 0.0)) < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "signals": len(rows),
        "wr": len(wins) / len(rows) * 100.0 if rows else 0.0,
        "pf": gross_win / gross_loss if gross_loss else (99.0 if gross_win else 0.0),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
    }


def group_stats(rows: list[dict[str, Any]], key_fn) -> list[tuple[str, dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(row)
    return [(key, stats(items)) for key, items in groups.items()]


def fmt_pf(value: float) -> str:
    return "inf" if value >= 99 else f"{value:.2f}"


def main() -> None:
    files = sorted(RESULTS_DIR.glob("screener_run_*_*.jsonl"))
    run_rows: list[tuple[int, str, str, list[dict[str, Any]], dict[str, Any]]] = []
    for path in files:
        rows = load_jsonl(path)
        if not rows:
            continue
        first = rows[0]
        run_id = int(first["run_id"])
        params = str(first["params_type"])
        tf = str(first["tf_signal"])
        run_rows.append((run_id, params, tf, rows, stats(rows)))

    if not run_rows:
        print(f"No screener result files found in {RESULTS_DIR}")
        return

    run_rows.sort(key=lambda item: item[0])
    print("RUN  PARAMS        TF   SIGNALS  WR%    PF    AVG_WIN  AVG_LOSS")
    for run_id, params, tf, _rows, st in run_rows:
        print(
            f"{run_id:<4} {params:<12} {tf:<4} {st['signals']:>7}  "
            f"{st['wr']:>5.1f}%  {fmt_pf(st['pf']):>5}  "
            f"{st['avg_win']:>+7.2f}%  {st['avg_loss']:>+8.2f}%"
        )

    best = max(run_rows, key=lambda item: (item[4]["pf"], item[4]["signals"]))
    best_id, best_params, best_tf, best_rows, best_stats = best
    print()
    print(f"Best run: {best_id} ({best_params} {best_tf}) PF={fmt_pf(best_stats['pf'])}, signals={best_stats['signals']}")
    print()
    print("By Regime/Style")
    for label, st in sorted(group_stats(best_rows, lambda row: f"{row.get('regime')} {row.get('trade_style')}")):
        print(f"{label:<16} n={st['signals']:<4} WR={st['wr']:.1f}% PF={fmt_pf(st['pf'])}")

    pair_rows = group_stats(best_rows, lambda row: str(row.get("symbol")))
    pair_rows.sort(key=lambda item: (item[1]["pf"], item[1]["signals"]), reverse=True)

    print()
    print("Top 5 Pairs")
    for symbol, st in pair_rows[:5]:
        print(f"{symbol:<14} n={st['signals']:<4} WR={st['wr']:.1f}% PF={fmt_pf(st['pf'])}")

    print()
    print("Worst 5 Pairs")
    for symbol, st in sorted(pair_rows, key=lambda item: (item[1]["pf"], item[1]["signals"]))[:5]:
        print(f"{symbol:<14} n={st['signals']:<4} WR={st['wr']:.1f}% PF={fmt_pf(st['pf'])}")


if __name__ == "__main__":
    main()
