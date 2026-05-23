from __future__ import annotations

import time
import uuid
from typing import Any

from src.data.impulse_pump_config import exit_slipped, fmt_close, fmt_open
from src.data.impulse_pump_model import (
    PairState,
    PaperPosition,
    iso_from_ms,
    pct_change,
    side_return,
    slipped,
)
from src.data.impulse_pump_records import write_outcome, write_signal, write_training
from src.data.ws_feed import Candle


async def maybe_open(engine: Any, state: PairState, ts_ms: int, price: float) -> None:
    if not can_open(engine, state, ts_ms, price):
        return
    side = "long" if price > state.minute.open else "short"
    entry = slipped(price, side, float(engine.config["entry_slippage_pct"]))
    stop = stop_price(engine, state, side)
    signal = signal_row(engine, state, side, ts_ms, price, entry, stop)
    write_signal(signal)
    pos = PaperPosition(
        signal_id=signal["signal_id"],
        symbol=state.symbol,
        side=side,
        entry_price=entry,
        raw_entry_price=price,
        stop_price=stop,
        opened_ms=ts_ms,
        impulse_open=state.minute.open,
        impulse_high=state.minute.high,
        impulse_low=state.minute.low,
        structure_k=int(engine.config["structure_k"]),
        fee_pct=float(engine.config["fee_pct"]),
        signal=signal,
    )
    pos.mark(price)
    state.minute.fired = True
    engine.positions[state.symbol] = pos
    engine.log(f"open | {state.symbol} {side} entry={entry:.8g} stop={stop:.8g}")
    await engine.notify(fmt_open(pos))


def can_open(engine: Any, state: PairState, ts_ms: int, price: float) -> bool:
    if state.minute.fired or len(state.closed) < 4:
        return False
    if len(engine.positions) >= int(engine.config["max_concurrent"]):
        return False
    if state.cooldown_until > time.time() or state.symbol in engine.positions:
        return False
    elapsed = (ts_ms - state.minute.minute_ms) / 1000
    if elapsed < 0 or elapsed > float(engine.config["trigger_window_sec"]):
        return False
    return passes_impulse(engine, state, price)


def passes_impulse(engine: Any, state: PairState, price: float) -> bool:
    if price == state.minute.open:
        return False
    lag = abs(pct_change(state.minute.open, price))
    return all(
        [
            lag >= float(engine.config["trigger_pct"]),
            state.body_ratio() >= float(engine.config["body_ratio_min"]),
            state.vol_ratio() >= float(engine.config["vol_ratio_min"]),
        ]
    )


async def mark_position(engine: Any, pos: PaperPosition, ts_ms: int, price: float) -> None:
    pos.mark(price)
    if pos.stop_hit(price):
        exit_price = exit_slipped(pos.stop_price, pos.side, float(engine.config["entry_slippage_pct"]))
        await close_position(engine, pos, "sl", exit_price, ts_ms)
        return
    if (ts_ms - pos.opened_ms) / 60000 >= float(engine.config["max_hold_min"]):
        exit_price = exit_slipped(price, pos.side, float(engine.config["entry_slippage_pct"]))
        await close_position(engine, pos, "time", exit_price, ts_ms)


async def close_on_candle(engine: Any, pos: PaperPosition, candle: Candle) -> None:
    high, low, close = float(candle[2]), float(candle[3]), float(candle[4])
    sl_hit = (pos.side == "long" and low <= pos.stop_price) or (
        pos.side == "short" and high >= pos.stop_price
    )
    if sl_hit:
        exit_price = exit_slipped(pos.stop_price, pos.side, float(engine.config["entry_slippage_pct"]))
        await close_position(engine, pos, "sl", exit_price, candle[0])
    elif pos.structure_broken():
        exit_price = exit_slipped(close, pos.side, float(engine.config["entry_slippage_pct"]))
        await close_position(engine, pos, "ride_exit", exit_price, candle[0])
    elif (candle[0] - pos.opened_ms) / 60000 >= float(engine.config["max_hold_min"]):
        exit_price = exit_slipped(close, pos.side, float(engine.config["entry_slippage_pct"]))
        await close_position(engine, pos, "time", exit_price, candle[0])


async def close_position(engine: Any, pos: PaperPosition, outcome: str, exit_price: float, exit_ms: int) -> None:
    engine.positions.pop(pos.symbol, None)
    engine.states[pos.symbol].cooldown_until = time.time() + float(engine.config["cooldown_sec"])
    row = pos.outcome_row(outcome, exit_price, exit_ms)
    write_outcome(row)
    write_training(pos.signal, row)
    engine.log(f"close | {pos.symbol} {outcome} net={row['net_pct']:+.2f}%")
    await engine.notify(fmt_close(row))


def signal_row(
    engine: Any, state: PairState, side: str, ts_ms: int, price: float, entry: float, stop: float
) -> dict[str, Any]:
    lag = side_return(state.minute.open, price, side)
    return {
        "signal_id": str(uuid.uuid4()),
        "ts": iso_from_ms(ts_ms),
        "symbol": state.symbol,
        "side": side,
        "vol_tier": engine.config["vol_tier"], "paper": True, "auto_trade": False,
        "entry_price": entry,
        "raw_entry_price": price,
        "stop_price": stop,
        "impulse_body_pct": abs(pct_change(state.minute.open, price)),
        "body_ratio": state.body_ratio(),
        "vol_ratio": state.vol_ratio(),
        "trigger_window_sec": engine.config["trigger_window_sec"],
        "entry_lag_pct": lag,
        "adx_1h": None,
        "price": price,
        "base_distance": abs(pct_change(entry, stop)),
        "impulse_open": state.minute.open, "impulse_high": state.minute.high, "impulse_low": state.minute.low,
        "structure_k": engine.config["structure_k"],
        "fee_pct": engine.config["fee_pct"],
        "slippage_pct": engine.config["entry_slippage_pct"],
        "config_version": "impulse_pump_2026-05-23",
    }


def stop_price(engine: Any, state: PairState, side: str) -> float:
    buffer_pct = float(engine.config["stop_buffer_pct"]) / 100
    if side == "long":
        return state.minute.low * (1 - buffer_pct)
    return state.minute.high * (1 + buffer_pct)
