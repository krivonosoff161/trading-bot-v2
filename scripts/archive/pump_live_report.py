"""Analyze paper-trading results from pump_signals.jsonl + pump_labels.jsonl."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SIGNALS_LOG = PROJECT_ROOT / "logs" / "pump" / "pump_signals.jsonl"
LABELS_LOG = PROJECT_ROOT / "logs" / "pump" / "pump_labels.jsonl"
REPORT_DIR = PROJECT_ROOT / "logs" / "pump"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build_report_text(signals: list[dict], labels: list[dict]) -> str:
    lines: list[str] = []
    if not labels:
        return "Нет закрытых сделок в pump_labels.jsonl\n"

    ldf = pd.DataFrame(labels)
    n = len(ldf)
    wins = int((ldf["net_pnl_pct"] > 0).sum())
    wr = wins / n * 100.0
    total = float(ldf["net_pnl_pct"].sum())
    avg = float(ldf["net_pnl_pct"].mean())
    gains = ldf[ldf["net_pnl_pct"] > 0]["net_pnl_pct"]
    losses = ldf[ldf["net_pnl_pct"] < 0]["net_pnl_pct"].abs()
    pf = float(gains.sum() / losses.sum()) if not losses.empty else float("inf")
    avg_hold = float(ldf["hold_min"].mean())

    tp_n = int((ldf["exit_reason"] == "TP").sum()) if "exit_reason" in ldf.columns else 0
    sl_n = int((ldf["exit_reason"] == "SL").sum()) if "exit_reason" in ldf.columns else 0
    time_n = int((ldf["exit_reason"] == "TIME").sum()) if "exit_reason" in ldf.columns else 0

    lines.append("=" * 50)
    lines.append("PUMP ENGINE - PAPER TRADING REPORT")
    lines.append(f"Period: {ldf['closed_at'].min()[:10]} - {ldf['closed_at'].max()[:10]}")
    lines.append("=" * 50)
    lines.append(f"Signals:        {len([r for r in signals if r.get('type') == 'ENTRY'])}")
    lines.append(f"Trades:         {n}")
    lines.append(f"WR:             {wr:.1f}%  (TP: {tp_n}, SL: {sl_n}, TIME: {time_n})")
    lines.append(f"Net PF:         {pf:.2f}")
    lines.append(f"Total net P&L:  {total:+.2f}%")
    lines.append(f"Avg net/trade:  {avg:+.3f}%")
    lines.append(f"Avg hold:       {avg_hold:.1f}m")

    lines.append("\n--- By Symbol ---")
    by_sym = (
        ldf.groupby("sym")
        .agg(
            n=("net_pnl_pct", "count"),
            wr=("net_pnl_pct", lambda x: (x > 0).mean() * 100),
            total=("net_pnl_pct", "sum"),
            avg=("net_pnl_pct", "mean"),
        )
        .round(2)
        .sort_values("total", ascending=False)
    )
    lines.append(by_sym.to_string())

    lines.append("\n--- By Exit Reason ---")
    by_exit = (
        ldf.groupby("exit_reason")
        .agg(
            n=("net_pnl_pct", "count"),
            avg=("net_pnl_pct", "mean"),
            total=("net_pnl_pct", "sum"),
        )
        .round(3)
    )
    lines.append(by_exit.to_string())

    ldf_sorted = ldf.sort_values("closed_at").copy()
    ldf_sorted["equity_pct"] = ldf_sorted["net_pnl_pct"].cumsum()
    peak = ldf_sorted["equity_pct"].cummax()
    max_dd = float((ldf_sorted["equity_pct"] - peak).min())
    lines.append(f"\nMax drawdown:   {max_dd:.2f}%")
    lines.append(f"Final equity:   {ldf_sorted['equity_pct'].iloc[-1]:+.2f}%")
    return "\n".join(lines) + "\n"


def main() -> None:
    signals = [row for row in load_jsonl(SIGNALS_LOG) if row.get("type") == "ENTRY"]
    labels = [row for row in load_jsonl(LABELS_LOG) if row.get("type") == "EXIT"]

    report = build_report_text(signals, labels)
    print(report, end="")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.utcnow().strftime("%Y%m%d_%H%M")
    out = REPORT_DIR / f"pump_report_{today}.txt"
    out.write_text(report, encoding="utf-8")
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
