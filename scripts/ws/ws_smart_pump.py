"""Paper reversal pump engine.

Phase C replacement for the archived momentum pump orchestrator. This process
does not place live orders; it records paper entries/exits only.
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
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from src.data.ws_feed import Candle, WSFeed, _chunked
from src.utils.telegram import send_message_to

ACTIVE_UNIVERSE_PATH = Path(__file__).resolve().parent / "cache" / "active_universe.json"
COINGECKO_CACHE_PATH = Path(__file__).resolve().parent / "cache" / "coingecko_coins_list.json"
COINGECKO_COINS_LIST_URL = "https://api.coingecko.com/api/v3/coins/list"
LOG_DIR = ROOT / "logs" / "pump"
ENGINE_LOG = LOG_DIR / "ws_smart_pump.log"
CANDIDATES_LOG = LOG_DIR / "smart_pump_candidates.jsonl"
SIGNALS_LOG = LOG_DIR / "smart_pump_signals.jsonl"
LABELS_LOG = LOG_DIR / "smart_pump_labels.jsonl"

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
    "explosion_pct": 0.8,
    "vol_mult": 2.0,
    "baseline_bars": 15,
    "eligible_pairs": ["BILL-USDT-SWAP", "JELLYJELLY-USDT-SWAP", "NOT-USDT-SWAP"],
    "early_entry_enabled": True,
    "early_trigger_pct": 0.5,
    "early_trigger_sec": 20,
    "sl_pct": 0.8,
    "tp_pct": 1.5,
    "max_hold_min": 15,
    "be_trigger_pct": 0.5,
    "lock_trigger_pct": 0.7,
    "lock_profit_pct": 0.2,
    "max_concurrent": 2,
    "cooldown_sec": 120,
    "universe_poll_sec": 1,
    "heartbeat_interval": 30,
    "warmup_bars": 20,
    "coingecko_cache_hours": 24,
    "coingecko_enabled": True,
}


@dataclass
class PairState:
    sym: str
    parent_network: str = "OTHER"
    last_candle_ts_ms: int = 0
    last_close: float = 0.0
    last_vol_ratio: float = 0.0
    last_price_change_pct: float = 0.0


@dataclass
class ExplosionEvent:
    sym: str
    direction: str
    reversal_side: str
    explosion_pct: float
    vol_ratio: float
    candle_open: float
    candle_close: float
    ts_ms: int
    ts: str


@dataclass
class PaperPosition:
    signal_id: str
    sym: str
    side: str
    entry_price: float
    sl_price: float
    tp_price: float
    sl_initial: float
    mfe_pct: float
    mae_pct: float
    opened_at: float
    ts: str
    early_entry: bool
    explosion_pct: float
    vol_ratio: float
    sl_promoted: str


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
        self.logger.debug("OI stream is not enabled for smart pump | %s", sym)

    async def subscribe_trades(self, sym: str) -> None:
        self.logger.debug("Trades stream is not enabled for smart pump | %s", sym)

    async def get_funding(self, sym: str) -> float | None:
        self.logger.debug("Funding cache is not enabled for smart pump | %s", sym)
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


class SmartPumpEngine:
    def __init__(self) -> None:
        self.config = _load_config()
        self.logger = _setup_logger()
        self.feed = WSFeed(pairs=[], bars=["candle1m"], buffer_size=self._buffer_size())
        self.gateway = OKXGateway(self.feed, self.logger, warmup_bars=int(self.config["warmup_bars"]))
        self.metadata = CoinGeckoClient(
            COINGECKO_CACHE_PATH,
            cache_ttl_sec=float(self.config.get("coingecko_cache_hours", 24)) * 3600.0,
            logger=self.logger,
        )
        self.stop_event = asyncio.Event()
        self.state_lock = asyncio.Lock()
        self.states: dict[str, PairState] = {}
        self.positions: dict[str, PaperPosition] = {}
        self.cooldowns: dict[str, float] = {}
        self.early_fired: dict[str, int] = {}
        self.pair_metadata: dict[str, PairMetadata] = {}
        self.parent_networks: dict[str, str] = {}
        self._universe_mtime: float = 0.0
        self.eligible_pairs = {str(sym) for sym in self.config.get("eligible_pairs", [])}

    async def run(self) -> None:
        self._install_signal_handlers()
        self.feed.on_candle_close("candle1m", self._on_candle_close)
        self.feed.on_candle_forming("candle1m", self._on_candle_forming)

        initial_universe = self._read_universe()
        self._universe_mtime = _path_mtime(ACTIVE_UNIVERSE_PATH)
        if bool(self.config.get("coingecko_enabled", True)):
            bases = {_base_asset(sym) for sym in initial_universe}
            self.parent_networks = await self.metadata.load(bases)
        await self._sync_universe(initial_universe)
        if self.states:
            await self.feed.warmup()

        self._log(
            "started | reversal_paper=true "
            f"eligible={','.join(sorted(self.eligible_pairs)) or '-'} "
            f"subscribed={len(self.states)} explosion_pct={self.config['explosion_pct']} "
            f"vol_mult={self.config['vol_mult']}"
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
            self._log(f"stopped | pairs={len(self.states)} open_positions={len(self.positions)}")

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
            if bool(self.config.get("coingecko_enabled", True)):
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
            desired = self.eligible_pairs  # always subscribe to eligible pairs directly
            current = set(self.states)
            added = sorted(desired - current)
            removed = sorted(current - desired)

            for sym in added:
                pair_meta = self._metadata_for(sym)
                self.pair_metadata[sym] = pair_meta
                self.states[sym] = PairState(sym=sym, parent_network=pair_meta.parent_network)
                await self.gateway.subscribe_candles(sym)

            for sym in removed:
                if sym in self.positions:
                    await self._close_position(self.positions[sym], "TIME", self.positions[sym].entry_price)
                self.states.pop(sym, None)
                self.pair_metadata.pop(sym, None)
                self.early_fired.pop(sym, None)
                await self.gateway.unsubscribe_candles(sym)

            for sym in desired & current:
                pair_meta = self._metadata_for(sym)
                self.pair_metadata[sym] = pair_meta
                self.states[sym].parent_network = pair_meta.parent_network

        if added or removed:
            self._log(
                f"UNIVERSE update | eligible={len(desired)} "
                f"| added={','.join(added) or '-'} | removed={','.join(removed) or '-'}"
            )

    async def _on_candle_close(self, sym: str, candle: Candle) -> None:
        async with self.state_lock:
            state = self.states.get(sym)
            if state is None:
                return
            self._update_pair_state(state, candle)
            pos = self.positions.get(sym)
            if pos:
                await self._check_position(pos, candle)

            if self.early_fired.get(sym) == candle[0]:
                return
            if sym not in self.states or sym in self.positions:
                return
            event = self._closed_explosion_event(sym, candle)
            if not event:
                return
            self._write_candidate(event, early_entry=False)
            if self._can_open(sym):
                await self._open_position(event, early_entry=False)

    async def _on_candle_forming(self, sym: str, candle: Candle) -> None:
        if not bool(self.config["early_entry_enabled"]):
            return
        async with self.state_lock:
            if sym not in self.states or sym in self.positions:
                return
            if self.early_fired.get(sym) == candle[0]:
                return
            if not self._can_open(sym):
                return
            event = self._forming_explosion_event(sym, candle)
            if not event:
                return
            self.early_fired[sym] = candle[0]
            self._write_candidate(event, early_entry=True)
            await self._open_position(event, early_entry=True)

    def _closed_explosion_event(self, sym: str, candle: Candle) -> ExplosionEvent | None:
        ts_ms, open_px, _high, _low, close_px, _vol_contracts, _vol_usdt = candle
        move_pct = _pct_change(open_px, close_px)
        explosion_pct = abs(move_pct)
        vol_ratio = self._vol_ratio(sym, forming_candle=None)
        if vol_ratio is None:
            self._log(f"CANDIDATE BLOCK | {sym:<20} | insufficient_baseline")
            return None
        if explosion_pct < float(self.config["explosion_pct"]) or vol_ratio < float(self.config["vol_mult"]):
            return None
        return self._event_from_move(sym, candle, explosion_pct, vol_ratio)

    def _forming_explosion_event(self, sym: str, candle: Candle) -> ExplosionEvent | None:
        ts_ms, open_px, _high, _low, close_px, _vol_contracts, _vol_usdt = candle
        elapsed_sec = self._elapsed_from_candle_open(ts_ms)
        if elapsed_sec < 0 or elapsed_sec > float(self.config["early_trigger_sec"]):
            return None
        move_pct = _pct_change(open_px, close_px)
        explosion_pct = abs(move_pct)
        if explosion_pct < float(self.config["early_trigger_pct"]):
            return None
        vol_ratio = self._vol_ratio(sym, forming_candle=candle)
        if vol_ratio is None or vol_ratio < float(self.config["vol_mult"]):
            return None
        return self._event_from_move(sym, candle, explosion_pct, vol_ratio)

    def _event_from_move(self, sym: str, candle: Candle, explosion_pct: float, vol_ratio: float) -> ExplosionEvent:
        ts_ms, open_px, _high, _low, close_px, _vol_contracts, _vol_usdt = candle
        direction = "up" if close_px >= open_px else "down"
        reversal_side = "sell" if direction == "up" else "buy"
        return ExplosionEvent(
            sym=sym,
            direction=direction,
            reversal_side=reversal_side,
            explosion_pct=float(explosion_pct),
            vol_ratio=float(vol_ratio),
            candle_open=float(open_px),
            candle_close=float(close_px),
            ts_ms=int(ts_ms),
            ts=_ts_from_ms(int(ts_ms)),
        )

    async def _open_position(self, event: ExplosionEvent, early_entry: bool) -> None:
        entry = event.candle_close
        sl_pct = float(self.config["sl_pct"]) / 100.0
        tp_pct = float(self.config["tp_pct"]) / 100.0
        if event.reversal_side == "buy":
            sl = entry * (1.0 - sl_pct)
            tp = entry * (1.0 + tp_pct)
        else:
            sl = entry * (1.0 + sl_pct)
            tp = entry * (1.0 - tp_pct)

        pos = PaperPosition(
            signal_id=str(uuid.uuid4()),
            sym=event.sym,
            side=event.reversal_side,
            entry_price=float(entry),
            sl_price=float(sl),
            tp_price=float(tp),
            sl_initial=float(sl),
            mfe_pct=0.0,
            mae_pct=0.0,
            opened_at=time.time(),
            ts=_ts_utc(),
            early_entry=early_entry,
            explosion_pct=event.explosion_pct,
            vol_ratio=event.vol_ratio,
            sl_promoted="",
        )
        self.positions[event.sym] = pos
        self._write_signal(pos, event)
        self._log(
            f"ENTRY | {pos.sym:<20} | {pos.side.upper()} reversal | "
            f"entry={pos.entry_price:.8g} sl={pos.sl_price:.8g} tp={pos.tp_price:.8g} "
            f"| explosion={event.explosion_pct:+.2f}% vol={event.vol_ratio:.2f}x early={early_entry}"
        )
        await self._notify(_format_open_message(pos, event))

    async def _check_position(self, pos: PaperPosition, candle: Candle) -> None:
        _ts_ms, _open_px, high, low, close, _vol_contracts, _vol_usdt = candle
        if pos.side == "buy":
            favorable_price = high
            adverse_price = low
            favorable_pct = (favorable_price - pos.entry_price) / pos.entry_price * 100.0
            adverse_pct = (pos.entry_price - adverse_price) / pos.entry_price * 100.0
            tp_hit = high >= pos.tp_price
            sl_hit = low <= pos.sl_price
        else:
            favorable_price = low
            adverse_price = high
            favorable_pct = (pos.entry_price - favorable_price) / pos.entry_price * 100.0
            adverse_pct = (adverse_price - pos.entry_price) / pos.entry_price * 100.0
            tp_hit = low <= pos.tp_price
            sl_hit = high >= pos.sl_price

        pos.mfe_pct = max(pos.mfe_pct, favorable_pct)
        pos.mae_pct = max(pos.mae_pct, adverse_pct)
        self._promote_stop(pos)

        # Research MFE/MAE assumes favorable side first, then adverse side.
        if tp_hit:
            await self._close_position(pos, "TP", pos.tp_price)
            return

        if pos.side == "buy":
            sl_hit = low <= pos.sl_price
        else:
            sl_hit = high >= pos.sl_price
        if sl_hit:
            await self._close_position(pos, "SL", pos.sl_price)
            return

        elapsed_min = (time.time() - pos.opened_at) / 60.0
        if elapsed_min >= float(self.config["max_hold_min"]):
            await self._close_position(pos, "TIME", close)

    def _promote_stop(self, pos: PaperPosition) -> None:
        if pos.mfe_pct >= float(self.config["be_trigger_pct"]) and pos.sl_promoted == "":
            pos.sl_price = pos.entry_price
            pos.sl_promoted = "be"
            self._log(f"SL_PROMOTE | {pos.sym:<20} | be | sl={pos.sl_price:.8g}")

        if pos.mfe_pct >= float(self.config["lock_trigger_pct"]) and pos.sl_promoted == "be":
            lock_pct = float(self.config["lock_profit_pct"]) / 100.0
            if pos.side == "buy":
                pos.sl_price = pos.entry_price * (1.0 + lock_pct)
            else:
                pos.sl_price = pos.entry_price * (1.0 - lock_pct)
            pos.sl_promoted = "lock"
            self._log(f"SL_PROMOTE | {pos.sym:<20} | lock | sl={pos.sl_price:.8g}")

    async def _close_position(self, pos: PaperPosition, outcome: str, exit_price: float) -> None:
        self.positions.pop(pos.sym, None)
        self.cooldowns[pos.sym] = time.time() + float(self.config["cooldown_sec"])
        elapsed_m = (time.time() - pos.opened_at) / 60.0
        r_value = _r_value(pos, exit_price)
        payload = {
            "signal_id": pos.signal_id,
            "outcome": outcome,
            "exit_price": float(exit_price),
            "mfe_pct": pos.mfe_pct,
            "mae_pct": pos.mae_pct,
            "elapsed_m": elapsed_m,
            "sl_promoted": pos.sl_promoted,
            "early_entry": pos.early_entry,
        }
        _append_jsonl(LABELS_LOG, payload)
        self._log(
            f"CLOSE | {pos.sym:<20} | {outcome} | r={r_value:+.2f} "
            f"| exit={exit_price:.8g} mfe={pos.mfe_pct:.2f}% mae={pos.mae_pct:.2f}% "
            f"| held={elapsed_m:.1f}m sl_promoted={pos.sl_promoted or '-'}"
        )
        await self._notify(_format_close_message(pos, outcome, exit_price, r_value, elapsed_m))

    def _can_open(self, sym: str) -> bool:
        if sym not in self.eligible_pairs:
            return False
        if len(self.positions) >= int(self.config["max_concurrent"]):
            return False
        if sym in self.positions:
            return False
        if time.time() < self.cooldowns.get(sym, 0.0):
            return False
        return True

    def _update_pair_state(self, state: PairState, candle: Candle) -> None:
        ts_ms, open_px, _high, _low, close_px, _vol_contracts, _vol_usdt = candle
        state.last_candle_ts_ms = int(ts_ms)
        state.last_close = float(close_px)
        state.last_price_change_pct = abs(_pct_change(open_px, close_px))
        state.last_vol_ratio = self._vol_ratio(state.sym, forming_candle=None) or 0.0

    def _vol_ratio(self, sym: str, forming_candle: Candle | None) -> float | None:
        bars = int(self.config["baseline_bars"])
        history = self.feed.get_candles(sym, "candle1m", bars + 1)
        if forming_candle is None:
            if len(history) < bars + 1:
                return None
            current = history[-1]
            previous = history[-(bars + 1) : -1]
        else:
            if len(history) < bars:
                return None
            current = forming_candle
            previous = history[-bars:]
        baseline = sum(row[6] for row in previous) / float(bars)
        if baseline <= 0:
            return None
        return float(current[6]) / baseline

    def _write_candidate(self, event: ExplosionEvent, early_entry: bool) -> None:
        payload = {
            **asdict(event),
            "early_entry": early_entry,
            "reversal_direction": f"{event.direction}_explosion",
        }
        _append_jsonl(CANDIDATES_LOG, payload)
        self._log(
            f"CANDIDATE | {event.sym:<20} | {event.direction}_explosion "
            f"| reversal={event.reversal_side.upper()} | move={event.explosion_pct:+.2f}% "
            f"vol={event.vol_ratio:.2f}x early={early_entry}"
        )

    def _write_signal(self, pos: PaperPosition, event: ExplosionEvent) -> None:
        payload = {
            "signal_id": pos.signal_id,
            "sym": pos.sym,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "sl_price": pos.sl_price,
            "tp_price": pos.tp_price,
            "explosion_pct": pos.explosion_pct,
            "vol_ratio": pos.vol_ratio,
            "early_entry": pos.early_entry,
            "ts": pos.ts,
            "reversal_direction": f"{event.direction}_explosion",
        }
        _append_jsonl(SIGNALS_LOG, payload)

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
            self._log(
                f"HEARTBEAT | pairs={len(self.states)} subscribed_candles={subscribed} "
                f"open_positions={len(self.positions)} cooldowns={self._active_cooldowns()}"
            )

    async def _notify(self, text: str) -> None:
        chats = _pump_chat_ids(self.config)
        if not chats:
            return
        for chat_id in chats:
            try:
                await send_message_to(chat_id, text)
            except Exception as exc:
                self._log(f"NOTIFY error | chat={chat_id} | {exc}")

    def _active_cooldowns(self) -> int:
        now = time.time()
        return sum(1 for expires_at in self.cooldowns.values() if expires_at > now)

    def _elapsed_from_candle_open(self, ts_ms: int) -> float:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return (now_ms - int(ts_ms)) / 1000.0

    def _buffer_size(self) -> int:
        return max(int(self.config["baseline_bars"]) + 5, int(self.config["warmup_bars"]), 30)

    def _metadata_for(self, sym: str) -> PairMetadata:
        base = _base_asset(sym)
        return PairMetadata(
            sym=sym,
            base_asset=base,
            parent_network=self.parent_networks.get(base, "OTHER"),
        )

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


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _pct_change(start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    return (float(end) - float(start)) / float(start) * 100.0


def _r_value(pos: PaperPosition, exit_price: float) -> float:
    risk = abs(pos.entry_price - pos.sl_initial)
    if risk <= 0:
        return 0.0
    if pos.side == "buy":
        return (float(exit_price) - pos.entry_price) / risk
    return (pos.entry_price - float(exit_price)) / risk


def _format_open_message(pos: PaperPosition, event: ExplosionEvent) -> str:
    side_label = "LONG" if pos.side == "buy" else "SHORT"
    return (
        f"🔄 REVERSAL PAPER | {pos.sym}\n"
        f"{side_label} | entry {pos.entry_price:.8g} | SL {pos.sl_price:.8g} | TP {pos.tp_price:.8g}\n"
        f"Explosion: {event.explosion_pct:+.2f}% | vol_ratio: {event.vol_ratio:.2f}x | "
        f"early: {'yes' if pos.early_entry else 'no'}"
    )


def _format_close_message(
    pos: PaperPosition,
    outcome: str,
    exit_price: float,
    r_value: float,
    elapsed_m: float,
) -> str:
    icon = "✅" if outcome == "TP" else ("⏱" if outcome == "TIME" else "❌")
    return (
        f"{icon} {outcome} | {pos.sym} | {r_value:+.2f}R\n"
        f"exit {exit_price:.8g} | MFE {pos.mfe_pct:.2f}% | held {elapsed_m:.1f}m | "
        f"SL promoted: {pos.sl_promoted or '-'}"
    )


def _pump_chat_ids(config: dict[str, Any] | None = None) -> list[str]:
    ids: list[str] = []
    if config:
        for cid in config.get("extra_notify_chats", []):
            ids.append(str(cid).strip())
    for cid in os.environ.get("PUMP_CHAT_ID", "").split(","):
        cid = cid.strip()
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def _base_asset(sym: str) -> str:
    return sym.split("-", 1)[0].upper()


def _now() -> str:
    return datetime.utcnow().strftime("%H:%M:%S")


def _ts_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    return value.strip()


if __name__ == "__main__":
    asyncio.run(SmartPumpEngine().run())
