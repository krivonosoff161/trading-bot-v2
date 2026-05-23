from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp


PUBLIC_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
TradeCallback = Callable[[str, int, float, float], Awaitable[None] | None]


def _chunked(items: list[dict[str, str]], size: int) -> list[list[dict[str, str]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class OKXTradeStream:
    def __init__(
        self,
        pairs: list[str],
        callback: TradeCallback,
        logger: logging.Logger,
        stop_event: asyncio.Event,
    ) -> None:
        self.pairs = pairs
        self.callback = callback
        self.logger = logger
        self.stop_event = stop_event
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None

    async def run(self) -> None:
        delay = 2
        while not self.stop_event.is_set():
            try:
                await self._connect()
                await self._subscribe()
                delay = 2
                await self._drain()
            except Exception as exc:
                self.logger.info("trade_ws error | %s", exc)
            finally:
                await self.close()
            if not self.stop_event.is_set():
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def close(self) -> None:
        if self.ws and not self.ws.closed:
            await self.ws.close()
        self.ws = None
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None

    async def _connect(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        self.ws = await self.session.ws_connect(PUBLIC_WS_URL, heartbeat=20)
        self.logger.info("trade_ws connected")

    async def _subscribe(self) -> None:
        if not self.ws:
            return
        args = [{"channel": "trades", "instId": pair} for pair in self.pairs]
        for chunk in _chunked(args, 45):
            await self.ws.send_str(json.dumps({"op": "subscribe", "args": chunk}))
        self.logger.info("trade_ws subscribed | pairs=%s", len(self.pairs))

    async def _drain(self) -> None:
        if not self.ws:
            return
        async for msg in self.ws:
            if self.stop_event.is_set():
                return
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._handle(msg.data)
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                return

    async def _handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if msg.get("event") in {"subscribe", "unsubscribe"}:
            return
        if msg.get("event") == "error":
            self.logger.info("trade_ws event error | %s", msg.get("msg", ""))
            return
        sym = msg.get("arg", {}).get("instId", "")
        for row in msg.get("data", []):
            await self._dispatch(sym, row)

    async def _dispatch(self, sym: str, row: dict[str, Any]) -> None:
        try:
            ts_ms = int(row["ts"])
            price = float(row["px"])
            size = float(row.get("sz") or 0.0)
        except (KeyError, TypeError, ValueError):
            return
        result = self.callback(sym, ts_ms, price, size)
        if asyncio.iscoroutine(result):
            await result
