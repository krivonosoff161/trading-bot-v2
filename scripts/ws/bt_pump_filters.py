"""
Phase 2 filter-stack research for the OKX WS pump scanner.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import aiohttp
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bt_pump_core import DEFAULT_CACHE_DIR, analyze_forward, detect_signals


BASE_VOL_MULT = 2.0
BASE_PRICE_PCT = 2.0
BASE_LOOKBACK = 15
BASE_DAYS = 30
PHASE1_CORE_HOURS = [1, 2, 4, 5, 22]
LIQUIDITY_THRESHOLDS = [10_000.0, 25_000.0, 50_000.0, 100_000.0]
FILTER_RESULTS_PATH = DEFAULT_CACHE_DIR / "phase2_filter_results.csv"
FILTER_SIGNALS_PATH = DEFAULT_CACHE_DIR / "phase2_signal_table.pkl"
LAB_RESULTS_PATH = DEFAULT_CACHE_DIR / "phase2_lab_summary.json"
CTVALS_CACHE_PATH = DEFAULT_CACHE_DIR / "ctvals_latest.json"


@dataclass(slots=True)
class FilterSpec:
    name: str
    require_pump: bool = False
    require_confirm: bool = False
    liquidity_threshold: float | None = None
    allowed_hours: list[int] | None = None
    require_sustain: bool = False
    require_tape_cluster: bool = False
    require_tape_confirm: bool = False
    require_btc_eth_lag: bool = False


def load_frames(cache_dir: Path = DEFAULT_CACHE_DIR) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for path in sorted(cache_dir.glob("*_1m_30d.pkl")):
        symbol = path.name.replace("_1m_30d.pkl", "")
        df = pd.read_pickle(path).sort_index()
        df.attrs["symbol"] = symbol
        frames[symbol] = df
    if not frames:
        raise FileNotFoundError(f"No 30d pair caches found in {cache_dir}")
    return frames


def fetch_ctvals(cache_path: Path = CTVALS_CACHE_PATH) -> dict[str, float]:
    url = "https://www.okx.com/api/v5/public/instruments?" + urlencode({"instType": "SWAP"})
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX instruments failed: code={payload.get('code')} msg={payload.get('msg', '')}")
        ctvals = {
            row["instId"]: float(row["ctVal"])
            for row in payload.get("data", [])
            if row.get("instId", "").endswith("-USDT-SWAP")
        }
        cache_path.write_text(json.dumps(ctvals, indent=2, sort_keys=True), encoding="utf-8")
        return ctvals
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError):
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        raise


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
    if df.empty:
        return float("nan")
    avg_gain = mean(df["max_gain"])
    avg_loss = mean(df["max_loss"])
    if math.isnan(avg_gain) or math.isnan(avg_loss):
        return float("nan")
    if abs(avg_loss) < 1e-12:
        return float("inf")
    return avg_gain / abs(avg_loss)


def build_signal_table(
    frames: dict[str, pd.DataFrame],
    ctvals: dict[str, float],
    vol_mult: float = BASE_VOL_MULT,
    price_pct: float = BASE_PRICE_PCT,
    lookback: int = BASE_LOOKBACK,
) -> pd.DataFrame:
    records: list[dict[str, object]] = []

    for symbol, df in frames.items():
        if len(df) <= lookback + 4:
            continue

        opens = df["open"].to_numpy(dtype="float64")
        highs = df["high"].to_numpy(dtype="float64")
        lows = df["low"].to_numpy(dtype="float64")
        closes = df["close"].to_numpy(dtype="float64")
        vols = df["vol"].to_numpy(dtype="float64")
        timestamps = df.index.to_list()
        ct_val = ctvals.get(symbol, 1.0)

        signals = detect_signals(
            df=df,
            vol_mult=vol_mult,
            price_pct=price_pct,
            lookback=lookback,
            sym=symbol,
        )

        for signal in signals:
            idx = signal.candle_idx
            if idx is None or idx + 1 >= len(df):
                continue

            try:
                forward = analyze_forward(df, signal)
            except ValueError:
                continue

            avg_vol = float(np.mean(vols[idx - lookback : idx]))
            mean_vol_next_3 = float(np.mean(vols[idx + 1 : idx + 4])) if idx + 4 <= len(df) else float("nan")
            confirm_close = float(closes[idx + 1]) if idx + 1 < len(df) else float("nan")
            signal_dollar_volume = float(closes[idx] * vols[idx] * ct_val)
            signal_body = float(closes[idx] - opens[idx])
            upper_wick = float(highs[idx] - closes[idx])

            records.append(
                {
                    "sym": symbol,
                    "ts": timestamps[idx],
                    "hour": int(timestamps[idx].hour),
                    "direction": signal.direction,
                    "signal_idx": idx,
                    "signal_open": float(opens[idx]),
                    "signal_high": float(highs[idx]),
                    "signal_low": float(lows[idx]),
                    "signal_close": float(closes[idx]),
                    "signal_vol": float(vols[idx]),
                    "avg_vol": avg_vol,
                    "vol_ratio": float(signal.vol_ratio),
                    "pct_move": float(signal.pct_move),
                    "ct_val": float(ct_val),
                    "signal_dollar_volume": signal_dollar_volume,
                    "entry_open": float(forward.entry_open_price),
                    "ret_5m": forward.directional_returns.get(5),
                    "ret_10m": forward.directional_returns.get(10),
                    "ret_15m": forward.directional_returns.get(15),
                    "ret_30m": forward.directional_returns.get(30),
                    "raw_ret_10m": forward.returns.get(10),
                    "max_gain": float(forward.max_gain),
                    "max_loss": float(forward.max_loss),
                    "reversed": bool(forward.reversed),
                    "time_to_max_gain": forward.time_to_max_gain,
                    "filter_a_pump": bool(signal.direction == "PUMP"),
                    "filter_b_confirm": bool(signal.direction == "PUMP" and confirm_close > opens[idx]),
                    "filter_e_sustain": bool(not math.isnan(mean_vol_next_3) and mean_vol_next_3 > avg_vol * 0.8),
                    "mean_vol_next_3": mean_vol_next_3,
                    "false_pump_pattern": bool(signal.direction == "PUMP" and upper_wick > max(signal_body, 0.0) * 2.0),
                    "day": timestamps[idx].strftime("%Y-%m-%d"),
                }
            )

    signals_df = pd.DataFrame.from_records(records).sort_values(["ts", "sym"]).reset_index(drop=True)
    return signals_df


def apply_filter_spec(signals_df: pd.DataFrame, spec: FilterSpec) -> pd.DataFrame:
    df = signals_df.copy()
    if spec.require_pump:
        df = df[df["filter_a_pump"]]
    if spec.require_confirm:
        df = df[df["filter_b_confirm"]]
    if spec.liquidity_threshold is not None:
        df = df[df["signal_dollar_volume"] >= spec.liquidity_threshold]
    if spec.allowed_hours is not None:
        df = df[df["hour"].isin(spec.allowed_hours)]
    if spec.require_sustain:
        df = df[df["filter_e_sustain"]]
    if spec.require_tape_cluster and "filter_f_cluster" in df.columns:
        df = df[df["filter_f_cluster"]]
    if spec.require_tape_confirm and "filter_f_tape_confirm" in df.columns:
        df = df[df["filter_f_tape_confirm"]]
    if spec.require_btc_eth_lag and "filter_g_btc_eth_lag" in df.columns:
        df = df[df["filter_g_btc_eth_lag"]]
    return df.copy()


def evaluate_subset(df: pd.DataFrame, name: str) -> dict[str, object]:
    return {
        "filters": name,
        "n": int(len(df)),
        "signals_per_day": float(len(df) / BASE_DAYS),
        "precision_10m": precision(df["ret_10m"]),
        "avg_ret_10m": mean(df["ret_10m"]),
        "avg_ret_5m": mean(df["ret_5m"]),
        "avg_r_r": safe_rr(df),
    }


def derive_hour_pool(signals_df: pd.DataFrame, liquidity_threshold: float = 25_000.0) -> list[int]:
    seed = apply_filter_spec(
        signals_df,
        FilterSpec(
            name="A+B+C25k_seed",
            require_pump=True,
            require_confirm=True,
            liquidity_threshold=liquidity_threshold,
        ),
    )
    if seed.empty:
        return sorted(set(PHASE1_CORE_HOURS))

    stats = (
        seed.groupby("hour")
        .agg(
            n=("hour", "size"),
            precision_10m=("ret_10m", precision),
            avg_ret_10m=("ret_10m", mean),
        )
        .reset_index()
    )
    selected = stats[
        (stats["n"] >= 15) &
        ((stats["precision_10m"] >= 50.0) | (stats["avg_ret_10m"] > 0.25))
    ]["hour"].tolist()
    pool = sorted(set(PHASE1_CORE_HOURS).union(selected))
    return pool


def build_filter_specs(signals_df: pd.DataFrame) -> list[FilterSpec]:
    specs: list[FilterSpec] = [
        FilterSpec(name="BASE"),
        FilterSpec(name="A", require_pump=True),
        FilterSpec(name="A+B", require_pump=True, require_confirm=True),
    ]

    for threshold in LIQUIDITY_THRESHOLDS:
        specs.append(
            FilterSpec(
                name=f"A+B+C{int(threshold/1000)}k",
                require_pump=True,
                require_confirm=True,
                liquidity_threshold=threshold,
            )
        )
        specs.append(
            FilterSpec(
                name=f"A+B+C{int(threshold/1000)}k+E",
                require_pump=True,
                require_confirm=True,
                liquidity_threshold=threshold,
                require_sustain=True,
            )
        )

    specs.append(
        FilterSpec(
            name="A+B+D_phase1",
            require_pump=True,
            require_confirm=True,
            allowed_hours=PHASE1_CORE_HOURS,
        )
    )

    hour_pool = derive_hour_pool(signals_df)
    for threshold in [25_000.0, 50_000.0, 100_000.0]:
        for require_sustain in [False, True]:
            for size in range(1, min(5, len(hour_pool)) + 1):
                for subset in itertools.combinations(hour_pool, size):
                    hours = list(subset)
                    name = f"A+B+C{int(threshold/1000)}k+D[{','.join(str(h) for h in hours)}]"
                    if require_sustain:
                        name += "+E"
                    specs.append(
                        FilterSpec(
                            name=name,
                            require_pump=True,
                            require_confirm=True,
                            liquidity_threshold=threshold,
                            allowed_hours=hours,
                            require_sustain=require_sustain,
                        )
                    )
    return specs


def run_filter_search(signals_df: pd.DataFrame) -> pd.DataFrame:
    results = []
    for spec in build_filter_specs(signals_df):
        subset = apply_filter_spec(signals_df, spec)
        results.append(evaluate_subset(subset, spec.name))

    results_df = pd.DataFrame(results)
    results_df = results_df.drop_duplicates(subset=["filters"]).sort_values(
        ["precision_10m", "avg_ret_10m", "signals_per_day"],
        ascending=[False, False, False],
    )
    return results_df


def lab_investigation(signals_df: pd.DataFrame) -> dict[str, object]:
    lab = signals_df[signals_df["sym"] == "LAB-USDT-SWAP"].copy()
    other = signals_df[signals_df["sym"] != "LAB-USDT-SWAP"].copy()

    filtered_with_lab = apply_filter_spec(
        signals_df,
        FilterSpec(
            name="A+B+C50k",
            require_pump=True,
            require_confirm=True,
            liquidity_threshold=50_000.0,
        ),
    )
    filtered_without_lab = filtered_with_lab[filtered_with_lab["sym"] != "LAB-USDT-SWAP"].copy()

    metrics = {
        "lab_signal_count": int(len(lab)),
        "all_signal_count": int(len(signals_df)),
        "lab_share_pct": float(len(lab) / len(signals_df) * 100.0) if len(signals_df) else float("nan"),
        "lab_avg_signal_dollar_volume": mean(lab["signal_dollar_volume"]),
        "lab_median_signal_dollar_volume": float(lab["signal_dollar_volume"].median()) if not lab.empty else float("nan"),
        "other_avg_signal_dollar_volume": mean(other["signal_dollar_volume"]),
        "other_median_signal_dollar_volume": float(other["signal_dollar_volume"].median()) if not other.empty else float("nan"),
        "lab_median_normal_vol": float(lab["avg_vol"].median()) if not lab.empty else float("nan"),
        "lab_median_spike_vol": float(lab["signal_vol"].median()) if not lab.empty else float("nan"),
        "lab_median_vol_ratio": float(lab["vol_ratio"].median()) if not lab.empty else float("nan"),
        "other_median_normal_vol": float(other["avg_vol"].median()) if not other.empty else float("nan"),
        "other_median_spike_vol": float(other["signal_vol"].median()) if not other.empty else float("nan"),
        "other_median_vol_ratio": float(other["vol_ratio"].median()) if not other.empty else float("nan"),
        "lab_pass_c50k_pct": float((lab["signal_dollar_volume"] >= 50_000.0).mean() * 100.0) if not lab.empty else float("nan"),
        "ab_c50k_with_lab_n": int(len(filtered_with_lab)),
        "ab_c50k_with_lab_precision_10m": precision(filtered_with_lab["ret_10m"]),
        "ab_c50k_with_lab_avg_ret_10m": mean(filtered_with_lab["ret_10m"]),
        "ab_c50k_without_lab_n": int(len(filtered_without_lab)),
        "ab_c50k_without_lab_precision_10m": precision(filtered_without_lab["ret_10m"]),
        "ab_c50k_without_lab_avg_ret_10m": mean(filtered_without_lab["ret_10m"]),
    }

    if metrics["lab_pass_c50k_pct"] < 10.0:
        decision = "EXCLUDE"
        reason = "LAB almost never passes a meaningful signal-dollar-volume threshold, so its spikes look structurally low-quality."
    elif metrics["ab_c50k_without_lab_precision_10m"] > metrics["ab_c50k_with_lab_precision_10m"] + 3.0:
        decision = "EXCLUDE"
        reason = "Removing LAB materially improves filtered precision, so LAB is diluting the stack."
    else:
        decision = "DO_NOT_AUTO_EXCLUDE"
        reason = "LAB dominates raw counts, but after correct ctVal-adjusted dollar-volume checks it still passes liquidity often and does not materially degrade filtered precision."

    metrics["decision"] = decision
    metrics["reason"] = reason
    return metrics


def print_summary(results_df: pd.DataFrame, lab_metrics: dict[str, object]) -> None:
    core = results_df[
        (results_df["n"] >= 30) &
        (results_df["avg_ret_10m"] > 0.5)
    ].copy()
    target = core[core["precision_10m"] >= 60.0].copy()

    print("=== FILTER STACK RESULTS ===")
    print("Top 20 by precision_10m:")
    cols = ["filters", "n", "signals_per_day", "precision_10m", "avg_ret_10m", "avg_r_r"]
    print(results_df[cols].head(20).to_string(index=False))

    print("\nTarget stacks (n >= 30, >= 1/day, precision_10m >= 60, avg_ret_10m > 0.5):")
    if target.empty:
        print("No A/B/C/D/E stack met the target.")
    else:
        print(target[cols].head(20).to_string(index=False))

    print("\n=== LAB INVESTIGATION ===")
    for key in [
        "lab_signal_count",
        "all_signal_count",
        "lab_share_pct",
        "lab_avg_signal_dollar_volume",
        "lab_median_signal_dollar_volume",
        "other_avg_signal_dollar_volume",
        "other_median_signal_dollar_volume",
        "lab_pass_c50k_pct",
        "ab_c50k_with_lab_n",
        "ab_c50k_with_lab_precision_10m",
        "ab_c50k_without_lab_n",
        "ab_c50k_without_lab_precision_10m",
        "decision",
        "reason",
    ]:
        print(f"{key}: {lab_metrics[key]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save-signals", action="store_true", help="save phase2 signal table to cache")
    args = parser.parse_args()

    frames = load_frames()
    ctvals = fetch_ctvals()
    signals_df = build_signal_table(frames, ctvals)
    results_df = run_filter_search(signals_df)
    lab_metrics = lab_investigation(signals_df)

    results_df.to_csv(FILTER_RESULTS_PATH, index=False)
    LAB_RESULTS_PATH.write_text(json.dumps(lab_metrics, indent=2), encoding="utf-8")
    if args.save_signals:
        signals_df.to_pickle(FILTER_SIGNALS_PATH)

    print_summary(results_df, lab_metrics)
    print(f"\nSaved filter results -> {FILTER_RESULTS_PATH}")
    print(f"Saved LAB summary   -> {LAB_RESULTS_PATH}")
    if args.save_signals:
        print(f"Saved signal table  -> {FILTER_SIGNALS_PATH}")


if __name__ == "__main__":
    main()
