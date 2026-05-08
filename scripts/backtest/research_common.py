from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
CACHE_FILE = Path(__file__).parent / "backtest_candle_cache_65d.pkl"
SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT", "ADA-USDT"]
ADX_PERIOD = 9
FEE_RT_PCT = 0.0007


def load_cache() -> dict:
    with CACHE_FILE.open("rb") as f:
        return pickle.load(f)


def closed_rows(raw: list) -> list:
    return [row for row in raw if len(row) < 9 or row[8] == "1"]


def arrays(raw: list) -> tuple[np.ndarray, ...]:
    rows = closed_rows(raw)
    ts = np.array([int(row[0]) for row in rows], dtype=np.int64)
    o = np.array([float(row[1]) for row in rows], dtype=float)
    h = np.array([float(row[2]) for row in rows], dtype=float)
    l = np.array([float(row[3]) for row in rows], dtype=float)
    c = np.array([float(row[4]) for row in rows], dtype=float)
    v = np.array([float(row[5]) for row in rows], dtype=float)
    return ts, o, h, l, c, v


def bollinger_series(closes: np.ndarray, period: int = 20, mult: float = 2.0) -> tuple[np.ndarray, ...]:
    mid = np.zeros(len(closes))
    upper = np.zeros(len(closes))
    lower = np.zeros(len(closes))
    for idx in range(period - 1, len(closes)):
        window = closes[idx - period + 1:idx + 1]
        avg = float(np.mean(window))
        std = float(np.std(window, ddof=0))
        mid[idx] = avg
        upper[idx] = avg + mult * std
        lower[idx] = avg - mult * std
    return mid, upper, lower


def map_by_ts(ts: np.ndarray, values: np.ndarray) -> dict[int, float]:
    return {int(k): float(v) for k, v in zip(ts, values)}


def net_r(side: str, entry: float, exit_price: float, stop: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    direction = 1.0 if side == "buy" else -1.0
    gross = direction * (exit_price - entry) / risk
    fee_r = entry * FEE_RT_PCT / risk
    return round(gross - fee_r, 4)


def summarize(trades: list[dict]) -> dict:
    wins = [trade for trade in trades if trade["result_r"] > 0]
    losses = [trade for trade in trades if trade["result_r"] <= 0]
    gross_w = sum(trade["result_r"] for trade in wins)
    gross_l = abs(sum(trade["result_r"] for trade in losses))
    holds = [trade["hold_min"] for trade in trades]
    return {
        "n": len(trades),
        "wr": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "pf": round(gross_w / gross_l, 2) if gross_l else 99.0,
        "avg_r": round(sum(trade["result_r"] for trade in trades) / len(trades), 3) if trades else 0.0,
        "avg_hold": round(sum(holds) / len(holds), 1) if holds else 0.0,
        "time_exit_count": sum(1 for trade in trades if trade["outcome"] == "TIME_EXIT"),
        "be_exit_count": sum(1 for trade in trades if trade["outcome"] == "BE_EXIT"),
    }


def save_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
