"""
Tape aggregation and tape-aware research for Phase 2.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bt_pump_core import DEFAULT_CACHE_DIR, analyze_forward, detect_signals
from bt_pump_filters import (
    BASE_LOOKBACK,
    BASE_PRICE_PCT,
    BASE_VOL_MULT,
    FILTER_SIGNALS_PATH,
    FilterSpec,
    apply_filter_spec,
    load_frames,
    precision,
)


TAPE_DIR = Path(__file__).resolve().parents[1] / "tape"
TAPE_PAIRS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "DOGE-USDT-SWAP", "XRP-USDT-SWAP"]
TAPE_SIGNALS_PATH = DEFAULT_CACHE_DIR / "phase2_signal_table_tape.pkl"
TAPE_SUMMARY_PATH = DEFAULT_CACHE_DIR / "tape_analysis_summary.json"


def mean(series: pd.Series) -> float:
    clean = series.dropna()
    if clean.empty:
        return float("nan")
    return float(clean.mean())


def iter_tape_paths() -> list[Path]:
    paths = []
    for path in sorted(TAPE_DIR.glob("2026-*.csv*")):
        stem = path.name.replace(".csv.gz", "").replace(".csv", "")
        if stem < "2026-04-11" or stem > "2026-05-02":
            continue
        paths.append(path)
    return paths


def aggregate_tape_minute(cache_dir: Path = DEFAULT_CACHE_DIR, force: bool = False) -> dict[str, pd.DataFrame]:
    cached = {sym: cache_dir / f"{sym}_tape_1m.pkl" for sym in TAPE_PAIRS}
    if not force and all(path.exists() for path in cached.values()):
        return {sym: pd.read_pickle(path).sort_index() for sym, path in cached.items()}

    pair_chunks: dict[str, list[pd.DataFrame]] = {sym: [] for sym in TAPE_PAIRS}
    paths = iter_tape_paths()

    for day_idx, path in enumerate(paths, start=1):
        compression = "gzip" if path.suffix == ".gz" else None
        print(f"[{day_idx}/{len(paths)}] aggregate {path.name}")

        for chunk_idx, chunk in enumerate(
            pd.read_csv(
                path,
                compression=compression,
                usecols=["ts_ms", "symbol", "side", "price", "size"],
                chunksize=1_000_000,
            ),
            start=1,
        ):
            chunk = chunk[chunk["symbol"].isin(TAPE_PAIRS)].copy()
            if chunk.empty:
                continue

            chunk["ts_ms"] = chunk["ts_ms"].astype("int64")
            chunk["minute_ts"] = (chunk["ts_ms"] // 60_000) * 60_000
            chunk["signed_size"] = np.where(chunk["side"] == "buy", chunk["size"], -chunk["size"])
            chunk["buy_count"] = (chunk["side"] == "buy").astype("int64")
            chunk["trade_count"] = 1
            chunk["dv_1m"] = chunk["price"] * chunk["size"]

            grouped = (
                chunk.groupby(["symbol", "minute_ts"], observed=True)
                .agg(
                    cvd_1m=("signed_size", "sum"),
                    total_size=("size", "sum"),
                    buy_count=("buy_count", "sum"),
                    trade_count=("trade_count", "sum"),
                    dv_1m=("dv_1m", "sum"),
                )
                .reset_index()
            )

            for symbol, part in grouped.groupby("symbol", observed=True):
                pair_chunks[symbol].append(part.copy())

            if chunk_idx % 5 == 0:
                print(f"  chunks processed: {chunk_idx}")

    tape_frames: dict[str, pd.DataFrame] = {}
    for symbol, parts in pair_chunks.items():
        if not parts:
            tape_frames[symbol] = pd.DataFrame()
            continue

        df = pd.concat(parts, ignore_index=True)
        df = (
            df.groupby("minute_ts", as_index=False)
            .agg(
                cvd_1m=("cvd_1m", "sum"),
                total_size=("total_size", "sum"),
                buy_count=("buy_count", "sum"),
                trade_count=("trade_count", "sum"),
                dv_1m=("dv_1m", "sum"),
            )
            .sort_values("minute_ts")
        )
        df["minute_ts"] = df["minute_ts"].astype("int64")
        valid_mask = (df["minute_ts"] >= 1_735_689_600_000) & (df["minute_ts"] <= 1_924_992_000_000)
        dropped = int((~valid_mask).sum())
        if dropped:
            print(f"{symbol}: dropped invalid minute rows={dropped}")
        df = df.loc[valid_mask].copy()
        df["buy_ratio"] = np.where(df["trade_count"] > 0, df["buy_count"] / df["trade_count"], np.nan)
        df["speed"] = df["trade_count"] / 60.0
        df["cvd_5m"] = df["cvd_1m"].rolling(5, min_periods=1).sum()
        df["speed_median_30"] = df["speed"].rolling(30, min_periods=10).median()
        df["dv_median_30"] = df["dv_1m"].rolling(30, min_periods=10).median()
        df["abs_cvd_median_30"] = df["cvd_1m"].abs().rolling(30, min_periods=10).median()
        df["cluster"] = (
            (df["buy_ratio"] > 0.60) &
            (df["cvd_1m"] > df["abs_cvd_median_30"].fillna(np.inf) * 1.5) &
            (df["dv_1m"] > df["dv_median_30"].fillna(np.inf) * 1.2)
        )

        df.index = pd.to_datetime(df["minute_ts"], unit="ms", utc=True)
        df = df.drop(columns=["minute_ts"])
        save_path = cache_dir / f"{symbol}_tape_1m.pkl"
        df.to_pickle(save_path)
        print(f"saved {save_path.name} rows={len(df)}")
        tape_frames[symbol] = df

    return tape_frames


def load_tape_frames(cache_dir: Path = DEFAULT_CACHE_DIR) -> dict[str, pd.DataFrame]:
    frames = {}
    for symbol in TAPE_PAIRS:
        path = cache_dir / f"{symbol}_tape_1m.pkl"
        if path.exists():
            frames[symbol] = pd.read_pickle(path).sort_index()
    return frames


def load_phase2_signals() -> pd.DataFrame:
    if FILTER_SIGNALS_PATH.exists():
        return pd.read_pickle(FILTER_SIGNALS_PATH)
    raise FileNotFoundError(f"Missing {FILTER_SIGNALS_PATH}; run bt_pump_filters.py --save-signals first.")


def enrich_signals_with_tape(signals_df: pd.DataFrame, tape_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for row in signals_df.itertuples(index=False):
        record = dict(row._asdict())
        tape_df = tape_frames.get(record["sym"])
        if tape_df is None or record["ts"] not in tape_df.index:
            record.update(
                {
                    "tape_cvd_1m": np.nan,
                    "tape_buy_ratio_1m": np.nan,
                    "tape_speed_1m": np.nan,
                    "tape_dv_1m": np.nan,
                    "tape_cvd_prev_1m": np.nan,
                    "tape_buy_ratio_prev_1m": np.nan,
                    "tape_speed_prev_1m": np.nan,
                    "tape_cvd_5m": np.nan,
                    "tape_speed_median_30": np.nan,
                    "tape_cluster": False,
                    "filter_f_tape_confirm": False,
                    "filter_f_cluster": False,
                }
            )
            rows.append(record)
            continue

        cur = tape_df.loc[record["ts"]]
        prev_idx = record["ts"] - pd.Timedelta(minutes=1)
        prev = tape_df.loc[prev_idx] if prev_idx in tape_df.index else None

        record.update(
            {
                "tape_cvd_1m": float(cur["cvd_1m"]),
                "tape_buy_ratio_1m": float(cur["buy_ratio"]),
                "tape_speed_1m": float(cur["speed"]),
                "tape_dv_1m": float(cur["dv_1m"]),
                "tape_cvd_prev_1m": float(prev["cvd_1m"]) if prev is not None else np.nan,
                "tape_buy_ratio_prev_1m": float(prev["buy_ratio"]) if prev is not None else np.nan,
                "tape_speed_prev_1m": float(prev["speed"]) if prev is not None else np.nan,
                "tape_cvd_5m": float(cur["cvd_5m"]),
                "tape_speed_median_30": float(cur["speed_median_30"]) if not pd.isna(cur["speed_median_30"]) else np.nan,
                "tape_cluster": bool(cur["cluster"]),
                "filter_f_tape_confirm": bool(
                    record["direction"] == "PUMP" and
                    cur["cvd_1m"] > 0 and
                    cur["buy_ratio"] > 0.60 and
                    (pd.isna(cur["speed_median_30"]) or cur["speed"] > cur["speed_median_30"])
                ),
                "filter_f_cluster": bool(record["direction"] == "PUMP" and cur["cluster"]),
            }
        )
        rows.append(record)

    enriched = pd.DataFrame(rows)
    enriched.to_pickle(TAPE_SIGNALS_PATH)
    return enriched


def analyze_tape_features(enriched_df: pd.DataFrame) -> dict[str, object]:
    pump = enriched_df[(enriched_df["sym"].isin(TAPE_PAIRS)) & (enriched_df["direction"] == "PUMP")].copy()

    cvd_pos = pump[pump["tape_cvd_1m"] > 0]
    cvd_neg = pump[pump["tape_cvd_1m"] <= 0]
    buy60 = pump[pump["tape_buy_ratio_1m"] > 0.60]
    buy_le60 = pump[pump["tape_buy_ratio_1m"] <= 0.60]
    speed_high = pump[pump["tape_speed_1m"] > pump["tape_speed_median_30"]]
    speed_low = pump[pump["tape_speed_1m"] <= pump["tape_speed_median_30"]]
    dv50 = pump[pump["tape_dv_1m"] > 50_000]
    dv_lt50 = pump[pump["tape_dv_1m"] <= 50_000]
    cluster = pump[pump["filter_f_cluster"]]
    tape_confirm = pump[pump["filter_f_tape_confirm"]]

    summary = {
        "pump_tape_signals": int(len(pump)),
        "cvd_pos_n": int(len(cvd_pos)),
        "cvd_pos_precision_10m": precision(cvd_pos["ret_10m"]),
        "cvd_neg_n": int(len(cvd_neg)),
        "cvd_neg_precision_10m": precision(cvd_neg["ret_10m"]),
        "buy60_n": int(len(buy60)),
        "buy60_precision_10m": precision(buy60["ret_10m"]),
        "buy_le60_n": int(len(buy_le60)),
        "buy_le60_precision_10m": precision(buy_le60["ret_10m"]),
        "speed_high_n": int(len(speed_high)),
        "speed_high_precision_10m": precision(speed_high["ret_10m"]),
        "speed_low_n": int(len(speed_low)),
        "speed_low_precision_10m": precision(speed_low["ret_10m"]),
        "dv50_n": int(len(dv50)),
        "dv50_precision_10m": precision(dv50["ret_10m"]),
        "dv_lt50_n": int(len(dv_lt50)),
        "dv_lt50_precision_10m": precision(dv_lt50["ret_10m"]),
        "cluster_n": int(len(cluster)),
        "cluster_precision_10m": precision(cluster["ret_10m"]),
        "cluster_avg_ret_10m": mean(cluster["ret_10m"]),
        "tape_confirm_n": int(len(tape_confirm)),
        "tape_confirm_precision_10m": precision(tape_confirm["ret_10m"]),
        "tape_confirm_avg_ret_10m": mean(tape_confirm["ret_10m"]),
    }
    return summary


def btc_eth_lag_test(frames: dict[str, pd.DataFrame]) -> dict[str, object]:
    btc_df = frames["BTC-USDT-SWAP"]
    eth_df = frames["ETH-USDT-SWAP"]
    btc_signals = [
        signal for signal in detect_signals(btc_df, BASE_VOL_MULT, BASE_PRICE_PCT, BASE_LOOKBACK, sym="BTC-USDT-SWAP")
        if signal.direction == "PUMP"
    ]

    eth_opens = eth_df["open"].to_numpy(dtype="float64")
    eth_closes = eth_df["close"].to_numpy(dtype="float64")
    eth_index = eth_df.index

    rows = []
    for signal in btc_signals:
        if signal.ts < pd.Timestamp("2026-04-11T00:00:00Z") or signal.ts > pd.Timestamp("2026-05-02T23:59:59Z"):
            continue
        if signal.ts not in eth_index:
            continue
        eth_idx = eth_index.get_loc(signal.ts)
        if isinstance(eth_idx, slice) or eth_idx + 3 >= len(eth_df):
            continue
        entry_idx = eth_idx + 1
        entry_price = float(eth_opens[entry_idx])
        if entry_price <= 0:
            continue
        r1 = (float(eth_closes[eth_idx + 1]) - entry_price) / entry_price * 100.0
        r2 = (float(eth_closes[eth_idx + 2]) - entry_price) / entry_price * 100.0
        r3 = (float(eth_closes[eth_idx + 3]) - entry_price) / entry_price * 100.0
        rows.append({"ts": signal.ts, "eth_r1": r1, "eth_r2": r2, "eth_r3": r3})

    lag_df = pd.DataFrame(rows)
    unconditional = []
    for idx in range(1, len(eth_df) - 3):
        entry = float(eth_opens[idx])
        if entry <= 0:
            continue
        unconditional.append((float(eth_closes[idx + 3]) - entry) / entry * 100.0)

    return {
        "btc_pump_events": int(len(lag_df)),
        "eth_precision_1m_after_btc_pump": precision(lag_df["eth_r1"]) if not lag_df.empty else float("nan"),
        "eth_precision_2m_after_btc_pump": precision(lag_df["eth_r2"]) if not lag_df.empty else float("nan"),
        "eth_precision_3m_after_btc_pump": precision(lag_df["eth_r3"]) if not lag_df.empty else float("nan"),
        "eth_avg_ret_3m_after_btc_pump": mean(lag_df["eth_r3"]) if not lag_df.empty else float("nan"),
        "eth_unconditional_precision_3m": float(np.mean(np.array(unconditional) > 0) * 100.0) if unconditional else float("nan"),
        "eth_unconditional_avg_ret_3m": float(np.mean(unconditional)) if unconditional else float("nan"),
    }


def flash_crash_bounce_analysis(frames: dict[str, pd.DataFrame]) -> dict[str, object]:
    rows = []
    for symbol, df in frames.items():
        signals = detect_signals(df, BASE_VOL_MULT, BASE_PRICE_PCT, BASE_LOOKBACK, sym=symbol)
        for signal in signals:
            if signal.direction != "DUMP" or signal.pct_move > -3.0 or signal.vol_ratio < 3.0:
                continue
            try:
                forward = analyze_forward(df, signal)
            except ValueError:
                continue
            rows.append(
                {
                    "sym": symbol,
                    "reversed": bool(forward.reversed),
                    "ret_5m": forward.directional_returns.get(5),
                    "ret_10m": forward.directional_returns.get(10),
                }
            )

    flash_df = pd.DataFrame(rows)
    return {
        "flash_bounce_n": int(len(flash_df)),
        "reversed_rate": float(flash_df["reversed"].mean() * 100.0) if not flash_df.empty else float("nan"),
        "precision_10m": precision(flash_df["ret_10m"]) if not flash_df.empty else float("nan"),
        "avg_ret_10m": mean(flash_df["ret_10m"]) if not flash_df.empty else float("nan"),
    }


def volume_profile_analysis(frames: dict[str, pd.DataFrame], signals_df: pd.DataFrame) -> dict[str, object]:
    rows = []
    for row in signals_df.itertuples(index=False):
        if row.direction != "PUMP":
            continue
        df = frames[row.sym]
        idx = int(row.signal_idx)
        start = max(0, idx - 7 * 1440)
        window = df.iloc[start:idx]
        if len(window) < 500:
            continue

        closes = window["close"].to_numpy(dtype="float64")
        vols = window["vol"].to_numpy(dtype="float64")
        cur_price = float(row.signal_close)
        min_price = float(closes.min())
        max_price = float(closes.max())
        if max_price <= min_price:
            continue

        bins = np.linspace(min_price, max_price, 51)
        hist, edges = np.histogram(closes, bins=bins, weights=vols)
        max_hist = hist.max() if len(hist) else 0.0
        if max_hist <= 0:
            continue

        hvz_indices = np.where(hist >= max_hist * 0.8)[0]
        next_distance_pct = np.nan
        for bin_idx in hvz_indices:
            zone_price = float((edges[bin_idx] + edges[bin_idx + 1]) / 2.0)
            if zone_price > cur_price:
                next_distance_pct = (zone_price - cur_price) / cur_price * 100.0
                break

        if math.isnan(next_distance_pct):
            continue

        rows.append(
            {
                "sym": row.sym,
                "distance_pct": next_distance_pct,
                "ret_10m": row.ret_10m,
                "ret_30m": row.ret_30m,
            }
        )

    vp_df = pd.DataFrame(rows)
    far = vp_df[vp_df["distance_pct"] > 1.0]
    near = vp_df[vp_df["distance_pct"] <= 1.0]
    return {
        "vp_samples": int(len(vp_df)),
        "far_hvz_n": int(len(far)),
        "far_hvz_precision_10m": precision(far["ret_10m"]) if not far.empty else float("nan"),
        "far_hvz_avg_ret_10m": mean(far["ret_10m"]) if not far.empty else float("nan"),
        "near_hvz_n": int(len(near)),
        "near_hvz_precision_10m": precision(near["ret_10m"]) if not near.empty else float("nan"),
        "near_hvz_avg_ret_10m": mean(near["ret_10m"]) if not near.empty else float("nan"),
    }


def print_summary(summary: dict[str, object], enriched_df: pd.DataFrame) -> None:
    print("=== TAPE ANALYSIS ===")
    for key, value in summary["tape"].items():
        print(f"{key}: {value}")

    pump = enriched_df[(enriched_df["sym"].isin(TAPE_PAIRS)) & (enriched_df["direction"] == "PUMP")]
    base_abcd = apply_filter_spec(
        pump,
        FilterSpec(
            name="A+B+C25k+D[3,4,22]",
            require_pump=True,
            require_confirm=True,
            liquidity_threshold=25_000.0,
            allowed_hours=[3, 4, 22],
        ),
    )
    cluster = base_abcd[base_abcd["filter_f_cluster"]]
    tape_confirm = base_abcd[base_abcd["filter_f_tape_confirm"]]

    print("\nA+B+C25k+D[3,4,22] on tape pairs:")
    print(
        f"base n={len(base_abcd)} prec10={precision(base_abcd['ret_10m']):.2f}% avg_ret10={mean(base_abcd['ret_10m']):.4f}%"
    )
    print(
        f"+F_cluster n={len(cluster)} prec10={precision(cluster['ret_10m']):.2f}% avg_ret10={mean(cluster['ret_10m']):.4f}%"
    )
    print(
        f"+F_tape_confirm n={len(tape_confirm)} prec10={precision(tape_confirm['ret_10m']):.2f}% avg_ret10={mean(tape_confirm['ret_10m']):.4f}%"
    )

    print("\n=== BTC->ETH LAG ===")
    for key, value in summary["btc_eth_lag"].items():
        print(f"{key}: {value}")

    print("\n=== FLASH CRASH BOUNCE ===")
    for key, value in summary["flash_bounce"].items():
        print(f"{key}: {value}")

    print("\n=== VOLUME PROFILE ===")
    for key, value in summary["volume_profile"].items():
        print(f"{key}: {value}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-aggregate", action="store_true")
    args = parser.parse_args()

    frames = load_frames()
    aggregate_tape_minute(force=args.force_aggregate)
    tape_frames = load_tape_frames()
    signals_df = load_phase2_signals()
    enriched_df = enrich_signals_with_tape(signals_df, tape_frames)

    summary = {
        "tape": analyze_tape_features(enriched_df),
        "btc_eth_lag": btc_eth_lag_test(frames),
        "flash_bounce": flash_crash_bounce_analysis(frames),
        "volume_profile": volume_profile_analysis(frames, signals_df),
    }

    TAPE_SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print_summary(summary, enriched_df)
    print(f"\nSaved tape-enriched signals -> {TAPE_SIGNALS_PATH}")
    print(f"Saved tape summary          -> {TAPE_SUMMARY_PATH}")


if __name__ == "__main__":
    main()
