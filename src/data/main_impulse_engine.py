from __future__ import annotations

import asyncio
import math
import os
import signal
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.data.main_impulse_config import (
    exit_slipped,
    load_main_impulse_config,
    notify_chat_ids,
    setup_logger,
    slipped,
)
from src.data.main_impulse_records import write_outcome, write_signal, write_training
from src.data.okx_trade_stream import OKXTradeStream
from src.data.ws_feed import Candle, WSFeed
from src.strategy.signal_contract import ExitRule, FollowRule, SignalContract
from src.utils.telegram import send_message_to


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
    closed: deque[Candle] = field(default_factory=lambda: deque(maxlen=80))
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
    contract: SignalContract
    signal_id: str
    entry_price: float
    stop_price: float
    opened_ms: int
    fee_pct: float
    signal: dict[str, Any]
    closed_candles: list[Candle] = field(default_factory=list)
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    stop_promoted: bool = False

    @property
    def symbol(self) -> str:
        return self.contract.pair

    @property
    def side(self) -> str:
        return self.contract.side

    def initial_r_pct(self) -> float:
        return abs(side_return(self.entry_price, self.stop_price, self.side))

    def mark(self, price: float) -> None:
        ret = side_return(self.entry_price, price, self.side)
        if math.isfinite(ret):
            self.mfe_pct = max(self.mfe_pct, ret)
            self.mae_pct = min(self.mae_pct, ret)

    def stop_hit(self, price: float) -> bool:
        return price <= self.stop_price if self.side == "long" else price >= self.stop_price

    def scaled_target_hit(self, price: float) -> bool:
        if self.contract.exit_rule.type != "scaled":
            return False
        target_pct = float(self.contract.exit_rule.params.get("target_pct", 0.0))
        return side_return(self.entry_price, price, self.side) >= target_pct

    def maybe_promote_stop(self) -> None:
        be_at_r = self.contract.follow.be_at_R
        r_pct = self.initial_r_pct()
        if self.stop_promoted or be_at_r is None or r_pct <= 0:
            return
        if self.mfe_pct >= float(be_at_r) * r_pct:
            self.stop_price = self.entry_price
            self.stop_promoted = True

    def update_with_candle(self, candle: Candle) -> None:
        high, low = float(candle[2]), float(candle[3])
        self.mark(high if self.side == "long" else low)
        self.mark(low if self.side == "long" else high)
        self.closed_candles.append(candle)
        self.maybe_promote_stop()

    def structure_broken(self) -> bool:
        k = int(self.contract.exit_rule.params.get("structure_k") or 2)
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
            "hold_min": hold_min,
            "exit_rule": self.contract.exit_rule.to_dict(),
            "stop_promoted": self.stop_promoted,
        }


class MainImpulseAnalyzer:
    analyzer_id = "main_impulse"
    regime = "TRENDING_IMPULSE"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def analyze(self, state: PairState, ts_ms: int, price: float) -> SignalContract | None:
        side = "long" if price > state.minute.open else "short"
        entry = slipped(price, side, float(self.config["entry_slippage_pct"]))
        stop = self._stop_price(state, side)
        impulse_body_pct = abs(pct_change(state.minute.open, price))
        exit_mode = str(self.config.get("exit_mode", "ride")).lower()
        if exit_mode == "scaled":
            target_pct = max(
                float(self.config["scaled_tp_min_pct"]),
                impulse_body_pct * float(self.config["scaled_tp_body_mult"]),
            )
            exit_rule = ExitRule("scaled", {"target_pct": target_pct, "impulse_body_pct": impulse_body_pct})
        else:
            exit_rule = ExitRule("ride", {"structure_k": int(self.config["structure_k"])})
        return SignalContract(
            pair=state.symbol,
            side=side,  # type: ignore[arg-type]
            entry=entry,
            stop=stop,
            exit_rule=exit_rule,
            max_hold_min=int(self.config["max_hold_min"]),
            follow=FollowRule(
                be_at_R=float(self.config["be_at_R"]),
                trail={"type": "structure", "structure_k": int(self.config["structure_k"])},
            ),
            regime=self.regime,
            analyzer_id=self.analyzer_id,
            snapshot_id=f"{state.symbol}:{ts_ms}",
            ts=iso_from_ms(ts_ms),
            metadata={
                "raw_entry": price,
                "impulse_open": state.minute.open,
                "impulse_high": state.minute.high,
                "impulse_low": state.minute.low,
                "impulse_body_pct": impulse_body_pct,
                "body_ratio": state.body_ratio(),
                "vol_ratio": state.vol_ratio(),
                "trigger_lag_pct": side_return(state.minute.open, price, side),
                "trigger_window_sec": self.config["trigger_window_sec"],
            },
        )

    def _stop_price(self, state: PairState, side: str) -> float:
        buffer_pct = float(self.config["stop_buffer_pct"]) / 100
        if side == "long":
            return state.minute.low * (1 - buffer_pct)
        return state.minute.high * (1 + buffer_pct)


class MainImpulseEngine:
    def __init__(self) -> None:
        self.config = load_main_impulse_config()
        self._assert_paper_only()
        self.logger = setup_logger()
        self.pairs = [str(pair) for pair in self.config["pairs"]]
        self.stop_event = asyncio.Event()
        self.feed = WSFeed(self.pairs, ["candle1m"], self._buffer_size())
        self.trade_stream = OKXTradeStream(self.pairs, self.on_trade, self.logger, self.stop_event)
        self.states = {pair: PairState(pair) for pair in self.pairs}
        self.positions: dict[str, PaperPosition] = {}
        self.analyzer = MainImpulseAnalyzer(self.config)
        self._last_notify = 0.0

    async def run(self) -> None:
        if not self.config.get("enabled", False):
            self.log("disabled in config | set main_impulse.enabled=true to run paper")
            return
        self._install_signal_handlers()
        self.feed.on_candle_close("candle1m", self.on_candle_close)
        await self.feed.warmup()
        self._seed_states()
        self.log(f"started | pairs={len(self.pairs)} paper=true auto_trade=false")
        tasks = [
            asyncio.create_task(self.feed.start()),
            asyncio.create_task(self.trade_stream.run()),
            asyncio.create_task(self.heartbeat_loop()),
        ]
        await self.stop_event.wait()
        await self.shutdown(tasks)

    async def shutdown(self, tasks: list[asyncio.Task]) -> None:
        self.stop_event.set()
        await self.feed.stop()
        await self.trade_stream.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.log("stopped")

    async def on_trade(self, sym: str, ts_ms: int, price: float, size: float) -> None:
        state = self.states.get(sym)
        if state is None or price <= 0:
            return
        self._update_minute(state, ts_ms, price, size)
        pos = self.positions.get(sym)
        if pos:
            await self._mark_position(pos, ts_ms, price)
            return
        await self._maybe_open(state, ts_ms, price)

    async def on_candle_close(self, sym: str, candle: Candle) -> None:
        state = self.states.get(sym)
        if state is None:
            return
        state.closed.append(candle)
        pos = self.positions.get(sym)
        if not pos:
            return
        pos.update_with_candle(candle)
        await self._close_on_candle(pos, candle)

    async def heartbeat_loop(self) -> None:
        interval = int(self.config["heartbeat_interval"])
        while not self.stop_event.is_set():
            await asyncio.sleep(interval)
            cooldowns = sum(1 for st in self.states.values() if st.cooldown_until > time.time())
            self.log(f"heartbeat | pairs={len(self.states)} open={len(self.positions)} cooldowns={cooldowns}")

    async def notify(self, text: str) -> None:
        chats = notify_chat_ids(self.config)
        if not chats:
            return
        await self._throttle_notify()
        for chat_id in chats:
            try:
                await send_message_to(chat_id, text)
            except Exception as exc:
                self.log(f"notify error | chat={chat_id} | {exc}")

    async def _maybe_open(self, state: PairState, ts_ms: int, price: float) -> None:
        if not self._can_open(state, ts_ms, price):
            return
        contract = self.analyzer.analyze(state, ts_ms, price)
        if contract is None:
            return
        self._assert_contract(state, contract)
        signal_id = str(uuid.uuid4())
        signal = {"signal_id": signal_id, **contract.to_dict(), "paper": True, "auto_trade": False}
        write_signal(signal)
        pos = PaperPosition(
            contract=contract,
            signal_id=signal_id,
            entry_price=contract.entry,
            stop_price=contract.stop,
            opened_ms=ts_ms,
            fee_pct=float(self.config["fee_pct"]),
            signal=signal,
        )
        pos.mark(price)
        state.minute.fired = True
        self.positions[state.symbol] = pos
        self.log(f"open | {state.symbol} {contract.side} entry={contract.entry:.8g} stop={contract.stop:.8g}")
        await self.notify(self._fmt_open(pos))

    def _can_open(self, state: PairState, ts_ms: int, price: float) -> bool:
        if state.minute.fired or len(state.closed) < 4:
            return False
        if len(self.positions) >= int(self.config["max_concurrent"]):
            return False
        if state.cooldown_until > time.time() or state.symbol in self.positions:
            return False
        elapsed = (ts_ms - state.minute.minute_ms) / 1000
        if elapsed < 0 or elapsed > float(self.config["trigger_window_sec"]):
            return False
        if price == state.minute.open:
            return False
        return all(
            [
                abs(pct_change(state.minute.open, price)) >= float(self.config["trigger_pct"]),
                state.body_ratio() >= float(self.config["body_ratio_min"]),
                state.vol_ratio() >= float(self.config["vol_ratio_min"]),
            ]
        )

    async def _mark_position(self, pos: PaperPosition, ts_ms: int, price: float) -> None:
        pos.mark(price)
        pos.maybe_promote_stop()
        if pos.stop_hit(price):
            exit_price = exit_slipped(pos.stop_price, pos.side, float(self.config["exit_slippage_pct"]))
            await self._close_position(pos, "sl", exit_price, ts_ms)
        elif pos.scaled_target_hit(price):
            exit_price = exit_slipped(price, pos.side, float(self.config["exit_slippage_pct"]))
            await self._close_position(pos, "scaled_tp", exit_price, ts_ms)
        elif (ts_ms - pos.opened_ms) / 60000 >= float(pos.contract.max_hold_min):
            exit_price = exit_slipped(price, pos.side, float(self.config["exit_slippage_pct"]))
            await self._close_position(pos, "time", exit_price, ts_ms)

    async def _close_on_candle(self, pos: PaperPosition, candle: Candle) -> None:
        high, low, close = float(candle[2]), float(candle[3]), float(candle[4])
        if (pos.side == "long" and low <= pos.stop_price) or (pos.side == "short" and high >= pos.stop_price):
            exit_price = exit_slipped(pos.stop_price, pos.side, float(self.config["exit_slippage_pct"]))
            await self._close_position(pos, "sl", exit_price, candle[0])
        elif pos.contract.exit_rule.type == "ride" and pos.structure_broken():
            exit_price = exit_slipped(close, pos.side, float(self.config["exit_slippage_pct"]))
            await self._close_position(pos, "ride_exit", exit_price, candle[0])
        elif (candle[0] - pos.opened_ms) / 60000 >= float(pos.contract.max_hold_min):
            exit_price = exit_slipped(close, pos.side, float(self.config["exit_slippage_pct"]))
            await self._close_position(pos, "time", exit_price, candle[0])

    async def _close_position(self, pos: PaperPosition, outcome: str, exit_price: float, exit_ms: int) -> None:
        self.positions.pop(pos.symbol, None)
        self.states[pos.symbol].cooldown_until = time.time() + float(self.config["cooldown_sec"])
        row = pos.outcome_row(outcome, exit_price, exit_ms)
        write_outcome(row)
        write_training(pos.signal, row)
        self.log(f"close | {pos.symbol} {outcome} net={row['net_pct']:+.2f}%")
        await self.notify(self._fmt_close(row))

    def _update_minute(self, state: PairState, ts_ms: int, price: float, size: float) -> None:
        minute_ms = ts_ms - ts_ms % 60000
        if state.minute.minute_ms != minute_ms:
            state.minute.reset(minute_ms, price)
        state.minute.update(price, size)

    def _seed_states(self) -> None:
        for pair in self.pairs:
            self.states[pair].closed.clear()
            self.states[pair].closed.extend(self.feed.get_candles(pair, "candle1m", self._buffer_size()))

    def _buffer_size(self) -> int:
        return max(int(self.config["warmup_bars"]), 40)

    def _assert_paper_only(self) -> None:
        env_auto = os.getenv("AUTO_TRADE", "false").strip().lower()
        if env_auto in {"1", "true", "yes"}:
            raise RuntimeError("main_impulse is PAPER ONLY: AUTO_TRADE must be false")
        if self.config.get("auto_trade") or not self.config.get("paper", True):
            raise RuntimeError("main_impulse is PAPER ONLY: config must keep auto_trade=false and paper=true")

    def _assert_contract(self, state: PairState, contract: SignalContract) -> None:
        if contract.pair != state.symbol:
            raise ValueError("contract pair must match routed state")
        if contract.analyzer_id != self.analyzer.analyzer_id:
            raise ValueError("contract analyzer must own the routed decision")
        if contract.pair in self.positions:
            raise ValueError("one pair can only have one main impulse owner until close")

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGINT", "SIGTERM"):
            if not hasattr(signal, sig_name):
                continue
            try:
                loop.add_signal_handler(getattr(signal, sig_name), self.stop_event.set)
            except NotImplementedError:
                signal.signal(getattr(signal, sig_name), lambda *_args: self.stop_event.set())

    async def _throttle_notify(self) -> None:
        gap = time.time() - self._last_notify
        wait = float(self.config["notify_min_interval_sec"]) - gap
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_notify = time.time()

    def _fmt_open(self, pos: PaperPosition) -> str:
        return (
            f"🔵 МАЙН (paper) OPEN | {pos.symbol} | {pos.side.upper()}\n"
            f"entry {pos.entry_price:.8g} | stop {pos.stop_price:.8g}\n"
            f"exit {pos.contract.exit_rule.type} | body "
            f"{pos.signal['metadata']['impulse_body_pct']:.2f}% | vol {pos.signal['metadata']['vol_ratio']:.2f}x"
        )

    def _fmt_close(self, row: dict[str, Any]) -> str:
        return (
            f"🔵 МАЙН (paper) CLOSE | {row['symbol']} | {row['side'].upper()} | {row['outcome']}\n"
            f"net {row['net_pct']:+.2f}% | mfe {row['mfe_pct']:.2f}% | "
            f"mae {row['mae_pct']:.2f}% | hold {row['hold_min']:.1f}m"
        )

    def log(self, message: str) -> None:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.logger.info("[%s] %s", now, message)
