from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.backtest.research_common import FEE_RT_PCT, net_r, save_json, summarize
WS_CACHE = ROOT / "scripts" / "ws" / "cache"
SIGNALS_LOG = ROOT / "logs" / "pump" / "pump_signals.jsonl"
LABELS_LOG = ROOT / "logs" / "pump" / "pump_labels.jsonl"
RESULT_PATH = Path(__file__).with_name("bt_pump_trailing_results.json")
CONFIGS = [(0.5, 1.0, 2.5), (0.5, 1.5, 2.5), (1.0, 1.0, 3.0), (1.0, 1.5, 3.0), (0.0, 1.0, 2.5), (0.0, 0.5, 2.5)]


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def baseline_stats() -> dict:
    exits = [row for row in load_jsonl(LABELS_LOG) if row.get("type") == "EXIT"]
    trades = []
    for row in exits:
        stop = row["entry_price"] * (1 - 0.008) if row["net_pnl_pct"] >= 0 or row["exit_reason"] == "SL" else row["entry_price"]
        trades.append({"outcome": row["exit_reason"], "hold_min": row["hold_min"], "result_r": row["net_pnl_pct"] / 0.8})
    return summarize(trades)


def entry_rows() -> list[dict]:
    entries = {}
    for row in load_jsonl(SIGNALS_LOG):
        if row.get("type") == "ENTRY":
            entries[row["signal_id"]] = row
    exits = [row for row in load_jsonl(LABELS_LOG) if row.get("type") == "EXIT"]
    joined = []
    for row in exits:
        entry = entries.get(row["signal_id"])
        if entry:
            joined.append(entry)
    return joined


def load_frames(symbols: set[str]) -> dict[str, pd.DataFrame]:
    frames = {}
    for symbol in symbols:
        matches = sorted(WS_CACHE.glob(f"{symbol}_1m_*d.pkl"), key=lambda path: path.stat().st_mtime, reverse=True)
        if matches:
            frames[symbol] = pd.read_pickle(matches[0]).sort_index()
    return frames


def simulate(entry: dict, frame: pd.DataFrame, be_pct: float, trail_mult: float, tp_mult: float) -> dict | None:
    ts = pd.Timestamp(entry["ts_utc"])
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    side = "buy" if entry["direction"] == "PUMP" else "sell"
    entry_price = float(entry["entry_open_price"])
    atr = abs(float(entry["paper_sl"]) - entry_price) / 1.5
    if atr <= 0:
        return None
    window = frame.loc[frame.index >= ts].head(60)
    if window.empty:
        return None
    stop = float(entry["paper_sl"])
    target = entry_price + atr * tp_mult if side == "buy" else entry_price - atr * tp_mult
    highest = lowest = entry_price
    armed = be_pct == 0.0

    for minute, (_idx, row) in enumerate(window.iterrows(), start=1):
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        highest = max(highest, high)
        lowest = min(lowest, low)
        favorable_pct = (highest - entry_price) / entry_price * 100 if side == "buy" else (entry_price - lowest) / entry_price * 100
        if not armed and favorable_pct >= be_pct:
            armed = True
            be_stop = entry_price * (1.0 + FEE_RT_PCT) if side == "buy" else entry_price * (1.0 - FEE_RT_PCT)
            stop = max(stop, be_stop) if side == "buy" else min(stop, be_stop)
        if armed:
            trail = highest - atr * trail_mult if side == "buy" else lowest + atr * trail_mult
            stop = max(stop, trail) if side == "buy" else min(stop, trail)
        if side == "buy" and high >= target:
            return {"outcome": "TP", "exit_price": target, "hold_min": minute}
        if side == "sell" and low <= target:
            return {"outcome": "TP", "exit_price": target, "hold_min": minute}
        if side == "buy" and low <= stop:
            outcome = "BE_EXIT" if armed and stop >= entry_price else "STOP"
            return {"outcome": outcome, "exit_price": stop, "hold_min": minute}
        if side == "sell" and high >= stop:
            outcome = "BE_EXIT" if armed and stop <= entry_price else "STOP"
            return {"outcome": outcome, "exit_price": stop, "hold_min": minute}
        last_close = close
    return {"outcome": "TIME_EXIT", "exit_price": last_close, "hold_min": minute}


def main() -> None:
    entries = entry_rows()
    frames = load_frames({row["sym"] for row in entries})
    payload = {"baseline": baseline_stats(), "configs": {}}

    for be_pct, trail_mult, tp_mult in CONFIGS:
        label = f"be_{be_pct}_trail_{trail_mult}_tp_{tp_mult}"
        trades = []
        for entry in entries:
            frame = frames.get(entry["sym"])
            if frame is None:
                continue
            result = simulate(entry, frame, be_pct, trail_mult, tp_mult)
            if result is None:
                continue
            side = "buy" if entry["direction"] == "PUMP" else "sell"
            stop = float(entry["paper_sl"])
            trades.append(
                {
                    "outcome": result["outcome"],
                    "hold_min": result["hold_min"],
                    "result_r": net_r(side, float(entry["entry_open_price"]), result["exit_price"], stop),
                }
            )
        payload["configs"][label] = {
            **summarize(trades),
            "breakeven_pct": be_pct,
            "trail_atr_mult": trail_mult,
            "tp_atr_mult": tp_mult,
        }

    ranked = sorted(payload["configs"].items(), key=lambda item: (item[1]["pf"], item[1]["avg_r"]), reverse=True)
    payload["top3"] = [{"label": label, **stats} for label, stats in ranked[:3]]
    save_json(RESULT_PATH, payload)

    print(f"Baseline: WR={payload['baseline']['wr']:.1f}% PF={payload['baseline']['pf']:.2f}")
    for row in payload["top3"]:
        print(
            f"{row['label']}: n={row['n']} WR={row['wr']:.1f}% PF={row['pf']:.2f} "
            f"avg_R={row['avg_r']:+.3f} BE={row['be_exit_count']}"
        )
    print(f"Saved -> {RESULT_PATH.name}")


if __name__ == "__main__":
    main()
