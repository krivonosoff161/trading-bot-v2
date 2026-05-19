"""
Core backtest utilities for the OKX WS pump scanner.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / "cache"
ALERT_COOLDOWN_SECONDS = 120


@dataclass(slots=True)
class Signal:
    ts: pd.Timestamp
    sym: str
    direction: str
    entry_price: float
    vol_ratio: float
    pct_move: float
    candle_open: float
    candle_high: float
    candle_low: float
    candle_idx: int | None = None


@dataclass(slots=True)
class ForwardResult:
    signal: Signal
    entry_open_price: float
    returns: dict[int, float]
    directional_returns: dict[int, float]
    max_gain: float
    max_loss: float
    time_to_max_gain: int | None
    reversed: bool


def load_symbol_frame(symbol: str, cache_dir: Path = DEFAULT_CACHE_DIR, days: int = 30) -> pd.DataFrame:
    path = cache_dir / f"{symbol}_1m_{days}d.pkl"
    df = pd.read_pickle(path)
    df = df.sort_index()
    df.attrs["symbol"] = symbol
    return df


def detect_signals(
    df: pd.DataFrame,
    vol_mult: float,
    price_pct: float,
    lookback: int,
    cooldown_seconds: int = ALERT_COOLDOWN_SECONDS,
    sym: str | None = None,
) -> list[Signal]:
    if lookback <= 0:
        raise ValueError("lookback must be positive")

    symbol = sym or df.attrs.get("symbol")
    if not symbol:
        raise ValueError("symbol is required via df.attrs['symbol'] or sym argument")

    required = {"open", "high", "low", "close", "vol"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")

    opens = df["open"].to_numpy(dtype="float64")
    highs = df["high"].to_numpy(dtype="float64")
    lows = df["low"].to_numpy(dtype="float64")
    closes = df["close"].to_numpy(dtype="float64")
    vols = df["vol"].to_numpy(dtype="float64")
    timestamps = df.index.to_list()

    signals: list[Signal] = []
    last_signal_ts: pd.Timestamp | None = None

    for i in range(lookback, len(df)):
        avg_vol = float(np.mean(vols[i - lookback : i]))
        if avg_vol == 0:
            continue

        vol_ratio = float(vols[i] / avg_vol)
        price_now = float(closes[i])
        price_then = float(closes[i - lookback])
        if price_then == 0:
            continue

        pct_move = (price_now - price_then) / price_then * 100.0
        if vol_ratio < vol_mult or abs(pct_move) < price_pct:
            continue

        ts = timestamps[i]
        if last_signal_ts is not None:
            delta_seconds = (ts - last_signal_ts).total_seconds()
            if delta_seconds < cooldown_seconds:
                continue

        last_signal_ts = ts
        signals.append(
            Signal(
                ts=ts,
                sym=symbol,
                direction="PUMP" if pct_move > 0 else "DUMP",
                entry_price=price_now,
                vol_ratio=vol_ratio,
                pct_move=pct_move,
                candle_open=float(opens[i]),
                candle_high=float(highs[i]),
                candle_low=float(lows[i]),
                candle_idx=i,
            )
        )

    return signals


def analyze_forward(
    df: pd.DataFrame,
    signal: Signal,
    horizons: list[int] | None = None,
) -> ForwardResult:
    horizons = horizons or [1, 2, 3, 5, 8, 10, 15, 20, 30]

    signal_idx = signal.candle_idx
    if signal_idx is None:
        signal_idx = df.index.get_loc(signal.ts)
        if isinstance(signal_idx, slice):
            raise ValueError(f"non-unique timestamp for signal {signal.ts}")
    if signal_idx + 1 >= len(df):
        raise ValueError(f"missing entry candle after signal {signal.ts}")

    opens = df["open"].to_numpy(dtype="float64")
    closes = df["close"].to_numpy(dtype="float64")
    entry_open_price = float(opens[signal_idx + 1])
    if entry_open_price == 0:
        raise ValueError(f"zero entry open price after signal {signal.ts}")

    returns: dict[int, float] = {}
    directional_returns: dict[int, float] = {}
    direction_sign = 1.0 if signal.direction == "PUMP" else -1.0

    for horizon in horizons:
        future_idx = signal_idx + horizon
        if future_idx >= len(df):
            continue
        future_close = float(closes[future_idx])
        raw_return = (future_close - entry_open_price) / entry_open_price * 100.0
        returns[horizon] = raw_return
        directional_returns[horizon] = raw_return * direction_sign

    future_closes = closes[signal_idx + 1 : signal_idx + 31]
    if len(future_closes) == 0:
        raise ValueError(f"missing forward window after signal {signal.ts}")

    directional_path = [0.0]
    minute_marks = [0]
    for minute, close_price in enumerate(future_closes, start=1):
        raw_return = (close_price - entry_open_price) / entry_open_price * 100.0
        directional_path.append(raw_return * direction_sign)
        minute_marks.append(minute)

    max_gain = max(directional_path)
    max_loss = min(directional_path)
    time_to_max_gain = minute_marks[directional_path.index(max_gain)]

    reverse_window = future_closes[:15]
    if signal.direction == "PUMP":
        reversed_signal = any(close_price < entry_open_price for close_price in reverse_window)
    else:
        reversed_signal = any(close_price > entry_open_price for close_price in reverse_window)

    return ForwardResult(
        signal=signal,
        entry_open_price=entry_open_price,
        returns=returns,
        directional_returns=directional_returns,
        max_gain=max_gain,
        max_loss=max_loss,
        time_to_max_gain=time_to_max_gain,
        reversed=reversed_signal,
    )


def format_signal(signal: Signal) -> str:
    return (
        f"{signal.ts.strftime('%Y-%m-%d %H:%M:%S %Z')}  "
        f"{signal.sym:16s} {signal.direction:4s}  "
        f"entry={signal.entry_price:.8g}  "
        f"vol={signal.vol_ratio:.2f}x  "
        f"move={signal.pct_move:+.3f}%  "
        f"ohl=({signal.candle_open:.8g}, {signal.candle_high:.8g}, {signal.candle_low:.8g})"
    )


def _slice_last_days(df: pd.DataFrame, days: int) -> pd.DataFrame:
    if days <= 0:
        return df
    cutoff = df.index.max() - pd.Timedelta(days=days)
    sliced = df.loc[df.index >= cutoff].copy()
    sliced.attrs.update(df.attrs)
    return sliced


def _head(items: Iterable[Signal], limit: int) -> list[Signal]:
    out: list[Signal] = []
    for item in items:
        out.append(item)
        if len(out) >= limit:
            break
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC-USDT-SWAP")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--vol_mult", type=float, default=2.0)
    parser.add_argument("--price_pct", type=float, default=1.0)
    parser.add_argument("--lookback", type=int, default=10)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--cache_dir", type=Path, default=DEFAULT_CACHE_DIR)
    args = parser.parse_args()

    df = load_symbol_frame(args.symbol, cache_dir=args.cache_dir)
    df = _slice_last_days(df, args.days)
    signals = detect_signals(
        df=df,
        vol_mult=args.vol_mult,
        price_pct=args.price_pct,
        lookback=args.lookback,
        sym=args.symbol,
    )

    print(
        f"symbol={args.symbol} days={args.days} rows={len(df)} "
        f"vol_mult={args.vol_mult} price_pct={args.price_pct} lookback={args.lookback}"
    )
    print(f"signals={len(signals)}")
    if signals:
        print("first_signals:")
        for signal in _head(signals, args.limit):
            print(format_signal(signal))


if __name__ == "__main__":
    main()
