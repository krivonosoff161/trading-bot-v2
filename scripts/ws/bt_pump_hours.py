"""Analyze pump-signal quality by UTC hour."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bt_pump_filters import DEFAULT_CACHE_DIR, mean, precision


SIGNAL_TABLE_PATH = DEFAULT_CACHE_DIR / "phase2_signal_table.pkl"
OUTPUT_PATH = DEFAULT_CACHE_DIR / "pump_hours_analysis.csv"


def main() -> None:
    df = pd.read_pickle(SIGNAL_TABLE_PATH)

    filtered = df[
        df["filter_a_pump"] &
        df["filter_b_confirm"] &
        (df["signal_dollar_volume"] >= 50_000) &
        df["filter_e_sustain"]
    ].copy()

    print(f"Total A+B+C50k+E signals: {len(filtered)}")

    stats = (
        filtered.groupby("hour")
        .agg(
            n=("hour", "size"),
            precision_10m=("ret_10m", precision),
            avg_ret_10m=("ret_10m", mean),
            avg_ret_5m=("ret_5m", mean),
            avg_max_gain=("max_gain", mean),
            avg_max_loss=("max_loss", mean),
        )
        .reset_index()
        .sort_values(["precision_10m", "avg_ret_10m", "n"], ascending=[False, False, False])
    )

    print("\n=== QUALITY BY UTC HOUR (A+B+C50k+E) ===")
    print(stats.to_string(index=False))

    good = stats[(stats["precision_10m"] >= 55.0) & (stats["n"] >= 3)]["hour"].tolist()
    good = sorted(int(hour) for hour in good)
    print(f"\nRecommended hours (precision>=55%, n>=3): {good}")
    print(f"-> Update config.yaml active_hours: {good}")

    stats.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
