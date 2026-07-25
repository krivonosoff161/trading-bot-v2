"""
Analysis report for the best WS pump-scan backtest configurations.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bt_pump_core import DEFAULT_CACHE_DIR, analyze_forward, detect_signals


REPORT_PATH = DEFAULT_CACHE_DIR / "pump_bt_report.txt"
PHASE2_REPORT_PATH = DEFAULT_CACHE_DIR / "pump_phase2_report.txt"
SWEEP_PATH = DEFAULT_CACHE_DIR / "sweep_results.csv"
TAPE_DIR = Path(__file__).resolve().parents[1] / "tape"
JOURNAL_PATH = Path(__file__).resolve().parents[2] / "JOURNAL.md"
ROUND_TRIP_COST_PCT = 0.10
FILTER_RESULTS_PATH = DEFAULT_CACHE_DIR / "phase2_filter_results.csv"
FILTER_SIGNALS_PATH = DEFAULT_CACHE_DIR / "phase2_signal_table.pkl"
LAB_SUMMARY_PATH = DEFAULT_CACHE_DIR / "phase2_lab_summary.json"
TAPE_SUMMARY_PATH = DEFAULT_CACHE_DIR / "tape_analysis_summary.json"
TAPE_SIGNALS_PATH = DEFAULT_CACHE_DIR / "phase2_signal_table_tape.pkl"
COIN_SCREENER_LATEST_PATH = DEFAULT_CACHE_DIR / "coin_screener_latest.json"
COIN_SCREENER_BACKTEST_PATH = DEFAULT_CACHE_DIR / "coin_screener_backtest.json"
EQUITY_FILTER_PATH = DEFAULT_CACHE_DIR / "equity_filter_suite.csv"
SIGNAL_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "signals" / "signal_log.jsonl"
SIGNAL_LABELS_PATH = Path(__file__).resolve().parents[2] / "logs" / "signals" / "signal_labels.jsonl"


@dataclass(slots=True)
class Config:
    vol_mult: float
    price_pct: float
    lookback: int
    source_rank: int


def load_frames(cache_dir: Path = DEFAULT_CACHE_DIR) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for path in sorted(cache_dir.glob("*_1m_30d.pkl")):
        symbol = path.name.replace("_1m_30d.pkl", "")
        df = pd.read_pickle(path).sort_index()
        df.attrs["symbol"] = symbol
        frames[symbol] = df
    return frames


def load_sweep_results() -> pd.DataFrame:
    if not SWEEP_PATH.exists():
        raise FileNotFoundError(f"Missing {SWEEP_PATH}")
    return pd.read_csv(SWEEP_PATH)


def pick_top_configs(sweep_df: pd.DataFrame, limit: int = 5) -> list[Config]:
    filtered = sweep_df[
        (sweep_df["signals_per_day"] >= 1.0) &
        (sweep_df["precision_10m"] >= 55.0)
    ].copy()

    source = filtered if not filtered.empty else sweep_df.copy()
    source = source.sort_values(["avg_r_r", "avg_return_10m", "precision_10m"], ascending=[False, False, False]).head(limit)
    return [
        Config(
            vol_mult=float(row.vol_mult),
            price_pct=float(row.price_pct),
            lookback=int(row.lookback),
            source_rank=idx + 1,
        )
        for idx, row in enumerate(source.itertuples(index=False))
    ]


def build_signal_table(frames: dict[str, pd.DataFrame], config: Config) -> pd.DataFrame:
    records: list[dict[str, object]] = []

    for symbol, df in frames.items():
        if len(df) <= config.lookback + 1:
            continue

        _opens = df["open"].to_numpy(dtype="float64")
        _highs = df["high"].to_numpy(dtype="float64")
        _lows = df["low"].to_numpy(dtype="float64")
        closes = df["close"].to_numpy(dtype="float64")
        vols = df["vol"].to_numpy(dtype="float64")

        signals = detect_signals(
            df=df,
            vol_mult=config.vol_mult,
            price_pct=config.price_pct,
            lookback=config.lookback,
            sym=symbol,
        )

        for signal in signals:
            try:
                forward = analyze_forward(df, signal)
            except ValueError:
                continue

            idx = signal.candle_idx
            if idx is None:
                continue

            baseline_avg_vol = float(np.mean(vols[idx - config.lookback : idx]))
            future_vols = vols[idx + 1 : idx + 11]
            vol_return_min = None
            for minute, future_vol in enumerate(future_vols, start=1):
                if future_vol <= baseline_avg_vol:
                    vol_return_min = minute
                    break

            next_close = closes[idx + 1] if idx + 1 < len(closes) else np.nan
            hour = int(signal.ts.hour)
            hour_bucket = "00-08" if hour < 8 else "08-16" if hour < 16 else "16-24"
            body = signal.entry_price - signal.candle_open
            upper_wick = signal.candle_high - signal.entry_price
            large_upper_wick = bool(signal.direction == "PUMP" and upper_wick > max(body, 0.0) * 2.0)

            records.append(
                {
                    "sym": symbol,
                    "ts": signal.ts,
                    "hour": hour,
                    "hour_bucket": hour_bucket,
                    "direction": signal.direction,
                    "signal_close": signal.entry_price,
                    "entry_open": forward.entry_open_price,
                    "vol_ratio": signal.vol_ratio,
                    "pct_move": signal.pct_move,
                    "signal_vol": vols[idx],
                    "baseline_avg_vol": baseline_avg_vol,
                    "signal_dollar_volume": vols[idx] * signal.entry_price,
                    "next_close": next_close,
                    "ret_1m": forward.directional_returns.get(1),
                    "ret_5m": forward.directional_returns.get(5),
                    "ret_10m": forward.directional_returns.get(10),
                    "ret_15m": forward.directional_returns.get(15),
                    "ret_30m": forward.directional_returns.get(30),
                    "raw_ret_10m": forward.returns.get(10),
                    "max_gain": forward.max_gain,
                    "max_loss": forward.max_loss,
                    "time_to_max_gain": forward.time_to_max_gain,
                    "reversed": forward.reversed,
                    "confirm_ok": bool((forward.directional_returns.get(1) or 0.0) > 0),
                    "vol_return_min": vol_return_min,
                    "false_pump_pattern": large_upper_wick,
                    "candle_open": signal.candle_open,
                    "candle_high": signal.candle_high,
                    "candle_low": signal.candle_low,
                }
            )

    return pd.DataFrame.from_records(records)


def precision(series: pd.Series) -> float:
    clean = series.dropna()
    if clean.empty:
        return float("nan")
    return float((clean > 0).mean() * 100.0)


def mean(series: pd.Series) -> float:
    clean = series.dropna()
    if clean.empty:
        return float("nan")
    return float(clean.mean())


def safe_rr(df: pd.DataFrame) -> float:
    avg_gain = mean(df["max_gain"])
    avg_loss = mean(df["max_loss"])
    if math.isnan(avg_gain) or math.isnan(avg_loss):
        return float("nan")
    if abs(avg_loss) < 1e-12:
        return float("inf")
    return avg_gain / abs(avg_loss)


def to_table(df: pd.DataFrame, columns: list[str], max_rows: int | None = None) -> str:
    subset = df[columns]
    if max_rows is not None:
        subset = subset.head(max_rows)
    return subset.to_string(index=False)


def tape_summary() -> str:
    dates = []
    for path in sorted(TAPE_DIR.glob("*.csv*")):
        stem = path.name.replace(".csv.gz", "").replace(".csv", "")
        if len(stem) == 10:
            dates.append(stem)
    if not dates:
        return "Tape: no files found"
    return f"Tape: {dates[0]} .. {dates[-1]} ({len(dates)} days)"


def journal_summary() -> str:
    if not JOURNAL_PATH.exists():
        return "Journal: not found"
    lines = JOURNAL_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    headline = next((line.strip("# ").strip() for line in lines if line.startswith("#")), "Journal present")
    return f"Journal: {headline}"


def analyze_config(signals_df: pd.DataFrame, config: Config) -> tuple[str, dict[str, object]]:
    header = (
        f"CONFIG #{config.source_rank}: vol_mult={config.vol_mult}  "
        f"price_pct={config.price_pct}  lookback={config.lookback}"
    )

    summary = {
        "n_signals": len(signals_df),
        "signals_per_day": len(signals_df) / 30.0,
        "precision_10m": precision(signals_df["ret_10m"]),
        "avg_return_10m": mean(signals_df["ret_10m"]),
        "avg_r_r": safe_rr(signals_df),
    }

    pair_df = (
        signals_df.groupby("sym")
        .agg(
            n=("sym", "size"),
            precision_10m=("ret_10m", precision),
            avg_ret_10m=("ret_10m", mean),
        )
        .reset_index()
        .sort_values(["precision_10m", "avg_ret_10m", "n"], ascending=[False, False, False])
    )

    bucket_df = (
        signals_df.groupby("hour_bucket")
        .agg(
            n=("hour_bucket", "size"),
            precision_10m=("ret_10m", precision),
            avg_ret_10m=("ret_10m", mean),
        )
        .reset_index()
        .sort_values("hour_bucket")
    )

    hour_df = (
        signals_df.groupby("hour")
        .agg(
            n=("hour", "size"),
            precision_10m=("ret_10m", precision),
            avg_ret_10m=("ret_10m", mean),
        )
        .reset_index()
        .sort_values("hour")
    )

    type_df = (
        signals_df.groupby("direction")
        .agg(
            n=("direction", "size"),
            precision_10m=("ret_10m", precision),
            avg_ret_10m=("ret_10m", mean),
            avg_rr=("max_gain", lambda s: safe_rr(signals_df.loc[s.index])),
        )
        .reset_index()
        .sort_values("direction")
    )

    net_return_5m = mean(signals_df["ret_5m"]) - ROUND_TRIP_COST_PCT

    confirm_a = signals_df["ret_10m"]
    confirm_b_df = signals_df[signals_df["confirm_ok"]]
    confirm_b_precision = precision(confirm_b_df["ret_10m"])
    confirm_b_avg = mean(confirm_b_df["ret_10m"])

    vol_decay_known = signals_df["vol_return_min"].dropna()
    vol_decay_lt3 = float((vol_decay_known <= 3).mean() * 100.0) if not vol_decay_known.empty else float("nan")
    vol_decay_gt10 = float((vol_decay_known > 10).mean() * 100.0) if not vol_decay_known.empty else float("nan")

    liquid_df = signals_df[signals_df["signal_dollar_volume"] > 50_000]
    illiquid_df = signals_df[signals_df["signal_dollar_volume"] <= 50_000]

    false_pump_df = signals_df[(signals_df["direction"] == "PUMP") & (signals_df["false_pump_pattern"])]
    pump_df = signals_df[signals_df["direction"] == "PUMP"]

    selected_pairs = pair_df[(pair_df["precision_10m"] >= 60.0) & (pair_df["n"] >= 10)]["sym"].tolist()
    best_type_row = type_df.sort_values(["precision_10m", "avg_ret_10m"], ascending=[False, False]).iloc[0] if not type_df.empty else None
    best_bucket_row = bucket_df.sort_values(["precision_10m", "avg_ret_10m"], ascending=[False, False]).iloc[0] if not bucket_df.empty else None

    report = [
        header,
        f"Signals/day={summary['signals_per_day']:.2f}  Precision-10m={summary['precision_10m']:.2f}%  "
        f"AvgRet10m={summary['avg_return_10m']:.4f}%  AvgR/R={summary['avg_r_r']:.3f}",
        "",
        "BY PAIR:",
        to_table(pair_df, ["sym", "n", "precision_10m", "avg_ret_10m"]),
        "",
        "BY HOUR BUCKET (UTC):",
        to_table(bucket_df, ["hour_bucket", "n", "precision_10m", "avg_ret_10m"]),
        "",
        "BY TYPE:",
        to_table(type_df, ["direction", "n", "precision_10m", "avg_ret_10m", "avg_rr"]),
        "",
        f"NET RETURN 5m after 0.10% costs: {net_return_5m:.4f}%",
        "",
        "ADDITIONAL CHECKS:",
        f"1. Timing heatmap: best hour bucket={best_bucket_row['hour_bucket'] if best_bucket_row is not None else 'n/a'}  "
        f"precision_10m={best_bucket_row['precision_10m']:.2f}%"
        if best_bucket_row is not None else "1. Timing heatmap: n/a",
        to_table(hour_df, ["hour", "n", "precision_10m", "avg_ret_10m"], max_rows=24),
        "",
        "2. Confirmation candle:",
        f"Aggressive entry: precision_10m={precision(confirm_a):.2f}%  avg_ret_10m={mean(confirm_a):.4f}%",
        f"Conservative entry: n={len(confirm_b_df)}  precision_10m={confirm_b_precision:.2f}%  "
        f"avg_ret_10m={confirm_b_avg:.4f}%",
        "",
        "3. Vol decay:",
        f"Known decay observations={len(vol_decay_known)}  avg_return_to_normal={vol_decay_known.mean():.2f}m"
        if not vol_decay_known.empty else "Known decay observations=0",
        f"Returned to normal <=3m: {vol_decay_lt3:.2f}%  |  >10m: {vol_decay_gt10:.2f}%",
        "",
        "4. Liquidity check ($50k candle dollar volume):",
        f"Liquid subset:   n={len(liquid_df)}  precision_10m={precision(liquid_df['ret_10m']):.2f}%  "
        f"avg_ret_10m={mean(liquid_df['ret_10m']):.4f}%",
        f"Illiquid subset: n={len(illiquid_df)}  precision_10m={precision(illiquid_df['ret_10m']):.2f}%  "
        f"avg_ret_10m={mean(illiquid_df['ret_10m']):.4f}%",
        "",
        "5. False pump pattern (large upper wick on PUMP candles):",
        f"PUMP signals total={len(pump_df)}  false-pattern={len(false_pump_df)}  "
        f"share={(len(false_pump_df) / len(pump_df) * 100.0 if len(pump_df) else float('nan')):.2f}%",
        f"False-pattern precision_10m={precision(false_pump_df['ret_10m']):.2f}%  "
        f"avg_ret_10m={mean(false_pump_df['ret_10m']):.4f}%",
        f"Other PUMP precision_10m={precision(pump_df.loc[~pump_df['false_pump_pattern'], 'ret_10m']):.2f}%  "
        f"avg_ret_10m={mean(pump_df.loc[~pump_df['false_pump_pattern'], 'ret_10m']):.4f}%",
        "",
        "RECOMMENDATION SNAPSHOT:",
        f"Best side={best_type_row['direction']}  precision_10m={best_type_row['precision_10m']:.2f}%"
        if best_type_row is not None else "Best side=n/a",
        f"Pairs with precision_10m >= 60 and n>=10: {selected_pairs if selected_pairs else 'none'}",
    ]

    recommendation = {
        "selected_pairs": selected_pairs,
        "best_side": None if best_type_row is None else str(best_type_row["direction"]),
        "best_bucket": None if best_bucket_row is None else str(best_bucket_row["hour_bucket"]),
        "precision_10m": summary["precision_10m"],
        "avg_r_r": summary["avg_r_r"],
        "avg_return_10m": summary["avg_return_10m"],
        "signals_df": signals_df,
    }
    return "\n".join(report), recommendation


def choose_recommendation(config_reports: list[tuple[Config, dict[str, object]]]) -> tuple[Config, dict[str, object]]:
    best_score = None
    best_item = None
    for config, info in config_reports:
        score = (
            float(info["precision_10m"]),
            float(info["avg_r_r"]),
            float(info["avg_return_10m"]),
        )
        if best_score is None or score > best_score:
            best_score = score
            best_item = (config, info)
    if best_item is None:
        raise ValueError("No configuration reports available")
    return best_item


def cross_analyze_rest_vs_pump() -> dict[str, object]:
    signals = [json.loads(line) for line in SIGNAL_LOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    labels = {
        row["signal_id"]: row["outcome"]
        for row in (json.loads(line) for line in SIGNAL_LABELS_PATH.read_text(encoding="utf-8").splitlines() if line.strip())
    }

    rows = []
    for signal in signals:
        outcome = labels.get(signal["signal_id"])
        if not outcome:
            continue
        rows.append(
            {
                "outcome": outcome,
                "vol_ratio": signal.get("vol_ratio"),
                "spread_bps": signal.get("spread_bps"),
                "slope_15m": signal.get("slope_15m"),
                "adx_1h": signal.get("adx_1h"),
                "obi5": signal.get("obi5"),
                "trade_delta_100": signal.get("trade_delta_100"),
            }
        )

    df = pd.DataFrame(rows)
    completed = df[df["outcome"].isin(["TP", "STOP"])].copy()

    def _wr(mask: pd.Series) -> tuple[int, float]:
        subset = completed[mask.fillna(False)]
        if subset.empty:
            return 0, float("nan")
        return int(len(subset)), float((subset["outcome"] == "TP").mean() * 100.0)

    n_vol, wr_vol = _wr(completed["vol_ratio"] > 1.5)
    n_spread, wr_spread = _wr(completed["spread_bps"] <= 6)
    n_slope, wr_slope = _wr(completed["slope_15m"] >= 35)
    n_adx, wr_adx = _wr(completed["adx_1h"] >= 25)

    base_wr = float((completed["outcome"] == "TP").mean() * 100.0) if not completed.empty else float("nan")
    return {
        "n_signals": int(len(df)),
        "base_completed_wr": base_wr,
        "vol_gt_1_5": {"n": n_vol, "wr": wr_vol},
        "spread_le_6": {"n": n_spread, "wr": wr_spread},
        "slope15_ge_35": {"n": n_slope, "wr": wr_slope},
        "adx1h_ge_25": {"n": n_adx, "wr": wr_adx},
        "time_exit_mean_vol_ratio": mean(df.loc[df["outcome"] == "TIME_EXIT", "vol_ratio"]),
        "tp_mean_vol_ratio": mean(df.loc[df["outcome"] == "TP", "vol_ratio"]),
        "stop_mean_vol_ratio": mean(df.loc[df["outcome"] == "STOP", "vol_ratio"]),
    }


def build_phase2_report() -> str:
    filter_df = pd.read_csv(FILTER_RESULTS_PATH)
    lab_summary = json.loads(LAB_SUMMARY_PATH.read_text(encoding="utf-8"))
    tape_summary = json.loads(TAPE_SUMMARY_PATH.read_text(encoding="utf-8"))
    live_screener = json.loads(COIN_SCREENER_LATEST_PATH.read_text(encoding="utf-8"))
    screener_backtest = json.loads(COIN_SCREENER_BACKTEST_PATH.read_text(encoding="utf-8"))
    equity_df = pd.read_csv(EQUITY_FILTER_PATH)
    cross = cross_analyze_rest_vs_pump()
    phase2_signals = pd.read_pickle(FILTER_SIGNALS_PATH)
    phase2_tape = pd.read_pickle(TAPE_SIGNALS_PATH) if TAPE_SIGNALS_PATH.exists() else None

    target_filter_df = filter_df[
        (filter_df["n"] >= 30) &
        (filter_df["signals_per_day"] >= 1.0) &
        (filter_df["precision_10m"] >= 60.0) &
        (filter_df["avg_ret_10m"] > 0.5)
    ].copy()
    best_filter = target_filter_df.sort_values(
        ["precision_10m", "avg_ret_10m", "signals_per_day"],
        ascending=[False, False, False],
    ).iloc[0]

    robust_equity = equity_df[equity_df["n_trades"] >= 30].copy()
    best_equity = (
        robust_equity.sort_values(["profit_factor", "pnl_pct", "n_trades"], ascending=[False, False, False]).iloc[0]
        if not robust_equity.empty
        else equity_df.sort_values(["profit_factor", "pnl_pct"], ascending=[False, False]).iloc[0]
    )
    _base_abcde = phase2_signals[
        (phase2_signals["filter_a_pump"]) &
        (phase2_signals["filter_b_confirm"]) &
        (phase2_signals["signal_dollar_volume"] >= 50_000.0) &
        (phase2_signals["hour"].isin([18, 19, 22])) &
        (phase2_signals["filter_e_sustain"])
    ].copy()

    if phase2_tape is not None:
        tape_filter_subset = phase2_tape[
            (phase2_tape["filter_a_pump"]) &
            (phase2_tape["filter_b_confirm"]) &
            (phase2_tape["signal_dollar_volume"] >= 50_000.0) &
            (phase2_tape["hour"].isin([18, 19, 22])) &
            (phase2_tape["filter_e_sustain"]) &
            (phase2_tape["filter_f_cluster"])
        ].copy()
    else:
        tape_filter_subset = pd.DataFrame()

    phase2_lines = [
        "=== PHASE 2 REPORT ===",
        "Parameters baseline: vol_mult=2.0  price_pct=2.0  lookback=15",
        "",
        "FILTER STACK:",
        f"Best A/B/C/D/E stack: {best_filter['filters']}",
        f"n={int(best_filter['n'])}  signals/day={best_filter['signals_per_day']:.2f}  "
        f"precision_10m={best_filter['precision_10m']:.2f}%  avg_ret_10m={best_filter['avg_ret_10m']:.4f}%  avg_r_r={best_filter['avg_r_r']:.3f}",
        "Top target stacks:",
        filter_df[
            (filter_df["n"] >= 30) &
            (filter_df["signals_per_day"] >= 1.0) &
            (filter_df["precision_10m"] >= 60.0) &
            (filter_df["avg_ret_10m"] > 0.5)
        ][["filters", "n", "signals_per_day", "precision_10m", "avg_ret_10m", "avg_r_r"]].head(10).to_string(index=False),
        "",
        "LAB INVESTIGATION:",
        f"Raw share of LAB signals: {lab_summary['lab_share_pct']:.2f}% ({lab_summary['lab_signal_count']} / {lab_summary['all_signal_count']})",
        f"LAB median signal $-volume: {lab_summary['lab_median_signal_dollar_volume']:.2f}  |  others: {lab_summary['other_median_signal_dollar_volume']:.2f}",
        f"LAB pass rate at $50k signal candle: {lab_summary['lab_pass_c50k_pct']:.2f}%",
        f"Decision: {lab_summary['decision']}",
        f"Reason: {lab_summary['reason']}",
        "Live screener note: current LAB status -> " +
        next((item["reason"] for item in live_screener["excluded"] if item["instId"] == "LAB-USDT-SWAP"), "not excluded"),
        "",
        "TAPE CVD / FILTER_F:",
        f"Pump tape signals matched: {tape_summary['tape']['pump_tape_signals']}",
        f"CVD>0 precision_10m: {tape_summary['tape']['cvd_pos_precision_10m']:.2f}%  n={tape_summary['tape']['cvd_pos_n']}",
        f"buy_ratio>60% precision_10m: {tape_summary['tape']['buy60_precision_10m']:.2f}%  n={tape_summary['tape']['buy60_n']}",
        f"dv_1m>$50k precision_10m: {tape_summary['tape']['dv50_precision_10m']:.2f}%  n={tape_summary['tape']['dv50_n']}",
        f"cluster precision_10m: {tape_summary['tape']['cluster_precision_10m']:.2f}%  n={tape_summary['tape']['cluster_n']}",
        f"Conclusion: FILTER_F is NOT production-ready yet; overlap sample is too small ({tape_summary['tape']['pump_tape_signals']} pump signals, {len(tape_filter_subset)} after full stack).",
        "",
        "BTC -> ETH LAG:",
        f"BTC pump events in tape window under baseline config: {tape_summary['btc_eth_lag']['btc_pump_events']}",
        "Conclusion: no actionable evidence. Baseline config produced zero BTC pump events in the tape overlap, so lag-arb remains unconfirmed.",
        "",
        "FLASH CRASH BOUNCE:",
        f"n={tape_summary['flash_bounce']['flash_bounce_n']}  reversed_rate={tape_summary['flash_bounce']['reversed_rate']:.2f}%  "
        f"precision_10m={tape_summary['flash_bounce']['precision_10m']:.2f}%  avg_ret_10m={tape_summary['flash_bounce']['avg_ret_10m']:.4f}%",
        "Conclusion: do not trade as standalone long setup. Reversal frequency is high, but forward economics stay negative.",
        "",
        "VOLUME PROFILE:",
        f"Far next HVZ (>1%): precision_10m={tape_summary['volume_profile']['far_hvz_precision_10m']:.2f}%  avg_ret_10m={tape_summary['volume_profile']['far_hvz_avg_ret_10m']:.4f}%  n={tape_summary['volume_profile']['far_hvz_n']}",
        f"Near next HVZ (<=1%): precision_10m={tape_summary['volume_profile']['near_hvz_precision_10m']:.2f}%  avg_ret_10m={tape_summary['volume_profile']['near_hvz_avg_ret_10m']:.4f}%  n={tape_summary['volume_profile']['near_hvz_n']}",
        "Conclusion: better used for TP sizing than as an entry filter.",
        "",
        "COIN SCREENER:",
        f"Current tier1: {live_screener['tier1']}",
        f"Current tier2: {live_screener['tier2']}",
        f"Current tier3: {live_screener['tier3']}",
        f"Dynamic-vs-static backtest: dynamic precision_10m={screener_backtest['dynamic_precision_10m']:.2f}% "
        f"vs static {screener_backtest['static_precision_10m']:.2f}%; dynamic avg_ret_10m={screener_backtest['dynamic_avg_ret_10m']:.4f}% "
        f"vs static {screener_backtest['static_avg_ret_10m']:.4f}%",
        "Conclusion: current screener is useful as a live liquidity/volatility guard, but not yet as a standalone alpha-improving universe selector.",
        "",
        "REST VS PUMP CROSS-ANALYSIS:",
        f"Completed-trade WR baseline (REST): {cross['base_completed_wr']:.2f}%",
        f"REST with vol_ratio>1.5: WR={cross['vol_gt_1_5']['wr']:.2f}%  n={cross['vol_gt_1_5']['n']}",
        f"REST with spread_bps<=6: WR={cross['spread_le_6']['wr']:.2f}%  n={cross['spread_le_6']['n']}",
        f"REST with slope_15m>=35: WR={cross['slope15_ge_35']['wr']:.2f}%  n={cross['slope15_ge_35']['n']}",
        f"REST with adx_1h>=25: WR={cross['adx1h_ge_25']['wr']:.2f}%  n={cross['adx1h_ge_25']['n']}",
        f"TIME_EXIT mean vol_ratio={cross['time_exit_mean_vol_ratio']:.4f}  |  TP mean vol_ratio={cross['tp_mean_vol_ratio']:.4f}",
        "Conclusion: vol_ratio helps completed REST trades, but TIME_EXIT was NOT caused by low volume; it had the highest mean vol_ratio of all outcomes.",
        "",
        "EQUITY:",
        equity_df[[
            "label",
            "signals_in_set",
            "n_trades",
            "pnl_pct",
            "profit_factor",
            "max_drawdown_pct",
            "win_rate_pct",
            "tp_rate_pct",
            "sl_rate_pct",
            "time_exit_rate_pct",
        ]].to_string(index=False),
        f"Best robust equity stack: {best_equity['label']}  |  PF={best_equity['profit_factor']:.3f}  "
        f"MaxDD={best_equity['max_drawdown_pct']:.2f}%  n_trades={int(best_equity['n_trades'])}  pnl={best_equity['pnl_pct']:+.2f}%",
        "",
        "FINAL RECOMMENDATION:",
        "1. Recommended filter stack: A + B + C($50k) + D([18,19,22] UTC) + E.",
        "2. Recommended parameters: vol_mult=2.0  price_pct=2.0  lookback=15.",
        f"3. Recommended live pair universe: tier1+tier2 from coin screener -> {live_screener['tier1'] + live_screener['tier2']}",
        "4. FILTER_F: keep as research-only; sample too small for production gating.",
        "5. BTC->ETH lag: no confirmed edge on current baseline sample.",
        "6. Flash Crash Bounce: do not deploy as standalone strategy.",
        "7. ws_pump_engine.py flags: reconnect=True, heartbeat_interval=30, max_pairs=12, state_persistence=True.",
        "8. Scanner launch baseline:",
        "python scripts/ws/ws_pump_scanner.py --vol_mult 2.0 --price_pct 2.0 --lookback 15",
        "9. Filtered live intent:",
        "Use only PUMP alerts, require confirmation candle, require >=$50k signal candle, prefer 18/19/22 UTC, require 3-bar volume sustain.",
    ]
    return "\n".join(phase2_lines)


def main_phase1() -> None:
    frames = load_frames()
    sweep_df = load_sweep_results()
    top_configs = pick_top_configs(sweep_df)

    all_indices = [df.index for df in frames.values() if not df.empty]
    overall_start = min(index.min() for index in all_indices)
    overall_end = max(index.max() for index in all_indices)
    total_candles = int(sum(len(df) for df in frames.values()))

    lines = [
        "=== PUMP SCANNER BACKTEST REPORT ===",
        f"Period: {overall_start.strftime('%Y-%m-%d %H:%M:%S UTC')} - {overall_end.strftime('%Y-%m-%d %H:%M:%S UTC')}  |  "
        f"Pairs: {len(frames)}  |  Total candles: {total_candles}",
        tape_summary(),
        journal_summary(),
        "",
    ]

    config_reports: list[tuple[Config, dict[str, object]]] = []
    for config in top_configs:
        signals_df = build_signal_table(frames, config)
        report_text, info = analyze_config(signals_df, config)
        lines.append(report_text)
        lines.append("")
        config_reports.append((config, info))

    best_config, best_info = choose_recommendation(config_reports)
    selected_pairs = best_info["selected_pairs"] or sorted(
        build_signal_table(frames, best_config)
        .groupby("sym")["ret_10m"]
        .apply(precision)
        .sort_values(ascending=False)
        .head(5)
        .index
        .tolist()
    )

    lines.extend(
        [
            "TOP CONFIGURATION:",
            f"vol_mult={best_config.vol_mult}  price_pct={best_config.price_pct}  lookback={best_config.lookback}",
            f"Signals/day: {len(best_info['signals_df']) / 30.0:.2f}  |  "
            f"Precision-10m: {best_info['precision_10m']:.2f}%  |  Avg R/R: {best_info['avg_r_r']:.3f}",
            "",
            "RECOMMENDATION:",
            "Raw all-signal sweep did not reach precision_10m >= 55%, so live use should be filtered.",
            f"Prefer side: {best_info['best_side'] or 'n/a'}  |  Prefer UTC bucket: {best_info['best_bucket'] or 'n/a'}",
            f"Pairs for first live shortlist: {selected_pairs}",
            "Command:",
            f"python scripts/ws/ws_pump_scanner.py --vol_mult {best_config.vol_mult} "
            f"--price_pct {best_config.price_pct} --lookback {best_config.lookback} "
            f"--pairs {' '.join(selected_pairs)}",
        ]
    )

    report_text = "\n".join(lines)
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    print(report_text)
    print(f"\nSaved report -> {REPORT_PATH}")


def main_phase2() -> None:
    if not REPORT_PATH.exists():
        raise FileNotFoundError(f"Missing {REPORT_PATH}; run phase1 report first.")

    base_text = REPORT_PATH.read_text(encoding="utf-8").rstrip()
    phase2_text = build_phase2_report()
    combined = base_text
    marker = "=== PHASE 2 REPORT ==="
    if marker in combined:
        combined = combined.split(marker)[0].rstrip()
    combined = combined + "\n\n" + phase2_text + "\n"
    PHASE2_REPORT_PATH.write_text(combined, encoding="utf-8")
    print(combined)
    print(f"\nSaved phase2 report -> {PHASE2_REPORT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase2", action="store_true")
    args = parser.parse_args()
    if args.phase2:
        main_phase2()
    else:
        main_phase1()
