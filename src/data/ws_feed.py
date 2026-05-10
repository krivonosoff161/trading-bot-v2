"""Central OKX WebSocket candle feed for the core scanner pairs."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from datetime import datetime
from typing import Awaitable, Callable

import aiohttp
import pandas as pd

WS_BUSINESS_URL = "wss://ws.okx.com:8443/ws/v5/business"
REST_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"
REST_HISTORY_CANDLES_URL = "https://www.okx.com/api/v5/market/history-candles"

Candle = tuple[int, float, float, float, float, float, float]  # ts,o,h,l,c,vol_contracts,vol_usdt
Callback = Callable[[str, Candle], Awaitable[None] | None]


def _now() -> str:
    return datetime.utcnow().strftime("%H:%M:%S")


def _chunked(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class WSFeed:
    BAR_BUFFER_SIZES = {
        "candle5m": 180,
        "candle15m": 120,
        "candle1H": 100,
        "candle4H": 80,
    }
    PAIRS = [
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
        "SOL-USDT-SWAP",
        "XRP-USDT-SWAP",
        "DOGE-USDT-SWAP",
    ]
    BARS = ["candle1m", "candle5m", "candle15m", "candle1H", "candle4H"]

    def __init__(
        self,
        pairs: list[str] | None = None,
        bars: list[str] | None = None,
        buffer_size: int = 120,
        reconnect_delays: list[int] | None = None,
    ) -> None:
        self.pairs = list(pairs if pairs is not None else self.PAIRS)
        self.bars = list(bars if bars is not None else self.BARS)
        self.buffer_size = int(buffer_size)
        self.reconnect_delays = list(reconnect_delays or [2, 4, 8, 16, 32, 60])
        self.buffers: dict[str, dict[str, deque[Candle]]] = {
            sym: self._make_bar_buffers() for sym in self.pairs
        }
        self.callbacks: dict[str, list[Callback]] = defaultdict(list)
        self.update_callbacks: dict[str, list[Callback]] = defaultdict(list)
        self.last_close_ts: dict[str, dict[str, int]] = defaultdict(dict)
        self.last_msg_ts: dict[str, pd.Timestamp] = {}
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.stop_event = asyncio.Event()
        self.connected_event = asyncio.Event()

    async def start(self) -> None:
        attempts = 0
        while not self.stop_event.is_set():
            ok = await self._connect()
            if not ok:
                delay = self.reconnect_delays[min(attempts, len(self.reconnect_delays) - 1)]
                print(f"[{_now()}] WSFeed connect failed, retry in {delay}s")
                await asyncio.sleep(delay)
                attempts += 1
                continue
            attempts = 0
            try:
                await self._subscribe()
                await self._drain()
            finally:
                self.connected_event.clear()
                await self._close_ws()
            if self.stop_event.is_set():
                break
            delay = self.reconnect_delays[min(attempts, len(self.reconnect_delays) - 1)]
            print(f"[{_now()}] WSFeed reconnect in {delay}s")
            await asyncio.sleep(delay)
            attempts += 1

    async def stop(self) -> None:
        self.stop_event.set()
        await self._close_ws()
        if self.session and not self.session.closed:
            await self.session.close()

    async def warmup(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        ok = 0
        fail = 0
        for sym in self.pairs:
            for bar in self.bars:
                try:
                    rows = await self._rest_candles(sym, bar.replace("candle", ""), self._warmup_limit_for_bar(bar))
                    closed = [row for row in rows if len(row) > 8 and row[8] == "1"]
                    closed.reverse()
                    buf = self.buffers[sym][bar]
                    buf.clear()
                    for row in closed:
                        candle = self._parse_candle(row)
                        buf.append(candle)
                        self.last_close_ts[sym][bar] = candle[0]
                    ok += 1
                except Exception as exc:
                    print(f"[{_now()}] Warmup failed {sym} {bar}: {exc}")
                    fail += 1
                await asyncio.sleep(0.05)
        print(f"[{_now()}] WSFeed warmup done ok={ok} fail={fail}")

    def get_candles(self, sym: str, bar: str, n: int) -> list[Candle]:
        return list(self.buffers.get(sym, {}).get(bar, deque()))[-int(n) :]

    def on_candle_close(self, bar: str, callback: Callback) -> None:
        if bar not in self.bars:
            raise ValueError(f"Unsupported bar: {bar}")
        self.callbacks[bar].append(callback)

    def on_candle_update(self, bar: str, callback: Callback) -> None:
        """Called on every live update of an incomplete candle (row[8]=='0')."""
        if bar not in self.bars:
            raise ValueError(f"Unsupported bar: {bar}")
        self.update_callbacks[bar].append(callback)

    def ensure_pair(self, sym: str) -> None:
        if sym in self.buffers:
            return
        self.pairs.append(sym)
        self.buffers[sym] = self._make_bar_buffers()

    def _make_bar_buffers(self) -> dict[str, deque[Candle]]:
        return {bar: deque(maxlen=self._buffer_size_for_bar(bar)) for bar in self.bars}

    def _buffer_size_for_bar(self, bar: str) -> int:
        return int(self.BAR_BUFFER_SIZES.get(bar, self.buffer_size))

    def _warmup_limit_for_bar(self, bar: str) -> int:
        return self._buffer_size_for_bar(bar)

    async def _connect(self) -> bool:
        try:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
            self.ws = await self.session.ws_connect(WS_BUSINESS_URL, heartbeat=20)
            self.connected_event.set()
            print(f"[{_now()}] WSFeed connected business")
            return True
        except Exception as exc:
            print(f"[{_now()}] WSFeed connect error: {exc}")
            await self._close_ws()
            return False

    async def _subscribe(self) -> None:
        if not self.ws:
            return
        args = [{"channel": bar, "instId": sym} for sym in self.pairs for bar in self.bars]
        for chunk in _chunked(args, 45):
            await self.ws.send_str(json.dumps({"op": "subscribe", "args": chunk}))
        print(f"[{_now()}] WSFeed subscribed pairs={len(self.pairs)} streams={len(args)}")

    async def _drain(self) -> None:
        if not self.ws:
            return
        async for msg in self.ws:
            if self.stop_event.is_set():
                return
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._handle(msg.data)
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                print(f"[{_now()}] WSFeed socket closed: {msg.type}")
                return

    async def _handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if msg.get("event") == "error":
            print(f"[{_now()}] WSFeed error event: {msg.get('msg', '')}")
            return
        if msg.get("event") in {"subscribe", "unsubscribe"}:
            return

        arg = msg.get("arg", {})
        bar = arg.get("channel", "")
        sym = arg.get("instId", "")
        if bar not in self.bars or sym not in self.buffers:
            return

        self.last_msg_ts[sym] = pd.Timestamp.utcnow()
        for row in msg.get("data", []):
            if len(row) < 9:
                continue
            candle = self._parse_candle(row)
            if row[8] == "0":
                # Live updating candle — dispatch to update callbacks only
                if self.update_callbacks.get(bar):
                    await self._dispatch_update(bar, sym, candle)
            elif row[8] == "1":
                # Closed candle — add to buffer and dispatch close callbacks
                if self.last_close_ts[sym].get(bar) == candle[0]:
                    continue
                self.last_close_ts[sym][bar] = candle[0]
                self.buffers[sym][bar].append(candle)
                await self._dispatch(bar, sym, candle)

    async def _dispatch(self, bar: str, sym: str, candle: Candle) -> None:
        for callback in self.callbacks.get(bar, []):
            try:
                result = callback(sym, candle)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                print(f"[{_now()}] WSFeed callback error {bar} {sym}: {exc}")

    async def _dispatch_update(self, bar: str, sym: str, candle: Candle) -> None:
        for callback in self.update_callbacks.get(bar, []):
            try:
                result = callback(sym, candle)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                print(f"[{_now()}] WSFeed update callback error {bar} {sym}: {exc}")

    async def _rest_candles(self, sym: str, bar: str, limit: int) -> list:
        assert self.session is not None
        async with self.session.get(
            REST_CANDLES_URL,
            params={"instId": sym, "bar": bar, "limit": str(limit)},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            body = await resp.json(content_type=None)
        if body.get("code") != "0":
            raise RuntimeError(body.get("msg") or f"candles failed for {sym} {bar}")
        return body.get("data", [])

    async def _rest_history(self, sym: str, bar: str, limit: int) -> list:
        assert self.session is not None
        async with self.session.get(
            REST_HISTORY_CANDLES_URL,
            params={"instId": sym, "bar": bar, "limit": str(limit)},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            body = await resp.json(content_type=None)
        if body.get("code") != "0":
            raise RuntimeError(body.get("msg") or f"history-candles failed for {sym} {bar}")
        return body.get("data", [])

    @staticmethod
    def _parse_candle(row: list[str]) -> Candle:
        return (int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[7]))

    async def _close_ws(self) -> None:
        if self.ws and not self.ws.closed:
            await self.ws.close()
        self.ws = None
