from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.data.ws_feed import Candle


def iso_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def pct_change(a: float, b: float) -> float:
    return (b - a) / a * 100 if a > 0 and b > 0 else float("nan")


def side_return(entry: float, price: float, side: str) -> float:
    if entry <= 0 or price <= 0:
        return float("nan")
    return (price - entry) / entry * 100 if side == "long" else (entry - price) / entry * 100


def avg(values: list[float]) -> float:
    vals = [v for v in values if math.isfinite(v)]
    return sum(vals) / len(vals) if vals else float("nan")


def slipped(price: float, side: str, slippage_pct: float) -> float:
    return price * (1 + slippage_pct / 100) if side == "long" else price * (1 - slippage_pct / 100)


def candle_body_pct(candle: Candle) -> float:
    return abs(pct_change(float(candle[1]), float(candle[4])))


def candle_volume(candle: Candle) -> float:
    return float(candle[5] or 0.0)


@dataclass
class MinuteState:
    minute_ms: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    fired: bool = False

    def reset(self, minute_ms: int, price: float) -> None:
        self.minute_ms = minute_ms
        self.open = self.high = self.low = self.close = price
        self.volume = 0.0
        self.fired = False

    def update(self, price: float, size: float) -> None:
        if self.open <= 0:
            self.reset(self.minute_ms, price)
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += max(size, 0.0)


@dataclass
class PairState:
    symbol: str
    closed: deque[Candle] = field(default_factory=lambda: deque(maxlen=40))
    minute: MinuteState = field(default_factory=MinuteState)
    cooldown_until: float = 0.0

    def body_ratio(self) -> float:
        base = avg([candle_body_pct(c) for c in list(self.closed)[-4:]])
        cur = abs(pct_change(self.minute.open, self.minute.close))
        return cur / base if base > 0 else float("nan")

    def vol_ratio(self) -> float:
        base = avg([candle_volume(c) for c in list(self.closed)[-4:]])
        return self.minute.volume / base if base > 0 else float("nan")


@dataclass
class PaperPosition:
    signal_id: str
    symbol: str
    side: str
    entry_price: float
    raw_entry_price: float
    stop_price: float
    opened_ms: int
    impulse_open: float
    impulse_high: float
    impulse_low: float
    structure_k: int
    fee_pct: float
    signal: dict[str, Any]
    closed_candles: list[Candle] = field(default_factory=list)
    mfe_pct: float = 0.0
    mae_pct: float = 0.0

    def mark(self, price: float) -> None:
        ret = side_return(self.entry_price, price, self.side)
        if math.isfinite(ret):
            self.mfe_pct = max(self.mfe_pct, ret)
            self.mae_pct = min(self.mae_pct, ret)

    def stop_hit(self, price: float) -> bool:
        return price <= self.stop_price if self.side == "long" else price >= self.stop_price

    def update_with_candle(self, candle: Candle) -> None:
        high, low = float(candle[2]), float(candle[3])
        self.mark(high if self.side == "long" else low)
        self.mark(low if self.side == "long" else high)
        self.closed_candles.append(candle)

    def structure_broken(self) -> bool:
        k = int(self.structure_k)
        if len(self.closed_candles) <= k:
            return False
        cur = self.closed_candles[-1]
        prev = self.closed_candles[-k - 1 : -1]
        close = float(cur[4])
        if self.side == "long":
            return close < min(float(c[3]) for c in prev)
        return close > max(float(c[2]) for c in prev)

    def outcome_row(self, outcome: str, exit_price: float, exit_ms: int) -> dict[str, Any]:
        gross = side_return(self.entry_price, exit_price, self.side)
        hold_min = (exit_ms - self.opened_ms) / 60000
        capture = max(gross, 0.0) / self.mfe_pct * 100 if self.mfe_pct > 0 else float("nan")
        return {
            "signal_id": self.signal_id,
            "ts": iso_from_ms(exit_ms),
            "symbol": self.symbol,
            "side": self.side,
            "outcome": outcome,
            "exit_price": exit_price,
            "gross_pct": gross,
            "net_pct": gross - self.fee_pct,
            "mfe_pct": self.mfe_pct,
            "mae_pct": self.mae_pct,
            "capture_pct": min(100.0, capture) if math.isfinite(capture) else capture,
            "hold_min": hold_min,
        }
