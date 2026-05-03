"""
Equity simulation for the best WS pump backtest configuration.
"""

from __future__ import annotations

import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bt_pump_core import DEFAULT_CACHE_DIR, detect_signals
from bt_pump_filters import FILTER_SIGNALS_PATH, FilterSpec, apply_filter_spec



BEST_VOL_MULT = 2.0
BEST_PRICE_PCT = 2.0
BEST_LOOKBACK = 15
INITIAL_BALANCE = 100.0
POSITION_FRACTION = 0.10
ROUND_TRIP_COST_PCT = 0.10
MAX_CONCURRENT_POSITIONS = 2
SKIP_AFTER_3_STOPS = 5
TP_MULTIPLIER = 1.5
REPORT_PATH = DEFAULT_CACHE_DIR / "pump_bt_report.txt"
EQUITY_SUMMARY_PATH = DEFAULT_CACHE_DIR / "equity_summary.csv"
FILTER_EQUITY_PATH = DEFAULT_CACHE_DIR / "equity_filter_suite.csv"
TAPE_SIGNAL_PATH = DEFAULT_CACHE_DIR / "phase2_signal_table_tape.pkl"


@dataclass(slots=True)
class CandidateTrade:
    sym: str
    signal_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    direction: str
    outcome: str
    entry_price: float
    exit_price: float
    target_pct: float
    sl_pct: float
    gross_return_pct: float
    leverage: int


@dataclass(slots=True)
class SimResult:
    label: str
    time_exit_min: int
    final_balance: float
    pnl_pct: float
    max_drawdown_pct: float
    profit_factor: float
    win_rate_pct: float
    time_exit_rate_pct: float
    tp_rate_pct: float
    sl_rate_pct: float
    n_trades: int


@dataclass(slots=True)
class OpenPosition:
    exit_ts: pd.Timestamp
    pnl: float
    outcome: str


def load_frames(cache_dir: Path = DEFAULT_CACHE_DIR) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for path in sorted(cache_dir.glob("*_1m_30d.pkl")):
        symbol = path.name.replace("_1m_30d.pkl", "")
        df = pd.read_pickle(path).sort_index()
        df.attrs["symbol"] = symbol
        frames[symbol] = df
    return frames


def compute_24h_vol_pct(df: pd.DataFrame, idx: int) -> float:
    start = max(0, idx - 1440)
    window = df.iloc[start:idx]
    if window.empty:
        return 0.0
    low = float(window["low"].min())
    high = float(window["high"].max())
    if low <= 0:
        return 0.0
    return (high - low) / low * 100.0


def compute_leverage(vol_24h_pct: float) -> int:
    if vol_24h_pct <= 0:
        return 1
    max_allowed = math.floor(100.0 / (vol_24h_pct * 2.0))
    return max(1, min(max_allowed, 3))


def load_phase2_signal_tables() -> tuple[pd.DataFrame, pd.DataFrame | None]:
    if not FILTER_SIGNALS_PATH.exists():
        raise FileNotFoundError(f"Missing {FILTER_SIGNALS_PATH}; run bt_pump_filters.py --save-signals first.")
    base = pd.read_pickle(FILTER_SIGNALS_PATH)
    tape = pd.read_pickle(TAPE_SIGNAL_PATH) if TAPE_SIGNAL_PATH.exists() else None
    return base, tape


def build_candidates(frames: dict[str, pd.DataFrame], time_exit_min: int) -> list[CandidateTrade]:
    candidates: list[CandidateTrade] = []
    target_pct = BEST_PRICE_PCT * TP_MULTIPLIER
    sl_pct = BEST_PRICE_PCT

    for symbol, df in frames.items():
        opens = df["open"].to_numpy(dtype="float64")
        highs = df["high"].to_numpy(dtype="float64")
        lows = df["low"].to_numpy(dtype="float64")
        closes = df["close"].to_numpy(dtype="float64")
        timestamps = df.index.to_list()

        signals = detect_signals(
            df=df,
            vol_mult=BEST_VOL_MULT,
            price_pct=BEST_PRICE_PCT,
            lookback=BEST_LOOKBACK,
            sym=symbol,
        )

        for signal in signals:
            idx = signal.candle_idx
            if idx is None or idx + 1 >= len(df):
                continue

            entry_idx = idx + 1
            entry_ts = timestamps[entry_idx]
            entry_price = float(opens[entry_idx])
            if entry_price <= 0:
                continue

            direction_sign = 1.0 if signal.direction == "PUMP" else -1.0
            if signal.direction == "PUMP":
                tp_price = entry_price * (1.0 + target_pct / 100.0)
                sl_price = entry_price * (1.0 - sl_pct / 100.0)
            else:
                tp_price = entry_price * (1.0 - target_pct / 100.0)
                sl_price = entry_price * (1.0 + sl_pct / 100.0)

            end_idx = min(len(df) - 1, entry_idx + time_exit_min - 1)
            outcome = "TIME"
            exit_price = float(closes[end_idx])
            exit_ts = timestamps[end_idx]

            for candle_idx in range(entry_idx, end_idx + 1):
                high = float(highs[candle_idx])
                low = float(lows[candle_idx])
                close = float(closes[candle_idx])
                ts = timestamps[candle_idx]

                if signal.direction == "PUMP":
                    hit_sl = low <= sl_price
                    hit_tp = high >= tp_price
                    if hit_sl and hit_tp:
                        outcome = "SL"
                        exit_price = sl_price
                        exit_ts = ts
                        break
                    if hit_sl:
                        outcome = "SL"
                        exit_price = sl_price
                        exit_ts = ts
                        break
                    if hit_tp:
                        outcome = "TP"
                        exit_price = tp_price
                        exit_ts = ts
                        break
                else:
                    hit_sl = high >= sl_price
                    hit_tp = low <= tp_price
                    if hit_sl and hit_tp:
                        outcome = "SL"
                        exit_price = sl_price
                        exit_ts = ts
                        break
                    if hit_sl:
                        outcome = "SL"
                        exit_price = sl_price
                        exit_ts = ts
                        break
                    if hit_tp:
                        outcome = "TP"
                        exit_price = tp_price
                        exit_ts = ts
                        break

                if candle_idx == end_idx:
                    outcome = "TIME"
                    exit_price = close
                    exit_ts = ts

            gross_return_pct = direction_sign * (exit_price - entry_price) / entry_price * 100.0
            leverage = compute_leverage(compute_24h_vol_pct(df, idx))

            candidates.append(
                CandidateTrade(
                    sym=symbol,
                    signal_ts=signal.ts,
                    entry_ts=entry_ts,
                    exit_ts=exit_ts,
                    direction=signal.direction,
                    outcome=outcome,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    target_pct=target_pct,
                    sl_pct=sl_pct,
                    gross_return_pct=gross_return_pct,
                    leverage=leverage,
                )
            )

    candidates.sort(key=lambda trade: (trade.entry_ts, trade.sym))
    return candidates


def build_candidates_from_signal_rows(
    frames: dict[str, pd.DataFrame],
    signal_rows: pd.DataFrame,
    time_exit_min: int,
) -> list[CandidateTrade]:
    candidates: list[CandidateTrade] = []
    target_pct = BEST_PRICE_PCT * TP_MULTIPLIER
    sl_pct = BEST_PRICE_PCT

    for row in signal_rows.itertuples(index=False):
        df = frames[row.sym]
        idx = int(row.signal_idx)
        if idx + 1 >= len(df):
            continue

        opens = df["open"].to_numpy(dtype="float64")
        highs = df["high"].to_numpy(dtype="float64")
        lows = df["low"].to_numpy(dtype="float64")
        closes = df["close"].to_numpy(dtype="float64")
        timestamps = df.index.to_list()

        entry_idx = idx + 1
        entry_ts = timestamps[entry_idx]
        entry_price = float(opens[entry_idx])
        if entry_price <= 0:
            continue

        direction_sign = 1.0 if row.direction == "PUMP" else -1.0
        if row.direction == "PUMP":
            tp_price = entry_price * (1.0 + target_pct / 100.0)
            sl_price = entry_price * (1.0 - sl_pct / 100.0)
        else:
            tp_price = entry_price * (1.0 - target_pct / 100.0)
            sl_price = entry_price * (1.0 + sl_pct / 100.0)

        end_idx = min(len(df) - 1, entry_idx + time_exit_min - 1)
        outcome = "TIME"
        exit_price = float(closes[end_idx])
        exit_ts = timestamps[end_idx]

        for candle_idx in range(entry_idx, end_idx + 1):
            high = float(highs[candle_idx])
            low = float(lows[candle_idx])
            close = float(closes[candle_idx])
            ts = timestamps[candle_idx]

            if row.direction == "PUMP":
                hit_sl = low <= sl_price
                hit_tp = high >= tp_price
                if hit_sl and hit_tp:
                    outcome = "SL"
                    exit_price = sl_price
                    exit_ts = ts
                    break
                if hit_sl:
                    outcome = "SL"
                    exit_price = sl_price
                    exit_ts = ts
                    break
                if hit_tp:
                    outcome = "TP"
                    exit_price = tp_price
                    exit_ts = ts
                    break
            else:
                hit_sl = high >= sl_price
                hit_tp = low <= tp_price
                if hit_sl and hit_tp:
                    outcome = "SL"
                    exit_price = sl_price
                    exit_ts = ts
                    break
                if hit_sl:
                    outcome = "SL"
                    exit_price = sl_price
                    exit_ts = ts
                    break
                if hit_tp:
                    outcome = "TP"
                    exit_price = tp_price
                    exit_ts = ts
                    break

            if candle_idx == end_idx:
                outcome = "TIME"
                exit_price = close
                exit_ts = ts

        gross_return_pct = direction_sign * (exit_price - entry_price) / entry_price * 100.0
        leverage = compute_leverage(compute_24h_vol_pct(df, idx))
        candidates.append(
            CandidateTrade(
                sym=row.sym,
                signal_ts=row.ts,
                entry_ts=entry_ts,
                exit_ts=exit_ts,
                direction=row.direction,
                outcome=outcome,
                entry_price=entry_price,
                exit_price=exit_price,
                target_pct=target_pct,
                sl_pct=sl_pct,
                gross_return_pct=gross_return_pct,
                leverage=leverage,
            )
        )

    candidates.sort(key=lambda trade: (trade.entry_ts, trade.sym))
    return candidates


def max_drawdown_pct(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    max_dd = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            max_dd = max(max_dd, dd)
    return max_dd


def run_simulation(candidates: list[CandidateTrade], time_exit_min: int, label: str) -> SimResult:
    balance = INITIAL_BALANCE
    equity_curve = [balance]
    open_positions: list[OpenPosition] = []
    profits: list[float] = []
    losses: list[float] = []
    stop_streak = 0
    skip_signals = 0
    counts = {"TP": 0, "SL": 0, "TIME": 0}
    last_progress_day = None

    def settle_positions(until_ts: pd.Timestamp | None) -> None:
        nonlocal balance, stop_streak, skip_signals, last_progress_day, open_positions

        still_open: list[OpenPosition] = []
        to_settle = sorted(
            [position for position in open_positions if until_ts is None or position.exit_ts <= until_ts],
            key=lambda position: position.exit_ts,
        )
        if until_ts is not None:
            still_open = [position for position in open_positions if position.exit_ts > until_ts]

        for position in to_settle:
            balance += position.pnl
            equity_curve.append(balance)
            counts[position.outcome] += 1
            if position.pnl >= 0:
                profits.append(position.pnl)
            else:
                losses.append(abs(position.pnl))

            if position.outcome == "SL":
                stop_streak += 1
                if stop_streak >= 3:
                    skip_signals = SKIP_AFTER_3_STOPS
                    stop_streak = 0
            else:
                stop_streak = 0

            trade_day = position.exit_ts.floor("D")
            if last_progress_day is None:
                last_progress_day = trade_day
            elif (trade_day - last_progress_day).days >= 5:
                print(
                    f"[TIME_EXIT={time_exit_min}m] {trade_day.strftime('%Y-%m-%d')} "
                    f"balance={balance:.2f} trades={sum(counts.values())}"
                )
                last_progress_day = trade_day

        open_positions = still_open if until_ts is not None else []

    for trade in candidates:
        settle_positions(trade.entry_ts)

        if skip_signals > 0:
            skip_signals -= 1
            continue

        if len(open_positions) >= MAX_CONCURRENT_POSITIONS:
            continue

        margin = balance * POSITION_FRACTION
        net_return_pct = trade.gross_return_pct - ROUND_TRIP_COST_PCT
        pnl = margin * trade.leverage * net_return_pct / 100.0
        open_positions.append(OpenPosition(exit_ts=trade.exit_ts, pnl=pnl, outcome=trade.outcome))

    settle_positions(None)

    total_trades = sum(counts.values())
    profit_factor = sum(profits) / sum(losses) if losses else float("inf")
    win_base = counts["TP"] + counts["SL"]
    win_rate = counts["TP"] / win_base * 100.0 if win_base else 0.0
    time_rate = counts["TIME"] / total_trades * 100.0 if total_trades else 0.0

    return SimResult(
        label=label,
        time_exit_min=time_exit_min,
        final_balance=balance,
        pnl_pct=(balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100.0,
        max_drawdown_pct=max_drawdown_pct(equity_curve),
        profit_factor=profit_factor,
        win_rate_pct=win_rate,
        time_exit_rate_pct=time_rate,
        tp_rate_pct=counts["TP"] / total_trades * 100.0 if total_trades else 0.0,
        sl_rate_pct=counts["SL"] / total_trades * 100.0 if total_trades else 0.0,
        n_trades=total_trades,
    )


def run_filter_suite(frames: dict[str, pd.DataFrame], base_signals: pd.DataFrame, tape_signals: pd.DataFrame | None) -> pd.DataFrame:
    specs: list[tuple[str, pd.DataFrame]] = [
        ("BASE_30M", base_signals),
        ("A+B", apply_filter_spec(base_signals, FilterSpec("A+B", require_pump=True, require_confirm=True))),
        ("A+B+C50k", apply_filter_spec(base_signals, FilterSpec("A+B+C50k", require_pump=True, require_confirm=True, liquidity_threshold=50_000.0))),
        ("A+B+C25k+D[22]", apply_filter_spec(base_signals, FilterSpec("A+B+C25k+D[22]", require_pump=True, require_confirm=True, liquidity_threshold=25_000.0, allowed_hours=[22]))),
        ("A+B+C50k+D[18,19,22]+E", apply_filter_spec(base_signals, FilterSpec("A+B+C50k+D[18,19,22]+E", require_pump=True, require_confirm=True, liquidity_threshold=50_000.0, allowed_hours=[18, 19, 22], require_sustain=True))),
    ]

    if tape_signals is not None:
        specs.append(
            (
                "A+B+C50k+D[18,19,22]+E+F_cluster",
                apply_filter_spec(
                    tape_signals,
                    FilterSpec(
                        "A+B+C50k+D[18,19,22]+E+F_cluster",
                        require_pump=True,
                        require_confirm=True,
                        liquidity_threshold=50_000.0,
                        allowed_hours=[18, 19, 22],
                        require_sustain=True,
                        require_tape_cluster=True,
                    ),
                ),
            )
        )

    records = []
    for label, signal_rows in specs:
        result = run_simulation(
            build_candidates_from_signal_rows(frames, signal_rows, time_exit_min=30),
            time_exit_min=30,
            label=label,
        )
        record = asdict(result)
        record["signals_in_set"] = int(len(signal_rows))
        records.append(record)
        print(
            f"{label:30s} n={record['n_trades']:4d} set={record['signals_in_set']:4d} "
            f"pnl={record['pnl_pct']:+7.2f}% pf={record['profit_factor']:.3f} "
            f"mdd={record['max_drawdown_pct']:.2f}% wr={record['win_rate_pct']:.2f}% "
            f"tp/sl/time={record['tp_rate_pct']:.2f}/{record['sl_rate_pct']:.2f}/{record['time_exit_rate_pct']:.2f}"
        )
    suite_df = pd.DataFrame(records)
    suite_df.to_csv(FILTER_EQUITY_PATH, index=False)
    return suite_df


def main() -> None:
    frames = load_frames()
    base_signals, tape_signals = load_phase2_signal_tables()
    results: list[SimResult] = []

    for time_exit_min in (5, 15, 30):
        print(f"\n=== Running equity sim with TIME_EXIT={time_exit_min}m ===")
        candidates = build_candidates(frames, time_exit_min=time_exit_min)
        print(f"candidate_trades={len(candidates)}")
        result = run_simulation(candidates, time_exit_min=time_exit_min, label="BASE")
        results.append(result)
        print(
            f"TIME_EXIT={time_exit_min}m  final_balance=${result.final_balance:.2f}  "
            f"pnl={result.pnl_pct:+.2f}%  max_dd={result.max_drawdown_pct:.2f}%  "
            f"pf={result.profit_factor:.3f}  wr={result.win_rate_pct:.2f}%  "
            f"time_rate={result.time_exit_rate_pct:.2f}%  TP/SL/TIME="
            f"{result.tp_rate_pct:.2f}%/{result.sl_rate_pct:.2f}%/{result.time_exit_rate_pct:.2f}%  "
            f"trades={result.n_trades}"
        )

    print("\n=== Equity Summary ===")
    summary_df = pd.DataFrame([asdict(result) for result in results])
    summary_df.to_csv(EQUITY_SUMMARY_PATH, index=False)
    print(summary_df.to_string(index=False))

    print("\n=== Filter Stack Equity (TIME_EXIT=30m) ===")
    filter_suite_df = run_filter_suite(frames, base_signals, tape_signals)
    print(filter_suite_df.to_string(index=False))

    best_result = max(results, key=lambda result: result.final_balance)
    equity_lines = [
        "",
        "EQUITY SIM:",
        f"Best TIME_EXIT: {best_result.time_exit_min}m",
        f"Final balance: ${best_result.final_balance:.2f}  ({best_result.pnl_pct:+.2f}%)",
        f"Win Rate: {best_result.win_rate_pct:.2f}%  |  PF: {best_result.profit_factor:.3f}  |  Max DD: {best_result.max_drawdown_pct:.2f}%",
        f"TP/SL/TIME: {best_result.tp_rate_pct:.2f}%/{best_result.sl_rate_pct:.2f}%/{best_result.time_exit_rate_pct:.2f}%",
        "",
        "EQUITY TIME_EXIT COMPARISON:",
        summary_df.to_string(index=False),
        "",
        "FILTER STACK EQUITY (TIME_EXIT=30m):",
        filter_suite_df.to_string(index=False),
    ]
    print(f"\nSaved equity summary -> {EQUITY_SUMMARY_PATH}")
    print(f"Saved filter suite   -> {FILTER_EQUITY_PATH}")

    if REPORT_PATH.exists():
        report_text = REPORT_PATH.read_text(encoding="utf-8")
        report_text = report_text.rstrip() + "\n\n" + "\n".join(equity_lines) + "\n"
        REPORT_PATH.write_text(report_text, encoding="utf-8")
        print(f"Updated report -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
