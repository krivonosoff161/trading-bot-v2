"""
Parameter sweep for the OKX WS pump scanner backtest.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bt_pump_core import DEFAULT_CACHE_DIR, analyze_forward, detect_signals


VOL_MULT_GRID = [1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
PRICE_PCT_GRID = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0]
LOOKBACK_GRID = [5, 10, 15, 20]
DAYS_IN_STUDY = 30
RESULTS_PATH = DEFAULT_CACHE_DIR / "sweep_results.csv"


def load_frames(cache_dir: Path = DEFAULT_CACHE_DIR) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for path in sorted(cache_dir.glob("*_1m_30d.pkl")):
        symbol = path.name.replace("_1m_30d.pkl", "")
        df = pd.read_pickle(path).sort_index()
        df.attrs["symbol"] = symbol
        frames[symbol] = df
    if not frames:
        raise FileNotFoundError(f"No 30d cache files found in {cache_dir}")
    return frames


def _mean(values: list[float]) -> float:
    return float(np.mean(values)) if values else float("nan")


def _precision(values: list[float]) -> float:
    return float(np.mean([value > 0 for value in values]) * 100.0) if values else float("nan")


def run_sweep(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    results: list[dict[str, float | int | str]] = []
    combos = list(itertools.product(VOL_MULT_GRID, PRICE_PCT_GRID, LOOKBACK_GRID))

    for idx, (vol_mult, price_pct, lookback) in enumerate(combos, start=1):
        all_forward = []
        pair_return10: dict[str, list[float]] = {}

        for symbol, df in frames.items():
            if len(df) <= lookback + 1:
                continue

            signals = detect_signals(
                df=df,
                vol_mult=vol_mult,
                price_pct=price_pct,
                lookback=lookback,
                sym=symbol,
            )

            pair_values_10: list[float] = []
            for signal in signals:
                try:
                    forward = analyze_forward(df, signal)
                except ValueError:
                    continue
                all_forward.append(forward)
                if 10 in forward.directional_returns:
                    pair_values_10.append(forward.directional_returns[10])

            if pair_values_10:
                pair_return10[symbol] = pair_values_10

        ret_5 = [f.directional_returns[5] for f in all_forward if 5 in f.directional_returns]
        ret_10 = [f.directional_returns[10] for f in all_forward if 10 in f.directional_returns]
        ret_15 = [f.directional_returns[15] for f in all_forward if 15 in f.directional_returns]

        avg_max_gain = _mean([f.max_gain for f in all_forward])
        avg_max_loss = _mean([f.max_loss for f in all_forward])
        if np.isnan(avg_max_gain) or np.isnan(avg_max_loss):
            avg_r_r = float("nan")
        elif abs(avg_max_loss) < 1e-12:
            avg_r_r = float("inf")
        else:
            avg_r_r = avg_max_gain / abs(avg_max_loss)

        best_pair = ""
        if pair_return10:
            best_pair = max(pair_return10.items(), key=lambda item: float(np.mean(item[1])))[0]

        record = {
            "vol_mult": vol_mult,
            "price_pct": price_pct,
            "lookback": lookback,
            "n_signals": len(all_forward),
            "signals_per_day": len(all_forward) / DAYS_IN_STUDY,
            "precision_5m": _precision(ret_5),
            "precision_10m": _precision(ret_10),
            "precision_15m": _precision(ret_15),
            "avg_return_5m": _mean(ret_5),
            "avg_return_10m": _mean(ret_10),
            "avg_max_gain": avg_max_gain,
            "avg_max_loss": avg_max_loss,
            "avg_r_r": avg_r_r,
            "reversed_rate": float(np.mean([f.reversed for f in all_forward]) * 100.0) if all_forward else float("nan"),
            "best_pair": best_pair,
        }
        results.append(record)
        print(
            f"[{idx:03d}/{len(combos)}] vol_mult={vol_mult} price_pct={price_pct} "
            f"lookback={lookback} n={record['n_signals']} "
            f"prec10={record['precision_10m']:.2f} rr={record['avg_r_r']:.3f}"
        )

    return pd.DataFrame(results)


def print_top_configs(results: pd.DataFrame, limit: int = 20) -> None:
    filtered = results[
        (results["signals_per_day"] >= 1.0) &
        (results["precision_10m"] >= 55.0)
    ].copy()
    filtered = filtered.sort_values(["avg_r_r", "avg_return_10m"], ascending=[False, False]).head(limit)

    if filtered.empty:
        print("\nNo configurations passed filters: signals_per_day >= 1 and precision_10m >= 55%.")
        return

    columns = [
        "vol_mult",
        "price_pct",
        "lookback",
        "n_signals",
        "signals_per_day",
        "precision_10m",
        "avg_return_10m",
        "avg_max_gain",
        "avg_max_loss",
        "avg_r_r",
        "reversed_rate",
        "best_pair",
    ]
    print("\nTop configurations:")
    print(filtered[columns].to_string(index=False))


def main() -> None:
    frames = load_frames()
    print(f"Loaded {len(frames)} cached pairs from {DEFAULT_CACHE_DIR}")
    results = run_sweep(frames)
    results.to_csv(RESULTS_PATH, index=False)
    print(f"\nSaved sweep results -> {RESULTS_PATH}")
    print_top_configs(results)


if __name__ == "__main__":
    main()
