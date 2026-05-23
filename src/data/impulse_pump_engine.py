from __future__ import annotations

import asyncio
import os
import signal
import time
from datetime import datetime, timezone

from src.data.impulse_pump_config import (
    load_impulse_config,
    pump_chat_ids,
    setup_logger,
)
from src.data.impulse_pump_model import PairState, PaperPosition
from src.data.impulse_pump_trading import close_on_candle, mark_position, maybe_open
from src.data.okx_trade_stream import OKXTradeStream
from src.data.ws_feed import Candle, WSFeed
from src.utils.telegram import send_message_to


class ImpulsePumpEngine:
    def __init__(self) -> None:
        self.config = load_impulse_config()
        self._assert_paper_only()
        self.logger = setup_logger()
        self.pairs = [str(pair) for pair in self.config["pairs"]]
        self.stop_event = asyncio.Event()
        self.feed = WSFeed(self.pairs, ["candle1m"], self._buffer_size())
        self.trade_stream = OKXTradeStream(self.pairs, self.on_trade, self.logger, self.stop_event)
        self.states = {pair: PairState(pair) for pair in self.pairs}
        self.positions: dict[str, PaperPosition] = {}
        self._last_notify = 0.0

    async def run(self) -> None:
        if not self.config.get("enabled", False):
            self.log("disabled in config | set impulse_pump.enabled=true to run paper")
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
            await mark_position(self, pos, ts_ms, price)
            return
        await maybe_open(self, state, ts_ms, price)

    async def on_candle_close(self, sym: str, candle: Candle) -> None:
        state = self.states.get(sym)
        if state is None:
            return
        state.closed.append(candle)
        pos = self.positions.get(sym)
        if not pos:
            return
        pos.update_with_candle(candle)
        await close_on_candle(self, pos, candle)

    async def heartbeat_loop(self) -> None:
        interval = int(self.config["heartbeat_interval"])
        while not self.stop_event.is_set():
            await asyncio.sleep(interval)
            cooldowns = sum(1 for st in self.states.values() if st.cooldown_until > time.time())
            self.log(f"heartbeat | pairs={len(self.states)} open={len(self.positions)} cooldowns={cooldowns}")

    async def notify(self, text: str) -> None:
        chats = pump_chat_ids(self.config)
        if not chats:
            return
        await self._throttle_notify()
        for chat_id in chats:
            try:
                await send_message_to(chat_id, text)
            except Exception as exc:
                self.log(f"notify error | chat={chat_id} | {exc}")

    async def _throttle_notify(self) -> None:
        gap = time.time() - self._last_notify
        wait = float(self.config["notify_min_interval_sec"]) - gap
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_notify = time.time()

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
            raise RuntimeError("impulse_pump is PAPER ONLY: AUTO_TRADE must be false")
        if self.config.get("auto_trade") or not self.config.get("paper", True):
            raise RuntimeError("impulse_pump is PAPER ONLY: config must keep auto_trade=false and paper=true")

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGINT", "SIGTERM"):
            if not hasattr(signal, sig_name):
                continue
            try:
                loop.add_signal_handler(getattr(signal, sig_name), self.stop_event.set)
            except NotImplementedError:
                signal.signal(getattr(signal, sig_name), lambda *_args: self.stop_event.set())

    def log(self, message: str) -> None:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.logger.info("[%s] %s", now, message)
