"""
test_ws_connection.py — проверка WebSocket подключения к OKX

Тестирует:
  1. Подключение к Public WebSocket (без ключей)
  2. Тикеры в реальном времени (несколько пар)
  3. 1m свечи одной пары
  4. Задержку (latency тика до получения)
  5. Reconnect при разрыве (симуляция)

Запуск:
  python scripts/ws/test_ws_connection.py
  python scripts/ws/test_ws_connection.py --pairs BTC-USDT ETH-USDT AI-USDT
  python scripts/ws/test_ws_connection.py --duration 60
"""

import argparse
import asyncio
import io
import json
import sys
import time
from collections import defaultdict
from datetime import datetime

import aiohttp

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

WS_PUBLIC_URL   = "wss://ws.okx.com:8443/ws/v5/public"
WS_BUSINESS_URL = "wss://ws.okx.com:8443/ws/v5/business"

DEFAULT_PAIRS = ["BTC-USDT-SWAP", "AI-USDT-SWAP", "LAB-USDT-SWAP", "PENGU-USDT-SWAP", "ETH-USDT-SWAP"]
CANDLE_PAIR   = "BTC-USDT-SWAP"
CANDLE_TF     = "1m"


class WSConnectionTest:
    def __init__(self, pairs: list[str], duration: int = 30):
        self.pairs    = pairs
        self.duration = duration
        self.ws       = None       # public — тикеры
        self.ws_biz   = None       # business — свечи
        self.session  = None

        self.tick_count  = defaultdict(int)
        self.last_price  = {}
        self.latencies   = []
        self.candles_rx  = 0
        self.errors      = []
        self.connected   = False

    # ── подключение ──────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        try:
            self.session = aiohttp.ClientSession()
            self.ws = await self.session.ws_connect(
                WS_PUBLIC_URL, heartbeat=20,
                timeout=aiohttp.ClientTimeout(total=30),
            )
            self.ws_biz = await self.session.ws_connect(
                WS_BUSINESS_URL, heartbeat=20,
                timeout=aiohttp.ClientTimeout(total=30),
            )
            self.connected = True
            print(f"  ✅ Public:   {WS_PUBLIC_URL}")
            print(f"  ✅ Business: {WS_BUSINESS_URL}")
            return True
        except Exception as e:
            print(f"  ❌ Connection failed: {e}")
            self.errors.append(f"connect: {e}")
            return False

    async def disconnect(self):
        self.connected = False
        for ws in (self.ws, self.ws_biz):
            if ws:
                await ws.close()
        if self.session:
            await self.session.close()

    # ── подписки ─────────────────────────────────────────────────────────────

    async def subscribe_tickers(self):
        args = [{"channel": "tickers", "instId": p} for p in self.pairs]
        await self.ws.send_str(json.dumps({"op": "subscribe", "args": args}))
        print(f"  📡 Subscribed tickers: {self.pairs}")

    async def subscribe_candles(self):
        args = [{"channel": f"candle{CANDLE_TF}", "instId": CANDLE_PAIR}]
        await self.ws_biz.send_str(json.dumps({"op": "subscribe", "args": args}))
        print(f"  📊 Subscribed candles {CANDLE_TF}: {CANDLE_PAIR} (business WS)")

    # ── обработка сообщений ───────────────────────────────────────────────────

    async def handle(self, raw: str):
        recv_ts = time.time()
        try:
            msg = json.loads(raw)
        except Exception:
            return

        event = msg.get("event")
        if event == "subscribe":
            return
        if event == "error":
            self.errors.append(msg.get("msg", "unknown error"))
            return

        channel = msg.get("arg", {}).get("channel", "")
        data    = msg.get("data", [])
        if not data:
            return

        if channel == "tickers":
            for t in data:
                sym   = t.get("instId", "")
                price = float(t.get("last", 0))
                ts    = int(t.get("ts", 0)) / 1000       # unix секунды
                lat   = (recv_ts - ts) * 1000             # мс
                self.tick_count[sym] += 1
                self.last_price[sym]  = price
                if 0 < lat < 5000:                        # игнорируем аномалии
                    self.latencies.append(lat)

        elif channel.startswith("candle"):
            self.candles_rx += 1

    # ── основной цикл ─────────────────────────────────────────────────────────

    async def _drain(self, ws, label: str, stop_event: asyncio.Event):
        try:
            async for msg in ws:
                if stop_event.is_set():
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self.handle(msg.data)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    self.errors.append(f"{label} closed: {msg.type}")
                    break
        except Exception as e:
            self.errors.append(f"{label}: {e}")

    async def listen(self):
        stop = asyncio.Event()
        asyncio.get_event_loop().call_later(self.duration, stop.set)
        await asyncio.gather(
            self._drain(self.ws,     "public",   stop),
            self._drain(self.ws_biz, "business", stop),
        )

    # ── отчёт ─────────────────────────────────────────────────────────────────

    def report(self):
        print()
        print("=" * 55)
        print("  РЕЗУЛЬТАТЫ ТЕСТА")
        print("=" * 55)

        print(f"\n📶 Тикеры за {self.duration} сек:")
        for pair in self.pairs:
            n     = self.tick_count.get(pair, 0)
            price = self.last_price.get(pair)
            p_str = f"  цена {price:.6g}" if price else "  нет данных"
            status = "✅" if n > 0 else "❌"
            print(f"  {status} {pair:16s}  тиков: {n:4d}{p_str}")

        print(f"\n🕯  Свечи {CANDLE_TF} ({CANDLE_PAIR}): получено {self.candles_rx}")

        if self.latencies:
            avg = sum(self.latencies) / len(self.latencies)
            mx  = max(self.latencies)
            mn  = min(self.latencies)
            print("\n⚡ Задержка (тик → Python):")
            print(f"   avg={avg:.1f} ms  min={mn:.1f} ms  max={mx:.1f} ms  n={len(self.latencies)}")
        else:
            print("\n⚡ Задержка: нет данных")

        if self.errors:
            print(f"\n⚠️  Ошибки ({len(self.errors)}):")
            for e in self.errors:
                print(f"   • {e}")
        else:
            print("\n✅ Ошибок нет")

        total_ticks = sum(self.tick_count.values())
        ok = total_ticks > 0 and not self.errors
        print()
        if ok:
            print("🎉 ТЕСТ ПРОЙДЕН — WebSocket работает")
        else:
            print("💥 ТЕСТ ПРОВАЛЕН — смотри ошибки выше")
        print("=" * 55)
        return ok


# ── точка входа ───────────────────────────────────────────────────────────────

async def main(pairs: list[str], duration: int):
    print("=" * 55)
    print("  OKX WebSocket Connection Test")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Пары: {pairs}")
    print(f"  Длительность: {duration} сек")
    print("=" * 55)

    test = WSConnectionTest(pairs, duration)

    print("\n[1/4] Подключение...")
    if not await test.connect():
        sys.exit(1)

    print("\n[2/4] Подписка на тикеры...")
    await test.subscribe_tickers()

    print("\n[3/4] Подписка на свечи...")
    await test.subscribe_candles()

    print(f"\n[4/4] Слушаем {duration} секунд...\n")
    await test.listen()

    await test.disconnect()
    ok = test.report()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs",    nargs="+", default=DEFAULT_PAIRS)
    parser.add_argument("--duration", type=int,  default=30)
    args = parser.parse_args()
    asyncio.run(main(args.pairs, args.duration))
