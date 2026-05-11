"""Shadow-mode smart pump detector.

Phase C steps 1-5 only:
  - typed contracts
  - exchange gateway boundary
  - pair metadata via CoinGecko cache
  - candle1m integration through WSFeed
  - prefilter candidate logging, no positions or paper trading
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.ws_feed import Candle, WSFeed, _chunked

ACTIVE_UNIVERSE_PATH = Path(__file__).resolve().parent / "cache" / "active_universe.json"
COINGECKO_CACHE_PATH = Path(__file__).resolve().parent / "cache" / "coingecko_coins_list.json"
COINGECKO_COINS_LIST_URL = "https://api.coingecko.com/api/v3/coins/list"
LOG_DIR = ROOT / "logs" / "pump"
ENGINE_LOG = LOG_DIR / "ws_smart_pump.log"
CANDIDATES_LOG = LOG_DIR / "smart_pump_candidates.jsonl"

NETWORK_BY_PLATFORM = {
    "solana": "SOL",
    "ethereum": "ETH",
    "bitcoin": "BTC",
    "binance-smart-chain": "BNB",
    "bnb-smart-chain": "BNB",
}
NETWORK_OVERRIDES = {
    "BTC": "BTC",
    "ORDI": "BTC",
    "ETH": "ETH",
    "SHIB": "ETH",
    "SOL": "SOL",
    "BONK": "SOL",
    "BNB": "BNB",
    "CAKE": "BNB",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "universe_poll_sec": 1,
    "price_pct": 1.5,
    "vol_mult": 2.0,
    "baseline_bars": 15,
    "stagnation_price_pct": 0.5,
    "data_freshness_sec": 90,
    "heartbeat_interval": 30,
    "warmup_bars": 20,
    "coingecko_cache_hours": 24,
    "coingecko_enabled": True,
}


@dataclass
class PairState:
    sym: str
    parent_network: str = "OTHER"
    candle_close: float = 0.0
    vol_usd: float = 0.0
    price_change_1m_pct: float = 0.0
    oi_now: float | None = None
    oi_5m_ago: float | None = None
    funding_rate: float | None = None
    taker_buy_ratio_60s: float | None = None
    cvd_delta: float | None = None
    news_score: float | None = None
    ts_candle: float = 0.0
    ts_oi: float = 0.0
    ts_trades: float = 0.0


@dataclass
class SignalCandidate:
    sym: str
    direction: str
    trigger_reason: str
    gate_passed: bool
    gate_blocked_by: str
    ts: str


@dataclass
class GateDecision:
    passed: bool
    reason: str
    score: float


@dataclass
class PairMetadata:
    sym: str
    base_asset: str
    parent_network: str


class ExchangeGateway(ABC):
    @abstractmethod
    async def subscribe_candles(self, sym: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def subscribe_oi(self, sym: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def subscribe_trades(self, sym: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_funding(self, sym: str) -> float | None:
        raise NotImplementedError


class OKXGateway(ExchangeGateway):
    def __init__(self, feed: WSFeed, logger: logging.Logger, warmup_bars: int) -> None:
        self.feed = feed
        self.logger = logger
        self.warmup_bars = warmup_bars
        self.subscribed_candles: set[str] = set()

    async def subscribe_candles(self, sym: str) -> None:
        if sym in self.subscribed_candles:
            return
        if sym not in self.feed.buffers:
            self.feed.ensure_pair(sym)
        if self.feed.connected_event.is_set() and self.feed.ws:
            args = [{"channel": "candle1m", "instId": sym}]
            for chunk in _chunked(args, 45):
                await self.feed.ws.send_str(json.dumps({"op": "subscribe", "args": chunk}))
            await self.warmup_candles(sym)
        self.subscribed_candles.add(sym)

    async def subscribe_oi(self, sym: str) -> None:
        self.logger.debug("OI stream is not enabled in Phase C steps 1-5 | %s", sym)

    async def subscribe_trades(self, sym: str) -> None:
        self.logger.debug("Trades stream is not enabled in Phase C steps 1-5 | %s", sym)

    async def get_funding(self, sym: str) -> float | None:
        self.logger.debug("Funding cache is not enabled in Phase C steps 1-5 | %s", sym)
        return None

    async def unsubscribe_candles(self, sym: str) -> None:
        if sym not in self.subscribed_candles:
            return
        if self.feed.connected_event.is_set() and self.feed.ws:
            args = [{"channel": "candle1m", "instId": sym}]
            await self.feed.ws.send_str(json.dumps({"op": "unsubscribe", "args": args}))
        self.subscribed_candles.discard(sym)
        if sym in self.feed.pairs:
            self.feed.pairs.remove(sym)
        self.feed.buffers.pop(sym, None)
        self.feed.last_close_ts.pop(sym, None)

    async def warmup_candles(self, sym: str) -> None:
        await self.feed.connected_event.wait()
        try:
            rows = await self.feed._rest_candles(sym, "1m", self.warmup_bars)
        except Exception as exc:
            self.logger.info("[%s] WARMUP fail | %s | %s", _now(), sym, exc)
            return
        closed = [row for row in rows if len(row) > 8 and row[8] == "1"]
        closed.reverse()
        buf = self.feed.buffers[sym]["candle1m"]
        buf.clear()
        for row in closed:
            candle = self.feed._parse_candle(row)
            buf.append(candle)
            self.feed.last_close_ts[sym]["candle1m"] = candle[0]


class CoinGeckoClient:
    def __init__(self, cache_path: Path, cache_ttl_sec: float, logger: logging.Logger) -> None:
        self.cache_path = cache_path
        self.cache_ttl_sec = cache_ttl_sec
        self.logger = logger
        self._coin_rows: list[dict[str, Any]] = []
        self._symbol_to_network: dict[str, str] = {}

    async def load(self, symbols: set[str]) -> dict[str, str]:
        if not symbols:
            return {}
        rows = await self._load_rows()
        self._coin_rows = rows
        self._symbol_to_network = self._build_symbol_map(rows)
        return {symbol: self.resolve_parent_network(symbol) for symbol in symbols}

    def resolve_parent_network(self, symbol: str) -> str:
        base = _base_asset(symbol).upper()
        if base in NETWORK_OVERRIDES:
            return NETWORK_OVERRIDES[base]
        return self._symbol_to_network.get(base, "OTHER")

    async def _load_rows(self) -> list[dict[str, Any]]:
        cached = self._read_fresh_cache()
        if cached is not None:
            self.logger.info("[%s] CoinGecko cache hit | rows=%s", _now(), len(cached))
            return cached
        try:
            rows = await self._fetch_coins_list()
            self._write_cache(rows)
            self.logger.info("[%s] CoinGecko fetched | rows=%s", _now(), len(rows))
            return rows
        except Exception as exc:
            self.logger.info("[%s] CoinGecko fetch failed | %s", _now(), exc)
            stale = self._read_any_cache()
            if stale is not None:
                self.logger.info("[%s] CoinGecko stale cache fallback | rows=%s", _now(), len(stale))
                return stale
            return []

    async def _fetch_coins_list(self) -> list[dict[str, Any]]:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {}
            api_key = _env("COINGECKO_API_KEY") or _env("CG_API_KEY")
            if api_key:
                headers["x-cg-demo-api-key"] = api_key
            async with session.get(
                COINGECKO_COINS_LIST_URL,
                params={"include_platform": "true"},
                headers=headers,
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")
                payload = await resp.json(content_type=None)
        if not isinstance(payload, list):
            raise RuntimeError("unexpected CoinGecko response")
        return [row for row in payload if isinstance(row, dict)]

    def _read_fresh_cache(self) -> list[dict[str, Any]] | None:
        if not self.cache_path.exists():
            return None
        age = time.time() - self.cache_path.stat().st_mtime
        if age > self.cache_ttl_sec:
            return None
        return self._read_any_cache()

    def _read_any_cache(self) -> list[dict[str, Any]] | None:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        rows = payload.get("coins") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return None
        return [row for row in rows if isinstance(row, dict)]

    def _write_cache(self, rows: list[dict[str, Any]]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _ts_utc(),
            "source": COINGECKO_COINS_LIST_URL,
            "coins": rows,
        }
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _build_symbol_map(self, rows: list[dict[str, Any]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for row in rows:
            symbol = str(row.get("symbol", "")).upper()
            if not symbol or symbol in result:
                continue
            platforms = row.get("platforms") or {}
            if not isinstance(platforms, dict):
                continue
            for platform in ("solana", "ethereum", "bitcoin", "binance-smart-chain", "bnb-smart-chain"):
                if platforms.get(platform):
                    result[symbol] = NETWORK_BY_PLATFORM[platform]
                    break
        return result


class SmartPumpShadow:
    def __init__(self) -> None:
        load_dotenv(ROOT / ".env")
        self.config = _load_config()
        self.logger = _setup_logger()
        self.feed = WSFeed(pairs=[], bars=["candle1m"], buffer_size=self._buffer_size())
        self.gateway = OKXGateway(self.feed, self.logger, warmup_bars=int(self.config["warmup_bars"]))
        self.metadata = CoinGeckoClient(
            COINGECKO_CACHE_PATH,
            cache_ttl_sec=float(self.config["coingecko_cache_hours"]) * 3600.0,
            logger=self.logger,
        )
        self.stop_event = asyncio.Event()
        self.state_lock = asyncio.Lock()
        self.states: dict[str, PairState] = {}
        self.pair_metadata: dict[str, PairMetadata] = {}
        self.parent_networks: dict[str, str] = {}
        self._universe_mtime: float = 0.0

    async def run(self) -> None:
        self._install_signal_handlers()
        self.feed.on_candle_close("candle1m", self._on_candle_close)

        initial_universe = self._read_universe()
        self._universe_mtime = _path_mtime(ACTIVE_UNIVERSE_PATH)
        if bool(self.config["coingecko_enabled"]):
            bases = {_base_asset(sym) for sym in initial_universe}
            self.parent_networks = await self.metadata.load(bases)
        await self._sync_universe(initial_universe)
        if self.states:
            await self.feed.warmup()

        self._log(
            "started | shadow_mode=true "
            f"pairs={len(initial_universe)} price_pct={self.config['price_pct']} "
            f"vol_mult={self.config['vol_mult']} baseline_bars={self.config['baseline_bars']}"
        )

        feed_task = asyncio.create_task(self.feed.start(), name="ws_smart_pump.feed")
        universe_task = asyncio.create_task(self._universe_watch_loop(), name="ws_smart_pump.universe")
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="ws_smart_pump.heartbeat")
        stop_task = asyncio.create_task(self.stop_event.wait(), name="ws_smart_pump.stop")

        try:
            done, _ = await asyncio.wait(
                {feed_task, universe_task, heartbeat_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if feed_task in done:
                exc = feed_task.exception()
                if exc:
                    raise exc
                raise RuntimeError("WSFeed stopped unexpectedly")
        finally:
            self.stop_event.set()
            for task in (feed_task, universe_task, heartbeat_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(feed_task, universe_task, heartbeat_task, stop_task, return_exceptions=True)
            await self.feed.stop()
            self._log(f"stopped | pairs={len(self.states)}")

    async def _universe_watch_loop(self) -> None:
        poll_sec = float(self.config["universe_poll_sec"])
        while not self.stop_event.is_set():
            await asyncio.sleep(poll_sec)
            try:
                mtime = ACTIVE_UNIVERSE_PATH.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtime <= self._universe_mtime:
                continue
            self._universe_mtime = mtime
            universe = self._read_universe()
            if bool(self.config["coingecko_enabled"]):
                missing = {
                    _base_asset(sym)
                    for sym in universe
                    if _base_asset(sym) not in self.parent_networks
                }
                if missing:
                    self.parent_networks.update(await self.metadata.load(missing))
            await self._sync_universe(universe)

    async def _sync_universe(self, universe: dict[str, dict[str, Any]]) -> None:
        async with self.state_lock:
            desired = set(universe)
            current = set(self.states)
            added = sorted(desired - current)
            removed = sorted(current - desired)

            for sym in added:
                pair_meta = self._metadata_for(sym)
                self.pair_metadata[sym] = pair_meta
                self.states[sym] = PairState(sym=sym, parent_network=pair_meta.parent_network)
                await self.gateway.subscribe_candles(sym)

            for sym in removed:
                self.states.pop(sym, None)
                self.pair_metadata.pop(sym, None)
                await self.gateway.unsubscribe_candles(sym)

            for sym in desired & current:
                pair_meta = self._metadata_for(sym)
                self.pair_metadata[sym] = pair_meta
                self.states[sym].parent_network = pair_meta.parent_network

        if added or removed:
            self._log(
                f"UNIVERSE update | active={len(universe)} "
                f"| added={','.join(added) or '-'} | removed={','.join(removed) or '-'}"
            )

    async def _on_candle_close(self, sym: str, candle: Candle) -> None:
        async with self.state_lock:
            state = self.states.get(sym)
            if state is None:
                return
            self._update_pair_state(state, candle)
            gate = self._gate_decision(state)
            if not gate.passed:
                if gate.reason.startswith("stale_candle"):
                    self._write_candidate(state, gate, "freshness_check")
                return
            self._write_candidate(state, gate, "prefilter")

    def _update_pair_state(self, state: PairState, candle: Candle) -> None:
        ts_ms, open_px, _high, _low, close_px, _vol_contracts, vol_usdt = candle
        state.candle_close = float(close_px)
        # WSFeed maps OKX candle row[7] (volCcyQuote, USDT volume) to candle[6].
        state.vol_usd = float(vol_usdt)
        state.price_change_1m_pct = (
            abs(float(close_px) - float(open_px)) / float(open_px) * 100.0 if open_px > 0 else 0.0
        )
        state.ts_candle = int(ts_ms) / 1000.0

    def _gate_decision(self, state: PairState) -> GateDecision:
        now = time.time()
        max_age = float(self.config["data_freshness_sec"])
        if state.ts_candle <= 0:
            return GateDecision(False, "missing_candle", 0.0)
        age = now - state.ts_candle
        if age > max_age:
            return GateDecision(False, f"stale_candle:{age:.1f}s", 0.0)

        vol_ratio = self._vol_ratio(state.sym)
        if vol_ratio is None:
            return GateDecision(False, "insufficient_baseline", 0.0)

        price_pct = float(state.price_change_1m_pct)
        if vol_ratio >= float(self.config["vol_mult"]) and price_pct < float(self.config["stagnation_price_pct"]):
            return GateDecision(False, "stagnation", vol_ratio)
        if price_pct < float(self.config["price_pct"]):
            return GateDecision(False, "price_change_below_threshold", vol_ratio)
        if vol_ratio < float(self.config["vol_mult"]):
            return GateDecision(False, "vol_ratio_below_threshold", vol_ratio)
        return GateDecision(True, f"prefilter:price={price_pct:.3f},vol_ratio={vol_ratio:.3f}", vol_ratio)

    def _vol_ratio(self, sym: str) -> float | None:
        bars = int(self.config["baseline_bars"])
        history = self.feed.get_candles(sym, "candle1m", bars + 1)
        if len(history) < bars + 1:
            return None
        current = history[-1]
        previous = history[-(bars + 1) : -1]
        baseline = sum(row[6] for row in previous) / float(bars)
        if baseline <= 0:
            return None
        return float(current[6]) / baseline

    def _write_candidate(self, state: PairState, gate: GateDecision, trigger: str) -> None:
        direction = self._direction_from_last_candle(state.sym)
        blocked_by = "" if gate.passed else gate.reason
        candidate = SignalCandidate(
            sym=state.sym,
            direction=direction,
            trigger_reason=trigger,
            gate_passed=gate.passed,
            gate_blocked_by=blocked_by,
            ts=_ts_utc(),
        )
        payload = {
            **asdict(candidate),
            "gate_score": gate.score,
            "pair_state": asdict(state),
        }
        CANDIDATES_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CANDIDATES_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        status = "PASS" if gate.passed else "BLOCK"
        self._log(f"CANDIDATE {status} | {state.sym:<20} | {gate.reason}")

    def _read_universe(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(ACTIVE_UNIVERSE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
        meta = payload.get("symbols", {})
        if isinstance(meta, dict) and meta:
            return {str(sym): info for sym, info in meta.items() if isinstance(info, dict)}
        active = payload.get("active", [])
        if isinstance(active, list):
            return {str(sym): {} for sym in active}
        return {}

    async def _heartbeat_loop(self) -> None:
        interval = int(self.config["heartbeat_interval"])
        while not self.stop_event.is_set():
            await asyncio.sleep(interval)
            subscribed = len(self.gateway.subscribed_candles)
            self._log(f"HEARTBEAT | pairs={len(self.states)} subscribed_candles={subscribed}")

    def _buffer_size(self) -> int:
        return max(int(self.config["baseline_bars"]) + 5, int(self.config["warmup_bars"]), 30)

    def _metadata_for(self, sym: str) -> PairMetadata:
        base = _base_asset(sym)
        return PairMetadata(
            sym=sym,
            base_asset=base,
            parent_network=self.parent_networks.get(base, "OTHER"),
        )

    def _direction_from_last_candle(self, sym: str) -> str:
        history = self.feed.get_candles(sym, "candle1m", 1)
        if not history:
            return "unknown"
        current = history[-1]
        if current[4] > current[1]:
            return "up"
        if current[4] < current[1]:
            return "down"
        return "flat"

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGINT", "SIGTERM"):
            if not hasattr(signal, sig_name):
                continue
            try:
                loop.add_signal_handler(getattr(signal, sig_name), self.stop_event.set)
            except NotImplementedError:
                signal.signal(getattr(signal, sig_name), lambda *_args: self.stop_event.set())

    def _log(self, message: str) -> None:
        self.logger.info(f"[{_now()}] {message}")


def _load_config() -> dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    try:
        with (ROOT / "config.yaml").open(encoding="utf-8") as f:
            project = yaml.safe_load(f) or {}
        cfg.update(project.get("pump_orchestrator", {}) or {})
        cfg.update(project.get("smart_pump", {}) or {})
    except Exception:
        pass
    return cfg


def _setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ws_smart_pump")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    fmt = logging.Formatter("%(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        ENGINE_LOG,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def _base_asset(sym: str) -> str:
    return sym.split("-", 1)[0].upper()


def _now() -> str:
    return datetime.utcnow().strftime("%H:%M:%S")


def _ts_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    return value.strip()


if __name__ == "__main__":
    asyncio.run(SmartPumpShadow().run())
