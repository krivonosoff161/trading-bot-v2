"""
ws_pump_scanner.py — детектор памп/дамп сигналов через OKX WebSocket

Алгоритм:
  При каждом закрытии 1m свечи (confirm=1) проверяем:
  1. Vol spike:   vol_current >= vol_mult × avg(vol, lookback свечей назад)
  2. Price move:  |price_pct| >= порог за lookback свечей

  Оба условия должны выполняться одновременно.

Запуск:
  python scripts/ws/ws_pump_scanner.py
  python scripts/ws/ws_pump_scanner.py --pairs AI-USDT-SWAP LAB-USDT-SWAP PEPE-USDT-SWAP
  python scripts/ws/ws_pump_scanner.py --vol_mult 3 --price_pct 2.0 --lookback 10
  python scripts/ws/ws_pump_scanner.py --duration 300   (0 = бесконечно)
"""

import argparse
import asyncio
import io
import json
import sys
import time
from collections import defaultdict, deque
from datetime import datetime

import aiohttp

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

WS_PUBLIC_URL   = "wss://ws.okx.com:8443/ws/v5/public"
WS_BUSINESS_URL = "wss://ws.okx.com:8443/ws/v5/business"
REST_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"

DEFAULT_PAIRS = [
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "DOGE-USDT-SWAP",
    "XRP-USDT-SWAP",
    "AI-USDT-SWAP",
    "LAB-USDT-SWAP",
    "PEPE-USDT-SWAP",
    "PENGU-USDT-SWAP",
    "WIF-USDT-SWAP",
    "BONK-USDT-SWAP",
]

CANDLE_TF      = "1m"
ALERT_COOLDOWN = 120  # секунд — не дублировать алерт по одной паре чаще 2 мин


def _now() -> str:
    return datetime.utcnow().strftime("%H:%M:%S")


class PumpScanner:
    def __init__(self, pairs: list[str], vol_mult: float, price_pct: float, lookback: int, debug: bool = False):
        self.pairs     = pairs
        self.vol_mult  = vol_mult
        self.price_pct = price_pct
        self.lookback  = lookback
        self.debug     = debug

        self.session  = None
        self.ws_pub   = None
        self.ws_biz   = None

        # закрытые свечи per pair: tuple(ts_ms, open, high, low, close, vol)
        self.candles: dict[str, deque] = {
            p: deque(maxlen=lookback + 10) for p in pairs
        }
        self.last_alert: dict[str, float] = defaultdict(float)
        self.alert_count = 0

    # ── connect / disconnect ─────────────────────────────────────────────────

    async def connect(self) -> bool:
        try:
            self.session = aiohttp.ClientSession()
            self.ws_pub = await self.session.ws_connect(
                WS_PUBLIC_URL, heartbeat=20, timeout=aiohttp.ClientTimeout(total=30)
            )
            self.ws_biz = await self.session.ws_connect(
                WS_BUSINESS_URL, heartbeat=20, timeout=aiohttp.ClientTimeout(total=30)
            )
            print(f"[{_now()}] Connected  public + business")
            return True
        except Exception as e:
            print(f"[{_now()}] ERROR connect: {e}")
            return False

    async def disconnect(self):
        for ws in (self.ws_pub, self.ws_biz):
            if ws:
                await ws.close()
        if self.session:
            await self.session.close()

    # ── REST warmup ──────────────────────────────────────────────────────────

    async def warmup(self):
        """Fetch closed 1m candles via REST to pre-populate history."""
        limit = self.lookback + 5
        ok, fail = 0, 0
        async with aiohttp.ClientSession() as s:
            tasks = [self._fetch_candles(s, sym, limit) for sym in self.pairs]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for sym, res in zip(self.pairs, results):
            if isinstance(res, Exception):
                print(f"[{_now()}] warmup FAIL {sym}: {res}")
                fail += 1
            else:
                ok += 1
        print(f"[{_now()}] Warmup done  ok={ok}  fail={fail}  history={self.lookback+5} candles each")

    async def _fetch_candles(self, session: aiohttp.ClientSession, sym: str, limit: int):
        params = {"instId": sym, "bar": CANDLE_TF, "limit": str(limit)}
        async with session.get(REST_CANDLES_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            body = await r.json(content_type=None)
        if body.get("code") != "0":
            raise ValueError(body.get("msg", "bad code"))
        rows = body.get("data", [])
        # REST returns newest-first; keep only confirmed (confirm==1), oldest first
        closed = [row for row in rows if len(row) > 8 and row[8] == "1"]
        closed.reverse()
        for row in closed:
            candle = (int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]))
            self.candles[sym].append(candle)

    # ── subscriptions ────────────────────────────────────────────────────────

    async def subscribe(self):
        pub_args = [{"channel": "tickers", "instId": p} for p in self.pairs]
        biz_args = [{"channel": f"candle{CANDLE_TF}", "instId": p} for p in self.pairs]
        await self.ws_pub.send_str(json.dumps({"op": "subscribe", "args": pub_args}))
        await self.ws_biz.send_str(json.dumps({"op": "subscribe", "args": biz_args}))
        print(f"[{_now()}] Subscribed {len(self.pairs)} pairs  tickers + candle{CANDLE_TF}")

    # ── message handling ─────────────────────────────────────────────────────

    async def _handle(self, raw: str):
        try:
            msg = json.loads(raw)
        except Exception:
            return

        if msg.get("event") == "error":
            print(f"[{_now()}] WS ERROR: {msg.get('msg')}")
            return
        if msg.get("event") == "subscribe":
            return

        ch   = msg.get("arg", {}).get("channel", "")
        sym  = msg.get("arg", {}).get("instId", "")
        data = msg.get("data", [])
        if not data or sym not in self.candles:
            return

        if ch.startswith("candle"):
            for row in data:
                # row: [ts, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
                if len(row) < 9:
                    continue
                if row[8] != "1":   # только закрытые свечи
                    continue
                candle = (
                    int(row[0]),    # ts ms
                    float(row[1]),  # open
                    float(row[2]),  # high
                    float(row[3]),  # low
                    float(row[4]),  # close
                    float(row[5]),  # vol (contracts)
                )
                self.candles[sym].append(candle)
                self._check_signal(sym)

    def _check_signal(self, sym: str):
        hist = list(self.candles[sym])
        if len(hist) < self.lookback + 1:
            return

        current  = hist[-1]
        previous = hist[-self.lookback - 1 : -1]  # lookback свечей до текущей

        avg_vol = sum(c[5] for c in previous) / self.lookback
        if avg_vol == 0:
            return

        vol_ratio  = current[5] / avg_vol
        price_now  = current[4]                  # close только что закрытой
        price_then = previous[0][4]              # close свечи lookback минут назад
        pct        = (price_now - price_then) / price_then * 100 if price_then else 0

        if self.debug:
            sign = "+" if pct >= 0 else ""
            print(f"  [{_now()}] {sym:20s}  V={vol_ratio:.2f}x  P={sign}{pct:.3f}%")

        if vol_ratio < self.vol_mult or abs(pct) < self.price_pct:
            return

        now = time.time()
        if now - self.last_alert[sym] < ALERT_COOLDOWN:
            return
        self.last_alert[sym] = now
        self.alert_count += 1
        self._print_alert(sym, pct, vol_ratio, current)

    def _print_alert(self, sym: str, pct: float, vol_ratio: float, candle: tuple):
        direction = "PUMP" if pct > 0 else "DUMP"
        sign      = "+" if pct > 0 else ""
        ts_str    = datetime.utcfromtimestamp(candle[0] / 1000).strftime("%H:%M")
        print()
        print("=" * 58)
        print(f"  *** {direction} ***   {sym}   @ {ts_str} UTC")
        print(f"  Price:     {candle[4]:.6g}")
        print(f"  Move:      {sign}{pct:.2f}%  za {self.lookback}m")
        print(f"  Vol ratio: {vol_ratio:.1f}x  (porog {self.vol_mult}x)")
        print(f"  OHLCV:     O={candle[1]:.6g}  H={candle[2]:.6g}  L={candle[3]:.6g}  C={candle[4]:.6g}  V={candle[5]:.0f}")
        print("=" * 58)

    # ── drain loops ──────────────────────────────────────────────────────────

    async def _drain(self, ws, label: str, stop: asyncio.Event):
        try:
            async for msg in ws:
                if stop.is_set():
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle(msg.data)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    print(f"[{_now()}] {label} closed: {msg.type}")
                    break
        except Exception as e:
            print(f"[{_now()}] {label} error: {e}")

    async def run(self, duration: int):
        stop = asyncio.Event()
        if duration > 0:
            asyncio.get_event_loop().call_later(duration, stop.set)
        await asyncio.gather(
            self._drain(self.ws_pub, "public",   stop),
            self._drain(self.ws_biz, "business", stop),
        )
        print(f"\n[{_now()}] Stopped. Total alerts: {self.alert_count}")


# ── entry point ───────────────────────────────────────────────────────────────

async def main(pairs, vol_mult, price_pct, lookback, duration, debug=False):
    print("=" * 58)
    print("  OKX Pump Scanner")
    print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  Pairs:     {len(pairs)}  {pairs}")
    print(f"  Threshold: vol >= {vol_mult}x avg  |  |price| >= {price_pct}%  za {lookback}m")
    print(f"  Duration:  {'infinite' if duration == 0 else str(duration) + 's'}")
    print("=" * 58)

    scanner = PumpScanner(pairs, vol_mult, price_pct, lookback, debug)

    if not await scanner.connect():
        sys.exit(1)

    print(f"\n[{_now()}] Warmup: loading {lookback+5} candles per pair via REST...")
    await scanner.warmup()

    await scanner.subscribe()
    print(f"\n[{_now()}] Listening...  signals ready immediately\n")
    await scanner.run(duration)
    await scanner.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs",     nargs="+", default=DEFAULT_PAIRS)
    parser.add_argument("--vol_mult",  type=float, default=3.0,  help="volume spike multiplier")
    parser.add_argument("--price_pct", type=float, default=2.0,  help="min price move %% over lookback candles")
    parser.add_argument("--lookback",  type=int,   default=10,   help="candles for baseline vol + price reference")
    parser.add_argument("--duration",  type=int,   default=0,    help="seconds to run (0 = infinite)")
    parser.add_argument("--debug",     action="store_true",       help="print every candle close")
    args = parser.parse_args()
    asyncio.run(main(args.pairs, args.vol_mult, args.price_pct, args.lookback, args.duration, args.debug))
