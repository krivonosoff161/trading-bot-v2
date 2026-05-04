"""
Production-ready OKX WS pump monitor with paper-trading logs.

This engine performs live monitoring only:
- public/business WebSocket connectivity
- dynamic universe refresh
- calibrated filter stack A+B+C+D+E
- structured signal logging
- paper trading with TP/SL labels

No real trading or order placement is implemented here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import pickle
import signal
import sys
import time
import traceback
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bt_pump_core import Signal, detect_signals
from bt_pump_filters import fetch_ctvals


WS_PUBLIC_URL = "wss://ws.okx.com:8443/ws/v5/public"
WS_BUSINESS_URL = "wss://ws.okx.com:8443/ws/v5/business"
REST_HISTORY_CANDLES_URL = "https://www.okx.com/api/v5/market/history-candles"
REST_TICKERS_URL = "https://www.okx.com/api/v5/market/tickers"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = _PROJECT_ROOT / "logs" / "pump"
SIGNALS_LOG = LOG_DIR / "pump_signals.jsonl"
LABELS_LOG = LOG_DIR / "pump_labels.jsonl"
ENGINE_LOG = LOG_DIR / "ws_pump_engine.log"
CACHE_DIR = Path(__file__).resolve().parent / "cache"
STATE_PATH = CACHE_DIR / "engine_state.pkl"


DEFAULT_CONFIG = {
    "vol_mult": 2.0,
    "price_pct": 2.0,
    "lookback": 15,
    "alert_cooldown_sec": 120,
    "min_usd_vol": 50_000,
    "active_hours": [18, 19, 22],
    "vol_sustain_bars": 3,
    "vol_sustain_ratio": 0.8,
    "max_pairs": 14,
    "min_market_vol_24h": 10_000_000,
    "universe_refresh_hours": 1,
    "paper_tp_pct": 0.5,
    "paper_sl_pct": 0.3,
    "paper_max_hold_min": 15,
    "paper_position_usd": 100.0,
    "paper_balance_usd": 1000.0,
    "max_open_positions": 3,
    "fee_rt_pct": 0.10,
    "telegram_enabled": False,
    "heartbeat_interval": 30,
    "state_ttl_min": 10,
    "reconnect_delays": [2, 4, 8, 16, 32, 60],
}


def _load_pump_config() -> dict:
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    try:
        import yaml

        with config_path.open(encoding="utf-8") as f:
            project = yaml.safe_load(f) or {}
        cfg = DEFAULT_CONFIG.copy()
        cfg.update(project.get("pump_engine", {}) or {})
        return cfg
    except Exception:
        return DEFAULT_CONFIG.copy()


@dataclass(slots=True)
class PendingSignal:
    signal: Signal
    baseline_avg_vol: float
    candle_vol_usd: float
    screener_tier: int
    latency_ms: int
    future_vols: list[float] = field(default_factory=list)
    bars_seen: int = 0
    confirm_passed: bool = False
    entry_open_price: float = 0.0
    signal_id: str = ""


@dataclass(slots=True)
class OpenPosition:
    signal_id: str
    sym: str
    entry_price: float
    sl_price: float
    tp_price: float
    opened_at: pd.Timestamp
    position_usd: float


def _now() -> str:
    return datetime.utcnow().strftime("%H:%M:%S")


def _setup_rotating_log() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        ENGINE_LOG,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    class _StreamToHandler:
        def __init__(self, stream_handler: logging.Handler):
            self._handler = stream_handler
            self._record = logging.LogRecord("pump_engine", logging.INFO, "", 0, "", (), None)

        def write(self, msg: str) -> None:
            msg = msg.rstrip()
            if msg:
                self._record.msg = msg
                self._handler.emit(self._record)

        def flush(self) -> None:
            self._handler.flush()

    sys.stdout = _StreamToHandler(handler)
    sys.stderr = _StreamToHandler(handler)


def _format_exception(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()


def _ts_utc(dt: pd.Timestamp) -> str:
    if dt.tzinfo is None:
        return dt.tz_localize("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _is_excluded_symbol(inst_id: str) -> tuple[bool, str]:
    if not inst_id.endswith("-USDT-SWAP"):
        return True, "not_usdt_swap"
    upper = inst_id.upper()
    if any(token in upper for token in ("UP-USDT", "DOWN-USDT", "3L-USDT", "3S-USDT")):
        return True, "leveraged_token"
    if inst_id.count("-") > 2:
        return True, "exotic_name"
    return False, ""


async def _rest_candles(s: aiohttp.ClientSession, sym: str, bar: str, limit: int) -> list:
    async with s.get(
        REST_HISTORY_CANDLES_URL,
        params={"instId": sym, "bar": bar, "limit": str(limit)},
        timeout=aiohttp.ClientTimeout(total=8),
    ) as r:
        body = await r.json(content_type=None)
    return body.get("data", []) if body.get("code") == "0" else []


async def _rest_funding(s: aiohttp.ClientSession, sym: str) -> dict:
    url = "https://www.okx.com/api/v5/public/funding-rate"
    async with s.get(url, params={"instId": sym}, timeout=aiohttp.ClientTimeout(total=5)) as r:
        body = await r.json(content_type=None)
    data = body.get("data", [{}])
    return data[0] if data and body.get("code") == "0" else {}


async def _rest_oi(s: aiohttp.ClientSession, sym: str) -> list:
    ccy = sym.split("-")[0]
    url = "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume"
    async with s.get(
        url,
        params={"ccy": ccy, "period": "1H", "limit": "5"},
        timeout=aiohttp.ClientTimeout(total=5),
    ) as r:
        body = await r.json(content_type=None)
    return body.get("data", []) if body.get("code") == "0" else []


def _parse_hlc(raw: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    data = sorted(raw, key=lambda row: int(row[0]))
    highs = np.array([float(row[2]) for row in data], dtype="float64")
    lows = np.array([float(row[3]) for row in data], dtype="float64")
    closes = np.array([float(row[4]) for row in data], dtype="float64")
    return highs, lows, closes


def _wilder_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 9) -> tuple[float, float, float]:
    n = len(closes)
    if n < period + 1:
        return 0.0, 0.0, 0.0

    tr = np.zeros(n, dtype="float64")
    pdm = np.zeros(n, dtype="float64")
    mdm = np.zeros(n, dtype="float64")
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hpc = abs(highs[i] - closes[i - 1])
        lpc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hpc, lpc)
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        pdm[i] = up if up > dn and up > 0 else 0.0
        mdm[i] = dn if dn > up and dn > 0 else 0.0

    atr = np.zeros(n, dtype="float64")
    pdi_arr = np.zeros(n, dtype="float64")
    mdi_arr = np.zeros(n, dtype="float64")
    atr[period] = tr[1 : period + 1].sum()
    pdi_arr[period] = pdm[1 : period + 1].sum()
    mdi_arr[period] = mdm[1 : period + 1].sum()

    for i in range(period + 1, n):
        atr[i] = atr[i - 1] - atr[i - 1] / period + tr[i]
        pdi_arr[i] = pdi_arr[i - 1] - pdi_arr[i - 1] / period + pdm[i]
        mdi_arr[i] = mdi_arr[i - 1] - mdi_arr[i - 1] / period + mdm[i]

    dx = np.zeros(n, dtype="float64")
    for i in range(period, n):
        if atr[i] > 0:
            pdi = pdi_arr[i] / atr[i] * 100
            mdi = mdi_arr[i] / atr[i] * 100
            denom = pdi + mdi
            dx[i] = abs(pdi - mdi) / denom * 100 if denom > 0 else 0.0

    adx_arr = np.zeros(n, dtype="float64")
    if 2 * period < n:
        adx_arr[2 * period] = dx[period : 2 * period + 1].mean()
    for i in range(2 * period + 1, n):
        adx_arr[i] = (adx_arr[i - 1] * (period - 1) + dx[i]) / period

    last_atr = atr[-1]
    pdi_last = pdi_arr[-1] / last_atr * 100 if last_atr > 0 else 0.0
    mdi_last = mdi_arr[-1] / last_atr * 100 if last_atr > 0 else 0.0
    return float(adx_arr[-1]), float(pdi_last), float(mdi_last)


def _wilder_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 9) -> float:
    n = len(closes)
    if n < 2:
        return 0.0
    tr = np.array(
        [
            max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            for i in range(1, n)
        ],
        dtype="float64",
    )
    seed = tr[:period] if len(tr) >= period else tr
    if len(seed) == 0:
        return 0.0
    atr = float(seed.mean())
    for value in tr[period:]:
        atr = (atr * (period - 1) + float(value)) / period
    return float(atr)


def _calc_ema(closes: np.ndarray, period: int) -> float:
    if len(closes) < period:
        return 0.0
    k = 2.0 / (period + 1)
    ema = float(closes[:period].mean())
    for close in closes[period:]:
        ema = float(close) * k + ema * (1 - k)
    return ema


def _ema_bias(closes: np.ndarray, fast: int, slow: int) -> str:
    e_fast = _calc_ema(closes, fast)
    e_slow = _calc_ema(closes, slow)
    if e_fast > e_slow > 0:
        return "UP"
    if e_fast < e_slow:
        return "DOWN"
    return "NEUTRAL"


def _intraday_vwap(raw_1h: list) -> float | None:
    today_ms = int(datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    day_rows = [row for row in raw_1h if int(row[0]) >= today_ms]
    if not day_rows:
        return None
    total_volume = sum(float(row[5]) for row in day_rows)
    if total_volume <= 0:
        return None
    vwap = sum(float(row[4]) * float(row[5]) for row in day_rows) / total_volume
    return round(vwap, 6)


class PumpEngine:
    def __init__(self, config: dict, debug: bool = False, fixed_pairs: list[str] | None = None, no_filter: bool = False):
        self.config = dict(config)
        self.debug = debug
        self.fixed_pairs = list(dict.fromkeys(fixed_pairs or [])) or None
        self.no_filter = no_filter

        self.session: aiohttp.ClientSession | None = None
        self.ws_pub: aiohttp.ClientWebSocketResponse | None = None
        self.ws_biz: aiohttp.ClientWebSocketResponse | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

        self.universe: list[str] = list(self.fixed_pairs or [])
        self.screener_tier: dict[str, int] = {sym: 0 for sym in self.universe}
        self.candles: dict[str, deque[tuple[int, float, float, float, float, float]]] = {}
        self.pending_signals: dict[str, list[PendingSignal]] = defaultdict(list)
        self.last_tick_ts: dict[str, pd.Timestamp] = {}
        self.last_signal_wall: dict[str, float] = defaultdict(float)
        self.ctval: dict[str, float] = {}
        self.open_positions: dict[str, OpenPosition] = {}

        self.tg_token = os.getenv("PUMP_TG_TOKEN", "")
        self.tg_chat_id = os.getenv("PUMP_TG_CHAT_ID", "")
        self.tg_enabled = bool(self.config.get("telegram_enabled", False)) and bool(self.tg_token) and bool(self.tg_chat_id)

        self.stop_event = asyncio.Event()
        self.reconnect_event = asyncio.Event()
        self.state_restored = False

        self._install_pair_buffers(self.universe)

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._install_signal_handlers()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        await self._load_ctvals()
        self._restore_state()
        self._print_startup_config()

        try:
            if not await self._establish_connection_cycle(use_restored_state=self.state_restored):
                return

            while not self.stop_event.is_set():
                self.reconnect_event.clear()
                tasks = [
                    asyncio.create_task(self._drain_pub(), name="drain_pub"),
                    asyncio.create_task(self._drain_biz(), name="drain_biz"),
                    asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
                    asyncio.create_task(self._persist_loop(), name="persist_loop"),
                    asyncio.create_task(self._daily_summary_loop(), name="daily_summary"),
                ]
                if not self.fixed_pairs:
                    tasks.append(asyncio.create_task(self._universe_loop(), name="universe_loop"))

                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

                if not self.stop_event.is_set():
                    for task in done:
                        if task.cancelled():
                            continue
                        exc = task.exception()
                        if exc:
                            print(f"[{_now()}] Loop error in {task.get_name()}:\n{_format_exception(exc)}")
                    self.reconnect_event.set()

                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await self._close_connections()

                if self.stop_event.is_set():
                    break

                if not await self._reconnect():
                    break
        finally:
            await self._shutdown()

    async def _connect(self) -> bool:
        try:
            if self.session is None or self.session.closed:
                timeout = aiohttp.ClientTimeout(total=30)
                self.session = aiohttp.ClientSession(timeout=timeout)
            self.ws_pub = await self.session.ws_connect(WS_PUBLIC_URL, heartbeat=20)
            self.ws_biz = await self.session.ws_connect(WS_BUSINESS_URL, heartbeat=20)
            print(f"[{_now()}] CONNECTED public+business")
            return True
        except Exception as exc:
            print(f"[{_now()}] CONNECT failed:\n{_format_exception(exc)}")
            await self._close_connections()
            return False

    async def _reconnect(self) -> bool:
        delays = list(self.config["reconnect_delays"])
        attempts = 0
        while not self.stop_event.is_set():
            wait_seconds = delays[min(attempts, len(delays) - 1)]
            print(f"[{_now()}] RECONNECT attempt {attempts + 1}, wait {wait_seconds}s")
            asyncio.create_task(self._send_tg(f"ENGINE reconnect attempt {attempts + 1}, wait {wait_seconds}s"))
            await asyncio.sleep(wait_seconds)
            ok = await self._establish_connection_cycle(use_restored_state=False)
            if ok:
                asyncio.create_task(self._send_tg("ENGINE reconnected successfully"))
                return True
            attempts += 1
            if attempts >= 20:
                print(f"[{_now()}] RECONNECT cooldown 300s after 20 failed attempts")
                asyncio.create_task(self._send_tg("ENGINE reconnect cooldown 300s after 20 failed attempts"))
                await asyncio.sleep(300)
                attempts = 0
        return False

    async def _warmup(self) -> None:
        if not self.session:
            return

        ok = 0
        fail = 0
        for sym in self.universe:
            try:
                rows = await _rest_candles(self.session, sym, "1m", 25)
                closed = [row for row in rows if len(row) > 8 and row[8] == "1"]
                closed.reverse()
                for row in closed:
                    self._append_candle(
                        sym,
                        (
                            int(row[0]),
                            float(row[1]),
                            float(row[2]),
                            float(row[3]),
                            float(row[4]),
                            float(row[5]),
                        ),
                    )
                ok += 1
            except Exception as exc:
                print(f"[{_now()}] Warmup failed {sym}: {exc}")
                fail += 1
            await asyncio.sleep(0.1)
        print(f"[{_now()}] Warmup done ok={ok} fail={fail}")

    async def _subscribe(self) -> None:
        if not self.ws_pub or not self.ws_biz:
            return
        pub_args = [{"channel": "tickers", "instId": sym} for sym in self.universe]
        biz_args = [{"channel": "candle1m", "instId": sym} for sym in self.universe]
        for chunk in _chunked(pub_args, 45):
            await self.ws_pub.send_str(json.dumps({"op": "subscribe", "args": chunk}))
        for chunk in _chunked(biz_args, 45):
            await self.ws_biz.send_str(json.dumps({"op": "subscribe", "args": chunk}))
        print(f"[{_now()}] Subscribed {len(self.universe)} pairs")

    async def _drain_pub(self) -> None:
        if not self.ws_pub:
            return
        try:
            async for msg in self.ws_pub:
                if self.stop_event.is_set() or self.reconnect_event.is_set():
                    return
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    print(f"[{_now()}] public WS closed: {msg.type}")
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[{_now()}] public drain error:\n{_format_exception(exc)}")

    async def _drain_biz(self) -> None:
        if not self.ws_biz:
            return
        try:
            async for msg in self.ws_biz:
                if self.stop_event.is_set() or self.reconnect_event.is_set():
                    return
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    print(f"[{_now()}] business WS closed: {msg.type}")
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[{_now()}] business drain error:\n{_format_exception(exc)}")

    async def _handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        event = msg.get("event")
        if event == "error":
            print(f"[{_now()}] WS error: {msg.get('msg', '')}")
            return
        if event in {"subscribe", "unsubscribe"}:
            return

        arg = msg.get("arg", {})
        channel = arg.get("channel", "")
        sym = arg.get("instId", "")
        data = msg.get("data", [])
        if not data or sym not in self.universe:
            return

        self.last_tick_ts[sym] = pd.Timestamp.utcnow()
        if channel == "tickers":
            return

        if channel.startswith("candle"):
            for row in data:
                if len(row) < 9 or row[8] != "1":
                    continue
                candle = (
                    int(row[0]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    float(row[5]),
                )
                latency_ms = max(0, int(time.time() * 1000 - (candle[0] + 60_000)))
                await self._on_candle_close(sym, candle, latency_ms)

    async def _on_candle_close(self, sym: str, candle: tuple[int, float, float, float, float, float], latency_ms: int) -> None:
        self._append_candle(sym, candle)
        self.last_tick_ts[sym] = pd.Timestamp.utcnow()

        try:
            await self._advance_pending(sym, candle)
        except Exception as exc:
            print(f"[{_now()}] Pending filter error {sym}: {exc}")

        if sym in self.open_positions:
            self._check_position(sym, candle)

        vol_ratio, pct_move, avg_vol = self._compute_live_metrics(sym)
        if self.debug and vol_ratio is not None and pct_move is not None:
            ts_label = datetime.utcfromtimestamp(candle[0] / 1000).strftime("%H:%M")
            print(f"[{ts_label}] Candle closed: {sym} vol_ratio={vol_ratio:.2f}x pct={pct_move:+.2f}%")

        signal = self._detect_latest_signal(sym)
        if signal is None or signal.direction != "PUMP" or avg_vol is None:
            return

        now_wall = time.time()
        if now_wall - self.last_signal_wall[sym] < float(self.config["alert_cooldown_sec"]):
            return

        ct_val = self.ctval.get(sym, 1.0)
        candle_usd_vol = float(signal.entry_price * candle[5] * ct_val)
        if candle_usd_vol < float(self.config["min_usd_vol"]):
            return

        active_hours = self.config.get("active_hours", [])
        if not self.no_filter and active_hours:
            signal_hour = int(signal.ts.hour)
            if signal_hour not in set(int(hour) for hour in active_hours):
                return

        pending = PendingSignal(
            signal=signal,
            baseline_avg_vol=float(avg_vol),
            candle_vol_usd=candle_usd_vol,
            screener_tier=self.screener_tier.get(sym, 0),
            latency_ms=latency_ms,
            signal_id=str(uuid.uuid4())[:12],
        )
        self.pending_signals[sym].append(pending)
        self.last_signal_wall[sym] = now_wall

    async def _update_universe(self) -> None:
        if self.fixed_pairs or not self.session:
            return

        market = await self._fetch_live_universe()
        next_universe = list(dict.fromkeys(market["pairs"]))[: int(self.config["max_pairs"])]
        next_tiers = dict(market["tiers"])
        current = set(self.universe)
        target = set(next_universe)
        added = sorted(target - current)
        removed = sorted(current - target)

        if not added and not removed:
            return

        if removed:
            await self._change_subscriptions(removed, op="unsubscribe")
            for rem in removed:
                self.candles.pop(rem, None)
                self.pending_signals.pop(rem, None)
                self.last_tick_ts.pop(rem, None)
                self.screener_tier.pop(rem, None)
                self.open_positions.pop(rem, None)

        if added:
            self._install_pair_buffers(added)
            merged = list(dict.fromkeys(self.universe + added))
            self.universe = merged
            for add in added:
                self.screener_tier[add] = next_tiers.get(add, 2)
            await self._warmup_pairs(added)
            await self._change_subscriptions(added, op="subscribe")

        if removed:
            self.universe = [sym for sym in self.universe if sym not in removed]

        active_set = set(self.universe)
        self.universe = [sym for sym in next_universe if sym in active_set]
        self.screener_tier = {sym: next_tiers.get(sym, self.screener_tier.get(sym, 0)) for sym in self.universe}

        plus = ",".join(added) if added else "-"
        minus = ",".join(removed) if removed else "-"
        print(f"[{_now()}] Universe updated: +[{plus}] -[{minus}]")
        asyncio.create_task(self._send_tg(f"ENGINE universe updated: +[{plus}] -[{minus}]"))

    async def _heartbeat_loop(self) -> None:
        interval = int(self.config["heartbeat_interval"])
        while not self.stop_event.is_set() and not self.reconnect_event.is_set():
            await asyncio.sleep(interval)
            now_ts = pd.Timestamp.utcnow()
            stale_symbols: list[str] = []
            for sym in self.universe:
                last = self.last_tick_ts.get(sym)
                if last is None:
                    continue
                age = (now_ts - last).total_seconds()
                if age > 120:
                    print(f"[WARN] {sym} stale {age:.0f}s")
                if age > 60:
                    stale_symbols.append(sym)
            print(
                f"[{_now()}] Heartbeat pairs={len(self.universe)} "
                f"active_pending={sum(len(v) for v in self.pending_signals.values())} "
                f"open_positions={len(self.open_positions)} "
                f"stale_pairs={len(stale_symbols)}"
            )
            if self.universe and len(stale_symbols) == len(self.universe):
                print(f"[{_now()}] Heartbeat detected all pairs stale >60s")
                self.reconnect_event.set()
                return

    async def _persist_state(self) -> None:
        payload = {
            "saved_at": pd.Timestamp.utcnow(),
            "universe": list(self.universe),
            "candles": {sym: list(buf) for sym, buf in self.candles.items()},
        }
        with STATE_PATH.open("wb") as f:
            pickle.dump(payload, f)

    def _emit_signal(self, pending: PendingSignal) -> None:
        entry = pending.entry_open_price or pending.signal.entry_price
        tp_price = round(entry * (1 + float(self.config["paper_tp_pct"]) / 100), 8)
        sl_price = round(entry * (1 - float(self.config["paper_sl_pct"]) / 100), 8)
        filters_passed = ["A", "B", "C", "D", "E"] if not self.no_filter else ["A", "B", "C"]

        row = {
            "signal_id": pending.signal_id,
            "type": "ENTRY",
            "ts_utc": _ts_utc(pending.signal.ts),
            "ts_wall": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sym": pending.signal.sym,
            "direction": "PUMP",
            "hour_utc": int(pending.signal.ts.hour),
            "screener_tier": pending.screener_tier,
            "signal_close": pending.signal.entry_price,
            "entry_open_price": entry,
            "paper_tp": tp_price,
            "paper_sl": sl_price,
            "vol_ratio": round(pending.signal.vol_ratio, 3),
            "pct_move": round(pending.signal.pct_move, 3),
            "avg_vol": round(pending.baseline_avg_vol, 2),
            "dollar_vol": round(pending.candle_vol_usd, 0),
            "ct_val": self.ctval.get(pending.signal.sym, 1.0),
            "latency_ms": pending.latency_ms,
            "filters_passed": filters_passed,
            "adx_4h": None,
            "bias_4h": None,
            "adx_1h": None,
            "di_spread_1h": None,
            "bias_1h": None,
            "atr_1h": None,
            "vwap": None,
            "funding_rate_pct": None,
            "oi_delta": None,
        }
        with SIGNALS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

        ts_label = pending.signal.ts.strftime("%H:%M")
        print("========================================")
        print(f"*** PUMP *** {pending.signal.sym} @ {ts_label} UTC")
        print(f"Entry: {entry:.8g}  Move: +{pending.signal.pct_move:.2f}% / {self.config['lookback']}m")
        print(f"Vol:   {pending.signal.vol_ratio:.1f}x avg  (${pending.candle_vol_usd:,.0f})")
        print(f"TP/SL: {tp_price:.8g} / {sl_price:.8g}")
        print(f"Filters: {' '.join(filters_passed)}")
        print("========================================")

        if pending.signal.sym not in self.open_positions and len(self.open_positions) < int(self.config["max_open_positions"]):
            self.open_positions[pending.signal.sym] = OpenPosition(
                signal_id=pending.signal_id,
                sym=pending.signal.sym,
                entry_price=entry,
                sl_price=sl_price,
                tp_price=tp_price,
                opened_at=pending.signal.ts + pd.Timedelta(minutes=1),
                position_usd=float(self.config["paper_position_usd"]),
            )
            asyncio.create_task(
                self._send_tg_entry(
                    pending.signal_id,
                    pending.signal.sym,
                    entry,
                    tp_price,
                    sl_price,
                    pending.signal.vol_ratio,
                    pending.signal.pct_move,
                    pending.candle_vol_usd,
                    int(pending.signal.ts.hour),
                )
            )

        asyncio.create_task(self._enrich_context(pending.signal.sym, pending.signal_id))

    async def _enrich_context(self, sym: str, signal_id: str) -> None:
        try:
            async with aiohttp.ClientSession() as s:
                raw_4h, raw_1h, fund_raw, oi_raw = await asyncio.gather(
                    _rest_candles(s, sym, "4H", 60),
                    _rest_candles(s, sym, "1H", 60),
                    _rest_funding(s, sym),
                    _rest_oi(s, sym),
                    return_exceptions=True,
                )

            ctx: dict[str, Any] = {}
            if isinstance(raw_4h, list) and len(raw_4h) >= 15:
                highs, lows, closes = _parse_hlc(raw_4h)
                adx, _, _ = _wilder_adx(highs, lows, closes, 9)
                ctx["adx_4h"] = round(adx, 1)
                ctx["bias_4h"] = _ema_bias(closes, 20, 50)

            if isinstance(raw_1h, list) and len(raw_1h) >= 15:
                highs, lows, closes = _parse_hlc(raw_1h)
                adx, pdi, mdi = _wilder_adx(highs, lows, closes, 9)
                atr = _wilder_atr(highs, lows, closes, 9)
                ctx["adx_1h"] = round(adx, 1)
                ctx["di_spread_1h"] = round(abs(pdi - mdi), 1)
                ctx["bias_1h"] = _ema_bias(closes, 20, 50)
                ctx["atr_1h"] = round(float(atr), 8)
                ctx["vwap"] = _intraday_vwap(raw_1h)

            if isinstance(fund_raw, dict):
                fr = float(fund_raw.get("fundingRate", 0) or 0)
                ctx["funding_rate_pct"] = round(fr * 100, 4)

            if isinstance(oi_raw, list) and len(oi_raw) >= 2:
                def _oi_val(entry: Any) -> float:
                    if isinstance(entry, dict):
                        return float(entry.get("oi", 0) or 0)
                    if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                        return float(entry[1])
                    return 0.0

                oi_cur = _oi_val(oi_raw[0])
                oi_prev = _oi_val(oi_raw[1])
                ctx["oi_delta"] = round((oi_cur - oi_prev) / oi_prev, 4) if oi_prev > 0 else 0.0

            update = {"signal_id": signal_id, "type": "CONTEXT", **ctx}
            with SIGNALS_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(update, ensure_ascii=False) + "\n")
        except Exception:
            pass

    async def _establish_connection_cycle(self, use_restored_state: bool) -> bool:
        if not await self._connect():
            return False
        if not self.universe:
            await self._update_universe()
            if not self.universe:
                print(f"[{_now()}] Universe is empty, reconnect cycle aborted")
                await self._close_connections()
                return False
        if use_restored_state:
            print(f"[{_now()}] State restored from cache, skipping warmup")
        else:
            await self._warmup()
        await self._subscribe()
        return True

    async def _change_subscriptions(self, pairs: list[str], op: str) -> None:
        if not pairs or not self.ws_pub or not self.ws_biz:
            return
        pub_args = [{"channel": "tickers", "instId": sym} for sym in pairs]
        biz_args = [{"channel": "candle1m", "instId": sym} for sym in pairs]
        for chunk in _chunked(pub_args, 45):
            await self.ws_pub.send_str(json.dumps({"op": op, "args": chunk}))
        for chunk in _chunked(biz_args, 45):
            await self.ws_biz.send_str(json.dumps({"op": op, "args": chunk}))

    async def _advance_pending(self, sym: str, candle: tuple[int, float, float, float, float, float]) -> None:
        if not self.pending_signals[sym]:
            return

        next_pending: list[PendingSignal] = []
        current_close = candle[4]
        current_vol = candle[5]

        for pending in self.pending_signals[sym]:
            pending.bars_seen += 1
            pending.future_vols.append(current_vol)

            if pending.bars_seen == 1:
                pending.entry_open_price = candle[1]
                pending.confirm_passed = current_close > pending.signal.candle_open
                if not pending.confirm_passed:
                    continue
                if int(self.config["vol_sustain_bars"]) <= 0:
                    self._emit_signal(pending)
                    continue

            if not pending.confirm_passed:
                continue

            sustain_bars = int(self.config["vol_sustain_bars"])
            if pending.bars_seen >= sustain_bars:
                mean_future_vol = sum(pending.future_vols[:sustain_bars]) / sustain_bars
                if mean_future_vol > pending.baseline_avg_vol * float(self.config["vol_sustain_ratio"]):
                    self._emit_signal(pending)
                continue

            next_pending.append(pending)

        self.pending_signals[sym] = next_pending

    async def _warmup_pairs(self, pairs: list[str]) -> None:
        if not self.session:
            return
        for sym in pairs:
            try:
                rows = await _rest_candles(self.session, sym, "1m", 25)
                closed = [row for row in rows if len(row) > 8 and row[8] == "1"]
                closed.reverse()
                for row in closed:
                    self._append_candle(
                        sym,
                        (
                            int(row[0]),
                            float(row[1]),
                            float(row[2]),
                            float(row[3]),
                            float(row[4]),
                            float(row[5]),
                        ),
                    )
            except Exception as exc:
                print(f"[{_now()}] Warmup failed {sym}: {exc}")
            await asyncio.sleep(0.1)

    async def _persist_loop(self) -> None:
        while not self.stop_event.is_set() and not self.reconnect_event.is_set():
            await asyncio.sleep(300)
            await self._persist_state()

    async def _universe_loop(self) -> None:
        refresh_seconds = int(self.config["universe_refresh_hours"]) * 3600
        while not self.stop_event.is_set() and not self.reconnect_event.is_set():
            await asyncio.sleep(refresh_seconds)
            await self._update_universe()

    async def _daily_summary_loop(self) -> None:
        while not self.stop_event.is_set():
            now = datetime.utcnow()
            next_summary = now.replace(hour=23, minute=55, second=0, microsecond=0)
            if now >= next_summary:
                next_summary = next_summary + pd.Timedelta(days=1)
            sleep_seconds = max(1.0, (next_summary - datetime.utcnow()).total_seconds())
            await asyncio.sleep(sleep_seconds)
            if self.stop_event.is_set():
                break
            await self._send_daily_summary()

    async def _send_daily_summary(self) -> None:
        if not LABELS_LOG.exists():
            return
        today = datetime.utcnow().strftime("%Y-%m-%d")
        with LABELS_LOG.open(encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        today_exits = [line for line in lines if line.get("type") == "EXIT" and str(line.get("closed_at", "")).startswith(today)]
        if not today_exits:
            await self._send_tg("📊 <b>ИТОГ ДНЯ</b>\nСигналов сегодня: 0")
            return
        n = len(today_exits)
        tp_n = sum(1 for item in today_exits if item.get("exit_reason") == "TP")
        sl_n = sum(1 for item in today_exits if item.get("exit_reason") == "SL")
        time_n = sum(1 for item in today_exits if item.get("exit_reason") == "TIME")
        wins = sum(1 for item in today_exits if float(item["net_pnl_pct"]) > 0)
        total_net = sum(float(item["net_pnl_pct"]) for item in today_exits)
        text = (
            f"📊 <b>ИТОГ ДНЯ {today}</b>\n"
            f"Сделок: {n} | TP: {tp_n} | SL: {sl_n} | TIME: {time_n}\n"
            f"Net P&L: {total_net:+.2f}% | WR: {wins / n * 100:.0f}%"
        )
        await self._send_tg(text)

    async def _load_ctvals(self) -> None:
        self.ctval = await asyncio.to_thread(fetch_ctvals)
        print(f"[{_now()}] ctVal loaded for {len(self.ctval)} instruments")

    async def _fetch_live_universe(self) -> dict[str, Any]:
        screener_path = Path(__file__).parent / "cache" / "coin_screener_latest.json"
        if screener_path.exists():
            age_hours = (time.time() - screener_path.stat().st_mtime) / 3600.0
            if age_hours < float(self.config.get("universe_refresh_hours", 1)):
                data = json.loads(screener_path.read_text(encoding="utf-8"))
                tier1 = data.get("tier1", [])
                tier2 = data.get("tier2", [])
                pairs = list(dict.fromkeys(tier1 + tier2))[: int(self.config["max_pairs"])]
                tiers = {sym: 1 for sym in tier1}
                tiers.update({sym: 2 for sym in tier2})
                return {"pairs": pairs, "tiers": tiers}

        if not self.session:
            return {"pairs": list(self.universe), "tiers": dict(self.screener_tier)}

        async with self.session.get(REST_TICKERS_URL, params={"instType": "SWAP"}) as resp:
            payload = await resp.json(content_type=None)
        if payload.get("code") != "0":
            raise RuntimeError(f"tickers failed: code={payload.get('code')} msg={payload.get('msg', '')}")

        tier1 = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
        scored: list[tuple[str, float]] = []
        for row in payload.get("data", []):
            inst_id = row.get("instId", "")
            excluded, _ = _is_excluded_symbol(inst_id)
            if excluded:
                continue
            try:
                dollar_vol = float(row.get("volCcy24h", 0.0))
                open24h = float(row.get("open24h", 0.0))
                high24h = float(row.get("high24h", 0.0))
                low24h = float(row.get("low24h", 0.0))
            except (TypeError, ValueError):
                continue
            if open24h <= 0:
                continue
            volatility_24h = (high24h - low24h) / open24h * 100.0
            if inst_id not in tier1 and dollar_vol < float(self.config["min_market_vol_24h"]):
                continue
            if volatility_24h > 50.0:
                continue
            scored.append((inst_id, dollar_vol))

        scored.sort(key=lambda item: item[1], reverse=True)
        remaining_slots = max(0, int(self.config["max_pairs"]) - len(tier1))
        tier2 = [sym for sym, _ in scored if sym not in tier1][:remaining_slots]
        pairs = list(dict.fromkeys(tier1 + tier2))
        tiers = {sym: 1 for sym in tier1}
        tiers.update({sym: 2 for sym in tier2})
        return {"pairs": pairs, "tiers": tiers}

    def _restore_state(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            with STATE_PATH.open("rb") as f:
                payload = pickle.load(f)
            saved_at = pd.Timestamp(payload.get("saved_at"))
            age_min = (pd.Timestamp.utcnow() - saved_at).total_seconds() / 60.0
            if age_min > float(self.config["state_ttl_min"]):
                return

            stored_universe = payload.get("universe") or list((payload.get("candles") or {}).keys())
            if self.fixed_pairs:
                allowed = set(self.fixed_pairs)
                self.universe = [sym for sym in stored_universe if sym in allowed]
                for sym in self.fixed_pairs:
                    if sym not in self.universe:
                        self.universe.append(sym)
            else:
                self.universe = list(stored_universe)

            self._install_pair_buffers(self.universe)
            for sym, rows in payload.get("candles", {}).items():
                if sym not in self.universe:
                    continue
                self.candles[sym].clear()
                for row in rows:
                    self.candles[sym].append(tuple(row))
            self.screener_tier = {sym: self.screener_tier.get(sym, 0) for sym in self.universe}
            self.state_restored = True
        except Exception as exc:
            print(f"[{_now()}] State restore failed: {exc}")

    async def _close_connections(self) -> None:
        for ws in (self.ws_pub, self.ws_biz):
            if ws is not None and not ws.closed:
                await ws.close()
        self.ws_pub = None
        self.ws_biz = None
        if self.session is not None and not self.session.closed:
            await self.session.close()
        self.session = None

    async def _shutdown(self) -> None:
        try:
            await self._persist_state()
        except Exception as exc:
            print(f"[{_now()}] Final state save failed: {exc}")
        await self._close_connections()
        print(f"[{_now()}] Graceful shutdown, state saved")

    def _install_pair_buffers(self, pairs: list[str]) -> None:
        maxlen = int(self.config["lookback"]) + 30
        for sym in pairs:
            if sym not in self.candles:
                self.candles[sym] = deque(maxlen=maxlen)

    def _append_candle(self, sym: str, candle: tuple[int, float, float, float, float, float]) -> None:
        buf = self.candles.setdefault(sym, deque(maxlen=int(self.config["lookback"]) + 30))
        if buf and candle[0] < buf[-1][0]:
            return
        if buf and candle[0] == buf[-1][0]:
            buf[-1] = candle
            return
        buf.append(candle)

    def _compute_live_metrics(self, sym: str) -> tuple[float | None, float | None, float | None]:
        buf = self.candles.get(sym)
        lookback = int(self.config["lookback"])
        if not buf or len(buf) < lookback + 1:
            return None, None, None
        hist = list(buf)
        current = hist[-1]
        previous = hist[-lookback - 1 : -1]
        avg_vol = sum(row[5] for row in previous) / lookback
        if avg_vol <= 0:
            return None, None, None
        price_then = previous[0][4]
        if price_then == 0:
            return None, None, avg_vol
        vol_ratio = current[5] / avg_vol
        pct_move = (current[4] - price_then) / price_then * 100.0
        return float(vol_ratio), float(pct_move), float(avg_vol)

    def _detect_latest_signal(self, sym: str) -> Signal | None:
        buf = self.candles.get(sym)
        lookback = int(self.config["lookback"])
        if not buf or len(buf) < lookback + 1:
            return None

        df = pd.DataFrame(list(buf), columns=["ts_ms", "open", "high", "low", "close", "vol"])
        df.index = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
        df = df[["open", "high", "low", "close", "vol"]]
        df.attrs["symbol"] = sym
        signals = detect_signals(
            df=df,
            vol_mult=float(self.config["vol_mult"]),
            price_pct=float(self.config["price_pct"]),
            lookback=lookback,
            sym=sym,
        )
        if not signals:
            return None
        latest = signals[-1]
        if latest.candle_idx != len(df) - 1:
            return None
        return latest

    def _check_position(self, sym: str, candle: tuple[int, float, float, float, float, float]) -> None:
        pos = self.open_positions.get(sym)
        if not pos:
            return
        ts_ms, _open, high, low, close, _vol = candle
        exit_reason = None
        exit_price = None
        if low <= pos.sl_price:
            exit_reason, exit_price = "SL", pos.sl_price
        elif high >= pos.tp_price:
            exit_reason, exit_price = "TP", pos.tp_price
        else:
            hold_min = (pd.Timestamp(ts_ms, unit="ms", tz="UTC") - pos.opened_at).total_seconds() / 60.0
            if hold_min >= float(self.config["paper_max_hold_min"]):
                exit_reason, exit_price = "TIME", close
        if not exit_reason or exit_price is None:
            return

        gross = (exit_price - pos.entry_price) / pos.entry_price * 100.0
        net = gross - float(self.config["fee_rt_pct"])
        hold_min = (pd.Timestamp(ts_ms, unit="ms", tz="UTC") - pos.opened_at).total_seconds() / 60.0
        label = {
            "signal_id": pos.signal_id,
            "type": "EXIT",
            "sym": sym,
            "exit_reason": exit_reason,
            "entry_price": pos.entry_price,
            "exit_price": round(exit_price, 8),
            "gross_pnl_pct": round(gross, 4),
            "fee_pct": float(self.config["fee_rt_pct"]),
            "net_pnl_pct": round(net, 4),
            "hold_min": round(hold_min, 1),
            "opened_at": pos.opened_at.isoformat(),
            "closed_at": pd.Timestamp(ts_ms, unit="ms", tz="UTC").isoformat(),
        }
        with LABELS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(label, ensure_ascii=False) + "\n")
        print(f"[PAPER {exit_reason}] {sym} net={net:+.2f}% hold={hold_min:.0f}m")
        del self.open_positions[sym]
        asyncio.create_task(self._send_tg_exit(label))

    async def _send_tg(self, text: str) -> None:
        if not self.tg_enabled:
            return
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(
                    url,
                    json={"chat_id": self.tg_chat_id, "text": text, "parse_mode": "HTML"},
                    timeout=aiohttp.ClientTimeout(total=5),
                )
        except Exception:
            pass

    async def _send_tg_entry(
        self,
        signal_id: str,
        sym: str,
        entry: float,
        tp: float,
        sl: float,
        vol_ratio: float,
        pct: float,
        dv: float,
        hour: int,
    ) -> None:
        text = (
            "📥 <b>PAPER ENTRY</b>\n"
            f"{sym} | +{pct:.1f}% | Vol {vol_ratio:.1f}x | ${dv:,.0f}\n"
            f"Вход: {entry} | TP: {tp} | SL: {sl}\n"
            f"Час: {hour}UTC | ID: {signal_id}"
        )
        asyncio.create_task(self._send_tg(text))

    async def _send_tg_exit(self, label: dict) -> None:
        text = (
            f"📤 <b>PAPER EXIT - {label['exit_reason']}</b>\n"
            f"{label['sym']} | Net: {label['net_pnl_pct']:+.2f}% | Hold: {label['hold_min']:.0f}m\n"
            f"Вход: {label['entry_price']} -> Выход: {label['exit_price']}\n"
            f"ID: {label['signal_id']}"
        )
        asyncio.create_task(self._send_tg(text))

    def _print_startup_config(self) -> None:
        print("=" * 58)
        print("  OKX Pump Engine - Paper Trading")
        print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"  Debug: {self.debug}  |  No-filter: {self.no_filter}")
        print(
            "  Config: "
            f"vol_mult={self.config['vol_mult']} "
            f"price_pct={self.config['price_pct']} "
            f"lookback={self.config['lookback']} "
            f"cooldown={self.config['alert_cooldown_sec']}s"
        )
        print(
            "  Filters: "
            f"min_usd_vol={self.config['min_usd_vol']} "
            f"hours={self.config.get('active_hours', [])} "
            f"sustain={self.config['vol_sustain_bars']}x{self.config['vol_sustain_ratio']}"
        )
        print(
            "  Paper: "
            f"tp={self.config['paper_tp_pct']}% "
            f"sl={self.config['paper_sl_pct']}% "
            f"max_hold={self.config['paper_max_hold_min']}m "
            f"pos_usd={self.config['paper_position_usd']} "
            f"max_open={self.config['max_open_positions']}"
        )
        print("=" * 58)

    def _install_signal_handlers(self) -> None:
        def _handle_stop(_signum: int, _frame: Any) -> None:
            if self.loop and not self.stop_event.is_set():
                self.loop.call_soon_threadsafe(self.stop_event.set)

        signal.signal(signal.SIGINT, _handle_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle_stop)


if __name__ == "__main__":
    _setup_rotating_log()
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--no-filter",
        action="store_true",
        help="skip D+E filters, show all A+B+C signals",
    )
    parser.add_argument(
        "--pairs",
        nargs="+",
        default=None,
        help="override dynamic universe with fixed list",
    )
    args = parser.parse_args()

    cfg = _load_pump_config()
    if args.no_filter:
        cfg["vol_sustain_bars"] = 0
        cfg["active_hours"] = list(range(24))

    engine = PumpEngine(cfg, debug=args.debug, fixed_pairs=args.pairs, no_filter=args.no_filter)
    asyncio.run(engine.start())
