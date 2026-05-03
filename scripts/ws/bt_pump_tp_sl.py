"""Path-aware TP/SL sweep on historical pump signals."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bt_pump_filters import DEFAULT_CACHE_DIR, load_frames
from ws_pump_engine import _load_pump_config


SIGNAL_TABLE_PATH = DEFAULT_CACHE_DIR / "phase2_signal_table.pkl"
OUTPUT_PATH = DEFAULT_CACHE_DIR / "pump_tp_sl_sweep.csv"

TP_VALUES = [0.2, 0.3, 0.5, 0.8, 1.0, 1.5]
SL_VALUES = [0.2, 0.3, 0.5, 0.8]
FEE_RT = 0.10
HOLD_BARS = 15


def _filter_signal_table(df: pd.DataFrame, active_hours: list[int]) -> pd.DataFrame:
    filtered = df[
        df["filter_a_pump"] &
        df["filter_b_confirm"] &
        (df["signal_dollar_volume"] >= 50_000) &
        df["filter_e_sustain"]
    ].copy()
    if active_hours:
        filtered = filtered[filtered["hour"].isin(active_hours)].copy()
    return filtered


def simulate_one_signal(
    frame: pd.DataFrame,
    signal_idx: int,
    tp_pct: float,
    sl_pct: float,
    fee_rt: float,
    hold_bars: int,
) -> dict[str, float | str | int]:
    entry_idx = signal_idx + 1
    if entry_idx >= len(frame):
        raise ValueError("missing entry candle")

    entry_price = float(frame.iloc[entry_idx]["open"])
    tp_price = entry_price * (1 + tp_pct / 100.0)
    sl_price = entry_price * (1 - sl_pct / 100.0)

    window = frame.iloc[entry_idx : entry_idx + hold_bars]
    if window.empty:
        raise ValueError("missing forward window")

    for minute, (_, row) in enumerate(window.iterrows(), start=1):
        high = float(row["high"])
        low = float(row["low"])
        if low <= sl_price and high >= tp_price:
            gross = -sl_pct
            return {"outcome": "SL", "gross": gross, "net": gross - fee_rt, "hold_min": minute}
        if low <= sl_price:
            gross = -sl_pct
            return {"outcome": "SL", "gross": gross, "net": gross - fee_rt, "hold_min": minute}
        if high >= tp_price:
            gross = tp_pct
            return {"outcome": "TP", "gross": gross, "net": gross - fee_rt, "hold_min": minute}

    last_close = float(window.iloc[-1]["close"])
    gross = (last_close - entry_price) / entry_price * 100.0
    return {"outcome": "TIME", "gross": gross, "net": gross - fee_rt, "hold_min": len(window)}


def simulate_tp_sl(
    filtered: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    tp_pct: float,
    sl_pct: float,
    fee_rt: float,
    hold_bars: int,
) -> list[dict[str, float | str | int]]:
    results: list[dict[str, float | str | int]] = []
    for _, row in filtered.iterrows():
        frame = frames.get(str(row["sym"]))
        if frame is None:
            continue
        try:
            sim = simulate_one_signal(
                frame=frame,
                signal_idx=int(row["signal_idx"]),
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                fee_rt=fee_rt,
                hold_bars=hold_bars,
            )
        except ValueError:
            continue
        sim["sym"] = str(row["sym"])
        sim["ts"] = row["ts"]
        results.append(sim)
    return results


def main() -> None:
    cfg = _load_pump_config()
    frames = load_frames()
    df = pd.read_pickle(SIGNAL_TABLE_PATH)
    active_hours = [int(hour) for hour in cfg.get("active_hours", [])]
    filtered = _filter_signal_table(df, active_hours)

    print(f"Signals for sweep: {len(filtered)}")
    print(f"Filter set: A+B+C50k+E + hours={active_hours if active_hours else 'ALL'}")
    print(f"Simulation: path-aware, entry=open(next candle), hold={HOLD_BARS}m, fee_rt={FEE_RT}%")

    rows: list[dict[str, float | int]] = []
    for tp in TP_VALUES:
        for sl in SL_VALUES:
            sims = simulate_tp_sl(filtered, frames, tp, sl, FEE_RT, HOLD_BARS)
            n = len(sims)
            nets = [float(item["net"]) for item in sims]
            wins = sum(1 for x in nets if x > 0)
            wr = wins / n * 100.0 if n else 0.0
            total = sum(nets)
            avg = total / n if n else 0.0
            gains = [x for x in nets if x > 0]
            losses = [abs(x) for x in nets if x < 0]
            pf = sum(gains) / sum(losses) if losses else float("inf")
            tp_rate = sum(1 for item in sims if item["outcome"] == "TP") / n * 100.0 if n else 0.0
            sl_rate = sum(1 for item in sims if item["outcome"] == "SL") / n * 100.0 if n else 0.0
            time_rate = sum(1 for item in sims if item["outcome"] == "TIME") / n * 100.0 if n else 0.0
            avg_hold = sum(float(item["hold_min"]) for item in sims) / n if n else 0.0
            rows.append(
                {
                    "tp_pct": tp,
                    "sl_pct": sl,
                    "n": n,
                    "wr_pct": round(wr, 1),
                    "tp_rate_pct": round(tp_rate, 1),
                    "sl_rate_pct": round(sl_rate, 1),
                    "time_rate_pct": round(time_rate, 1),
                    "avg_hold_min": round(avg_hold, 1),
                    "total_net_pct": round(total, 2),
                    "avg_net_pct": round(avg, 3),
                    "profit_factor": round(pf, 2),
                }
            )

    result = pd.DataFrame(rows).sort_values(
        ["total_net_pct", "profit_factor", "wr_pct"],
        ascending=[False, False, False],
    )

    print("\n=== TOP TP/SL COMBINATIONS (path-aware, by total net P&L) ===")
    print(result.head(15).to_string(index=False))

    best = result.iloc[0]
    print(f"\nBest combination: TP={best.tp_pct}% SL={best.sl_pct}%")
    print(f"Rates: TP={best.tp_rate_pct}% SL={best.sl_rate_pct}% TIME={best.time_rate_pct}%")
    print(f"-> Update config.yaml paper_tp_pct: {best.tp_pct}")
    print(f"-> Update config.yaml paper_sl_pct: {best.sl_pct}")

    result.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
