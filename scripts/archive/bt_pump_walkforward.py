"""
Walk-forward validation for the Phase 2 pump filter stack.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bt_pump_equity import build_candidates_from_signal_rows, load_frames, run_simulation
from bt_pump_filters import (
    FILTER_RESULTS_PATH,
    FILTER_SIGNALS_PATH,
    FilterSpec,
    apply_filter_spec,
    build_filter_specs,
    mean,
    precision,
    safe_rr,
)


REPORT_PATH = Path(__file__).resolve().parent / "cache" / "pump_walkforward_report.txt"
TRAIN_START = pd.Timestamp("2026-04-03T00:00:00Z")
SPLIT_TS = pd.Timestamp("2026-04-23T00:00:00Z")
TEST_END = pd.Timestamp("2026-05-03T23:59:59Z")
TRAIN_DAYS = 20
TEST_DAYS = 10
TIME_EXIT_MIN = 30


def load_signal_table() -> pd.DataFrame:
    if not FILTER_SIGNALS_PATH.exists():
        raise FileNotFoundError(f"Missing {FILTER_SIGNALS_PATH}; run bt_pump_filters.py --save-signals first.")
    df = pd.read_pickle(FILTER_SIGNALS_PATH).copy()
    return df.sort_values(["ts", "sym"]).reset_index(drop=True)


def split_windows(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    train_df = df[(df["ts"] >= TRAIN_START) & (df["ts"] < SPLIT_TS)].copy()
    test_df = df[(df["ts"] >= SPLIT_TS) & (df["ts"] <= TEST_END)].copy()
    return train_df, test_df


def evaluate_subset_days(df: pd.DataFrame, name: str, days: int) -> dict[str, object]:
    return {
        "filters": name,
        "n": int(len(df)),
        "signals_per_day": float(len(df) / days),
        "precision_10m": precision(df["ret_10m"]),
        "avg_ret_10m": mean(df["ret_10m"]),
        "avg_r_r": safe_rr(df),
    }


def search_best_on_train(train_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    rows = []
    for spec in build_filter_specs(train_df):
        subset = apply_filter_spec(train_df, spec)
        rows.append(evaluate_subset_days(subset, spec.name, TRAIN_DAYS))
    results = pd.DataFrame(rows).drop_duplicates(subset=["filters"])
    results = results.sort_values(["precision_10m", "avg_ret_10m", "signals_per_day"], ascending=[False, False, False])

    target = results[
        (results["n"] >= TRAIN_DAYS) &
        (results["signals_per_day"] >= 1.0) &
        (results["precision_10m"] >= 60.0) &
        (results["avg_ret_10m"] > 0.5)
    ].copy()
    selected = target.iloc[0] if not target.empty else results.iloc[0]
    return results, selected


def evaluate_stack(
    frames: dict[str, pd.DataFrame],
    signal_df: pd.DataFrame,
    label: str,
) -> dict[str, object]:
    equity = run_simulation(
        build_candidates_from_signal_rows(frames, signal_df, time_exit_min=TIME_EXIT_MIN),
        time_exit_min=TIME_EXIT_MIN,
        label=label,
    )
    return {
        "label": label,
        "signals_n": int(len(signal_df)),
        "signals_per_day": float(len(signal_df) / TEST_DAYS),
        "precision_10m": precision(signal_df["ret_10m"]),
        "avg_ret_10m": mean(signal_df["ret_10m"]),
        "avg_r_r": safe_rr(signal_df),
        "equity_pf": float(equity.profit_factor),
        "equity_wr": float(equity.win_rate_pct),
        "equity_max_dd": float(equity.max_drawdown_pct),
        "equity_pnl_pct": float(equity.pnl_pct),
        "equity_n_trades": int(equity.n_trades),
        "equity_time_exit_pct": float(equity.time_exit_rate_pct),
        "equity_tp_pct": float(equity.tp_rate_pct),
        "equity_sl_pct": float(equity.sl_rate_pct),
    }


def full_period_winners() -> tuple[str, str]:
    full_df = pd.read_csv(FILTER_RESULTS_PATH)
    target = full_df[
        (full_df["n"] >= 30) &
        (full_df["signals_per_day"] >= 1.0) &
        (full_df["precision_10m"] >= 60.0) &
        (full_df["avg_ret_10m"] > 0.5)
    ].copy()
    best_filter = str(target.iloc[0]["filters"]) if not target.empty else str(full_df.iloc[0]["filters"])
    robust_equity_stack = "A+B+C50k+D[18,19,22]+E"
    return best_filter, robust_equity_stack


def parse_hours_from_name(name: str) -> list[int]:
    if "D[" not in name:
        return []
    raw = name.split("D[", 1)[1].split("]", 1)[0].strip()
    if not raw:
        return []
    return [int(part) for part in raw.split(",") if part]


def format_eval(eval_row: dict[str, object]) -> str:
    return (
        f"{eval_row['label']}: signals={eval_row['signals_n']} ({eval_row['signals_per_day']:.2f}/day)  "
        f"prec10={eval_row['precision_10m']:.2f}%  avg_ret10={eval_row['avg_ret_10m']:.4f}%  "
        f"PF={eval_row['equity_pf']:.3f}  WR={eval_row['equity_wr']:.2f}%  "
        f"MaxDD={eval_row['equity_max_dd']:.2f}%  pnl={eval_row['equity_pnl_pct']:+.2f}%  "
        f"trades={eval_row['equity_n_trades']}  TIME/TP/SL="
        f"{eval_row['equity_time_exit_pct']:.2f}%/{eval_row['equity_tp_pct']:.2f}%/{eval_row['equity_sl_pct']:.2f}%"
    )


def main() -> None:
    frames = load_frames()
    signal_df = load_signal_table()
    train_df, test_df = split_windows(signal_df)
    train_results, selected = search_best_on_train(train_df)
    selected_name = str(selected["filters"])

    full_best_filter, full_best_equity = full_period_winners()

    selected_spec = next(spec for spec in build_filter_specs(train_df) if spec.name == selected_name)
    test_selected = apply_filter_spec(test_df, selected_spec)
    test_ab = apply_filter_spec(test_df, FilterSpec(name="A+B", require_pump=True, require_confirm=True))
    test_full_equity_stack = apply_filter_spec(
        test_df,
        FilterSpec(
            name="A+B+C50k+D[18,19,22]+E",
            require_pump=True,
            require_confirm=True,
            liquidity_threshold=50_000.0,
            allowed_hours=[18, 19, 22],
            require_sustain=True,
        ),
    )

    selected_eval = evaluate_stack(frames, test_selected, f"TRAIN_SELECTED::{selected_name}")
    ab_eval = evaluate_stack(frames, test_ab, "A+B")
    robust_eval = evaluate_stack(frames, test_full_equity_stack, "A+B+C50k+D[18,19,22]+E")

    overfit_flag = selected_eval["equity_pf"] < 1.0
    stable_flag = selected_eval["equity_pf"] >= 1.5

    report_lines = [
        "=== PUMP WALK-FORWARD REPORT ===",
        f"TRAIN: {TRAIN_START.strftime('%Y-%m-%d %H:%M:%S UTC')} .. {(SPLIT_TS - pd.Timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S UTC')} ({TRAIN_DAYS} days)",
        f"TEST:  {SPLIT_TS.strftime('%Y-%m-%d %H:%M:%S UTC')} .. {TEST_END.strftime('%Y-%m-%d %H:%M:%S UTC')} ({TEST_DAYS} days)",
        f"TIME_EXIT for equity comparison: {TIME_EXIT_MIN}m",
        "",
        "TRAIN SELECTION:",
        f"Selected stack on TRAIN: {selected_name}",
        f"TRAIN metrics: n={int(selected['n'])}  signals/day={selected['signals_per_day']:.2f}  "
        f"precision_10m={selected['precision_10m']:.2f}%  avg_ret_10m={selected['avg_ret_10m']:.4f}%  avg_r_r={selected['avg_r_r']:.3f}",
        f"Full-period best filter stack: {full_best_filter}",
        f"Full-period best robust equity stack: {full_best_equity}",
        f"Same as full-period best filter? {'YES' if selected_name == full_best_filter else 'NO'}",
        f"TRAIN hours in D: {parse_hours_from_name(selected_name) or 'none'}",
        f"Full-period hours in D: {parse_hours_from_name(full_best_filter) or 'none'}",
        "",
        "Top TRAIN candidates:",
        train_results[
            (train_results['n'] >= TRAIN_DAYS) &
            (train_results['signals_per_day'] >= 1.0)
        ][["filters", "n", "signals_per_day", "precision_10m", "avg_ret_10m", "avg_r_r"]].head(10).to_string(index=False),
        "",
        "TEST RESULTS:",
        format_eval(selected_eval),
        format_eval(ab_eval),
        format_eval(robust_eval),
        "",
        "JUDGMENT:",
        f"Selected TRAIN stack PF on TEST = {selected_eval['equity_pf']:.3f} -> {'STABLE (>=1.5)' if stable_flag else 'OVERFIT RISK' if overfit_flag else 'MARGINAL'}",
        f"A+B PF on TEST = {ab_eval['equity_pf']:.3f}",
        f"A+B+C+D+E PF on TEST = {robust_eval['equity_pf']:.3f}",
        (
            "Out-of-sample winner: A+B"
            if ab_eval["equity_pf"] > robust_eval["equity_pf"] and ab_eval["equity_pf"] >= selected_eval["equity_pf"]
            else "Out-of-sample winner: A+B+C+D+E"
            if robust_eval["equity_pf"] >= ab_eval["equity_pf"] and robust_eval["equity_pf"] >= selected_eval["equity_pf"]
            else "Out-of-sample winner: TRAIN-selected stack"
        ),
        "",
        "CONCLUSION:",
        (
            "Engine build should stay paused: selected train stack failed out-of-sample PF >= 1.0."
            if overfit_flag else
            "Engine build is defensible from a walk-forward standpoint: selected train stack kept PF >= 1.5 on TEST."
            if stable_flag else
            "Engine build is still premature: walk-forward did not fail outright, but PF on TEST stayed below 1.5."
        ),
    ]

    report_text = "\n".join(report_lines)
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    print(report_text)
    print(f"\nSaved walk-forward report -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
