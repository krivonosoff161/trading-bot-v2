"""Pump engine v2 driven by the live screener active universe cache."""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import signal
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bt_pump_filters import fetch_ctvals
from src.data.ws_feed import Candle, WSFeed, _chunked

ACTIVE_UNIVERSE_PATH = Path(__file__).resolve().parent / "cache" / "active_universe.json"
LOG_DIR = ROOT / "logs" / "pump"
SIGNALS_LOG = LOG_DIR / "pump_signals.jsonl"
LABELS_LOG = LOG_DIR / "pump_labels.jsonl"
ENGINE_LOG = LOG_DIR / "ws_pump_engine_v2.log"

DEFAULT_CONFIG = {
    "vol_mult": 1.5,
    "price_pct": 1.2,
    "alert_cooldown_sec": 120,
    "min_usd_vol": 30_000,
    "paper_tp_pct": 1.5,
    "paper_sl_pct": 0.8,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 2.5,
    "paper_max_hold_min": 9999,
    "paper_position_usd": 100.0,
    "paper_balance_usd": 1000.0,
    "max_open_positions": 3,
    "fee_rt_pct": 0.10,
    "heartbeat_interval": 30,
    # Circuit breaker
    "cb_max_consecutive_sl": 3,    # SL подряд → cooldown
    "cb_cooldown_min": 30,         # минут cooldown после серии потерь
    "cb_hourly_sl_limit": 3,       # SL за час → blacklist до конца сессии
    "cb_daily_loss_pct": 5.0,      # дневной убыток % → halt all
}


@dataclass(slots=True)
class OpenPosition:
    signal_id: str
    sym: str
    side: str
    entry_price: float
    sl_price: float
    tp_price: float
    opened_at: pd.Timestamp
    position_usd: float
    atr: float


def _now() -> str:
    return datetime.utcnow().strftime("%H:%M:%S")


def _ts_utc(dt: pd.Timestamp) -> str:
    if dt.tzinfo is None:
        return dt.tz_localize("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_config() -> dict[str, Any]:
    config_path = ROOT / "config.yaml"
    cfg = DEFAULT_CONFIG.copy()
    try:
        with config_path.open(encoding="utf-8") as f:
            project = yaml.safe_load(f) or {}
        cfg.update(project.get("pump_engine", {}) or {})
    except Exception:
        pass
    return cfg


def _setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ws_pump_engine_v2")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        ENGINE_LOG,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


class PumpEngineV2:
    def __init__(self) -> None:
        self.config = _load_config()
        self.logger = _setup_logger()
        self.feed = WSFeed(pairs=[], bars=["candle1m"], buffer_size=30)
        self.stop_event = asyncio.Event()
        self.universe_lock = asyncio.Lock()

        self.target_universe: list[str] = []
        self.universe_meta: dict[str, dict[str, str]] = {}
        self.subscribed_pairs: list[str] = []
        self.last_signal_wall: dict[str, float] = {}
        self.open_positions: dict[str, OpenPosition] = {}
        self.last_tick_ts: dict[str, pd.Timestamp] = {}
        self.ctval: dict[str, float] = {}

        # Circuit breaker state
        self.cb_consecutive_sl: dict[str, int] = {}
        self.cb_cooldown_until: dict[str, float] = {}   # wall time
        self.cb_blacklist: set[str] = set()              # session-long ban
        self.cb_recent_sl_ts: dict[str, list[float]] = {}  # SL timestamps for hourly check
        self.cb_daily_pnl_pct: float = 0.0
        self.cb_halted: bool = False

    async def run(self) -> None:
        feed_task: asyncio.Task[None] | None = None
        poll_task: asyncio.Task[None] | None = None
        heartbeat_task: asyncio.Task[None] | None = None
        stop_task: asyncio.Task[bool] | None = None

        self._install_signal_handlers()
        self.feed.on_candle_close("candle1m", self._on_candle_close)
        self.ctval = await asyncio.to_thread(fetch_ctvals)
        self._log(
            "started | auto_trade=false | "
            f"vol_mult={self.config['vol_mult']} price_pct={self.config['price_pct']} "
            f"sl_atr_mult={self.config['sl_atr_mult']} tp_atr_mult={self.config['tp_atr_mult']}"
        )

        try:
            initial_meta = self._read_active_universe()
            initial_active = sorted(initial_meta)
            self.universe_meta = initial_meta
            if initial_active:
                await self._ensure_pairs_ready(initial_active)
                await self.feed.warmup()
                self.subscribed_pairs = list(initial_active)

            feed_task = asyncio.create_task(self.feed.start(), name="pump_v2.feed")
            poll_task = asyncio.create_task(self._poll_universe_loop(), name="pump_v2.universe")
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="pump_v2.heartbeat")
            stop_task = asyncio.create_task(self.stop_event.wait(), name="pump_v2.stop")

            done, _ = await asyncio.wait(
                {feed_task, poll_task, heartbeat_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                if task is stop_task:
                    continue
                exc = task.exception()
                if exc:
                    raise exc
                if task is feed_task:
                    raise RuntimeError("WSFeed stopped unexpectedly")
        finally:
            self.stop_event.set()
            for task in (feed_task, poll_task, heartbeat_task, stop_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (feed_task, poll_task, heartbeat_task, stop_task) if task is not None),
                return_exceptions=True,
            )
            await self.feed.stop()
            self._log(f"stopped | subscribed={len(self.subscribed_pairs)} | open_positions={len(self.open_positions)}")

    async def _poll_universe_loop(self) -> None:
        while not self.stop_event.is_set():
            await self._refresh_universe()
            await asyncio.sleep(30)

    async def _refresh_universe(self) -> None:
        async with self.universe_lock:
            active_meta = self._read_active_universe()
            self.universe_meta = active_meta
            active = sorted(active_meta)
            self.target_universe = list(active)
            keep_open = {sym for sym in self.open_positions}
            desired_subscription = sorted(set(active) | keep_open)
            current = set(self.subscribed_pairs)
            target = set(desired_subscription)
            added = sorted(target - current)
            removed = sorted(current - target)

            if not added and not removed:
                return

            if added:
                await self._ensure_pairs_ready(added)
                await self._warmup_pairs(added)
                await self._change_subscriptions(added, op="subscribe")

            if removed:
                await self._change_subscriptions(removed, op="unsubscribe")
                for sym in removed:
                    self.feed.buffers.pop(sym, None)
                    self.feed.last_close_ts.pop(sym, None)
                    self.last_signal_wall.pop(sym, None)
                    self.last_tick_ts.pop(sym, None)
                    self.feed.pairs = [pair for pair in self.feed.pairs if pair != sym]

            self.subscribed_pairs = list(desired_subscription)
            self.feed.pairs = list(desired_subscription)
            plus = ",".join(added) if added else "-"
            minus = ",".join(removed) if removed else "-"
            self._log(f"UNIVERSE update | active={len(active)} | added={plus} | removed={minus}")

    async def _ensure_pairs_ready(self, pairs: list[str]) -> None:
        for sym in pairs:
            if sym not in self.feed.buffers:
                self.feed.pairs.append(sym)
                self.feed.buffers[sym] = {bar: deque(maxlen=self.feed.buffer_size) for bar in self.feed.bars}
            if sym not in self.last_signal_wall:
                self.last_signal_wall[sym] = 0.0

    async def _warmup_pairs(self, pairs: list[str]) -> None:
        if not pairs:
            return
        await self.feed.connected_event.wait()
        for sym in pairs:
            try:
                rows = await self.feed._rest_candles(sym, "1m", 20)
            except Exception as exc:
                self._log(f"WARMUP fail | {sym} | {exc}")
                continue
            closed = [row for row in rows if len(row) > 8 and row[8] == "1"]
            closed.reverse()
            buf = self.feed.buffers[sym]["candle1m"]
            buf.clear()
            for row in closed:
                candle = self.feed._parse_candle(row)
                buf.append(candle)
                self.feed.last_close_ts[sym]["candle1m"] = candle[0]

    async def _change_subscriptions(self, pairs: list[str], op: str) -> None:
        if not pairs:
            return
        await self.feed.connected_event.wait()
        if not self.feed.ws:
            return
        args = [{"channel": "candle1m", "instId": sym} for sym in pairs]
        for chunk in _chunked(args, 45):
            await self.feed.ws.send_str(json.dumps({"op": op, "args": chunk}))

    def _cb_check(self, sym: str) -> bool:
        """Return False if sym is blocked by circuit breaker."""
        if self.cb_halted:
            return False
        if sym in self.cb_blacklist:
            return False
        until = self.cb_cooldown_until.get(sym, 0.0)
        if time.time() < until:
            return False
        return True

    def _cb_record_exit(self, sym: str, is_sl: bool, net_pct: float) -> None:
        """Update circuit breaker state after a trade closes."""
        self.cb_daily_pnl_pct += net_pct

        if self.cb_daily_pnl_pct <= -float(self.config["cb_daily_loss_pct"]):
            self.cb_halted = True
            self._log(f"CB HALT | daily_pnl={self.cb_daily_pnl_pct:.2f}% — all trading stopped")
            return

        if not is_sl:
            self.cb_consecutive_sl[sym] = 0
            return

        # Track consecutive SL
        self.cb_consecutive_sl[sym] = self.cb_consecutive_sl.get(sym, 0) + 1

        # Track SL timestamps for hourly limit
        now = time.time()
        sl_ts = self.cb_recent_sl_ts.setdefault(sym, [])
        sl_ts.append(now)
        sl_ts[:] = [t for t in sl_ts if now - t < 3600]  # keep last 1 hour only

        hourly_limit = int(self.config["cb_hourly_sl_limit"])
        if len(sl_ts) >= hourly_limit:
            self.cb_blacklist.add(sym)
            self._log(f"CB BLACKLIST | {sym} | {len(sl_ts)} SL in 1h — banned for session")
            return

        consec_limit = int(self.config["cb_max_consecutive_sl"])
        if self.cb_consecutive_sl[sym] >= consec_limit:
            cooldown_sec = float(self.config["cb_cooldown_min"]) * 60
            self.cb_cooldown_until[sym] = now + cooldown_sec
            self._log(
                f"CB COOLDOWN | {sym} | {self.cb_consecutive_sl[sym]} SL in a row "
                f"— blocked {self.config['cb_cooldown_min']}m"
            )

    async def _on_candle_close(self, sym: str, candle: Candle) -> None:
        self.last_tick_ts[sym] = pd.Timestamp.utcnow()
        self._check_position(sym, candle)

        if sym not in self.target_universe:
            return
        if sym in self.open_positions:
            return
        if len(self.open_positions) >= int(self.config["max_open_positions"]):
            return
        if not self._cb_check(sym):
            return

        history = self.feed.get_candles(sym, "candle1m", 11)
        if len(history) < 11:
            return

        current = history[-1]
        previous = history[-11:-1]
        baseline_vol = sum(row[5] for row in previous) / 10.0
        if baseline_vol <= 0 or current[1] <= 0:
            return

        vol_spike_1m = current[5] / baseline_vol
        price_pct_1m = abs(current[4] - current[1]) / current[1] * 100.0
        if vol_spike_1m < float(self.config["vol_mult"]) or price_pct_1m < float(self.config["price_pct"]):
            return

        # Stagnation filter: high vol but price barely moved → MM wash trading
        if vol_spike_1m >= 2.0 and price_pct_1m < 0.5:
            return

        atr = sum(abs(row[2] - row[3]) for row in previous) / 10.0
        if atr <= 0:
            return

        dollar_vol = current[6]  # row[7] = volCcyQuote (exact USDT volume)
        if dollar_vol < float(self.config["min_usd_vol"]):
            return

        now_wall = time.time()
        if now_wall - self.last_signal_wall.get(sym, 0.0) < float(self.config["alert_cooldown_sec"]):
            return
        self.last_signal_wall[sym] = now_wall

        side = self._side_from_universe(sym)
        await self._open_position(
            sym=sym,
            candle=current,
            side=side,
            vol_spike=vol_spike_1m,
            price_move=price_pct_1m,
            baseline_vol=baseline_vol,
            dollar_vol=dollar_vol,
            atr=atr,
        )

    async def _open_position(
        self,
        *,
        sym: str,
        candle: Candle,
        side: str,
        vol_spike: float,
        price_move: float,
        baseline_vol: float,
        dollar_vol: float,
        atr: float,
    ) -> None:
        ts = pd.Timestamp(candle[0], unit="ms", tz="UTC")
        entry_price = float(candle[4])
        sl_mult = float(self.config["sl_atr_mult"])
        tp_mult = float(self.config["tp_atr_mult"])

        if side == "buy":
            sl_price = round(entry_price - atr * sl_mult, 8)
            tp_price = round(entry_price + atr * tp_mult, 8)
            direction = "PUMP"
        else:
            sl_price = round(entry_price + atr * sl_mult, 8)
            tp_price = round(entry_price - atr * tp_mult, 8)
            direction = "DUMP"

        signal_id = str(uuid.uuid4())[:12]
        signal_row = {
            "signal_id": signal_id,
            "type": "ENTRY",
            "ts_utc": _ts_utc(ts),
            "ts_wall": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sym": sym,
            "direction": direction,
            "hour_utc": int(ts.hour),
            "screener_tier": 1,
            "signal_close": entry_price,
            "entry_open_price": entry_price,
            "paper_tp": tp_price,
            "paper_sl": sl_price,
            "vol_ratio": round(vol_spike, 3),
            "pct_move": round(price_move, 3),
            "avg_vol": round(baseline_vol, 2),
            "dollar_vol": round(dollar_vol, 0),
            "ct_val": self.ctval.get(sym, 1.0),
            "latency_ms": 0,
            "filters_passed": ["A", "B"],
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
            f.write(json.dumps(signal_row, ensure_ascii=False) + "\n")

        self.open_positions[sym] = OpenPosition(
            signal_id=signal_id,
            sym=sym,
            side=side,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            opened_at=ts,
            position_usd=float(self.config["paper_position_usd"]),
            atr=atr,
        )
        self._log(
            f"OPEN  | {sym} {side.upper():<4} | entry={entry_price:.8g} sl={sl_price:.8g} "
            f"tp={tp_price:.8g} | atr={atr:.8g}"
        )

    def _check_position(self, sym: str, candle: Candle) -> None:
        pos = self.open_positions.get(sym)
        if not pos:
            return

        ts_ms, _open, high, low, close, _vol, _vol_usd = candle
        exit_reason: str | None = None
        exit_price: float | None = None

        if pos.side == "buy":
            if low <= pos.sl_price:
                exit_reason, exit_price = "SL", pos.sl_price
            elif high >= pos.tp_price:
                exit_reason, exit_price = "TP", pos.tp_price
        else:
            if high >= pos.sl_price:
                exit_reason, exit_price = "SL", pos.sl_price
            elif low <= pos.tp_price:
                exit_reason, exit_price = "TP", pos.tp_price

        if exit_reason is None or exit_price is None:
            hold_min = (pd.Timestamp(ts_ms, unit="ms", tz="UTC") - pos.opened_at).total_seconds() / 60.0
            if hold_min >= float(self.config["paper_max_hold_min"]):
                exit_reason, exit_price = "TIME", close

        if exit_reason is None or exit_price is None:
            return

        if pos.side == "buy":
            gross = (exit_price - pos.entry_price) / pos.entry_price * 100.0
        else:
            gross = (pos.entry_price - exit_price) / pos.entry_price * 100.0
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

        self._log(f"CLOSE | {sym:<18} | {exit_reason} | pnl={net:+.2f}% | hold={hold_min:.0f}m")
        del self.open_positions[sym]
        self._cb_record_exit(sym, is_sl=(exit_reason == "SL"), net_pct=net)
        if sym not in self.target_universe:
            asyncio.create_task(self._prune_inactive_symbol(sym))

    async def _prune_inactive_symbol(self, sym: str) -> None:
        async with self.universe_lock:
            if sym in self.open_positions or sym in self.target_universe or sym not in self.subscribed_pairs:
                return
            await self._change_subscriptions([sym], op="unsubscribe")
            self.subscribed_pairs = [pair for pair in self.subscribed_pairs if pair != sym]
            self.feed.pairs = [pair for pair in self.feed.pairs if pair != sym]
            self.feed.buffers.pop(sym, None)
            self.feed.last_close_ts.pop(sym, None)
            self.last_signal_wall.pop(sym, None)
            self.last_tick_ts.pop(sym, None)
            self._log(f"UNIVERSE update | active={len(self.target_universe)} | added=- | removed={sym}")

    async def _heartbeat_loop(self) -> None:
        interval = int(self.config["heartbeat_interval"])
        while not self.stop_event.is_set():
            await asyncio.sleep(interval)
            now_ts = pd.Timestamp.utcnow()
            stale = 0
            for sym in self.subscribed_pairs:
                last = self.last_tick_ts.get(sym)
                if last is None:
                    continue
                if (now_ts - last).total_seconds() > 120:
                    stale += 1
            self._log(
                f"HEARTBEAT | subscribed={len(self.subscribed_pairs)} | target={len(self.target_universe)} "
                f"| open={len(self.open_positions)} | stale={stale}"
            )

    def _read_active_universe(self) -> dict[str, dict[str, str]]:
        if not ACTIVE_UNIVERSE_PATH.exists():
            return {}
        try:
            payload = json.loads(ACTIVE_UNIVERSE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
        meta = payload.get("symbols", {})
        if isinstance(meta, dict) and meta:
            return {str(sym): info for sym, info in meta.items() if isinstance(info, dict)}
        active = payload.get("active", [])
        if not isinstance(active, list):
            return {}
        return {str(sym): {"direction": "up"} for sym in active}

    def _side_from_universe(self, sym: str) -> str:
        direction = str(self.universe_meta.get(sym, {}).get("direction", "up")).lower()
        return "buy" if direction == "up" else "sell"

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGINT", "SIGTERM"):
            if not hasattr(signal, sig_name):
                continue
            try:
                loop.add_signal_handler(getattr(signal, sig_name), self.stop_event.set)
            except NotImplementedError:
                pass

    def _log(self, message: str) -> None:
        self.logger.info(f"[{_now()}] {message}")


if __name__ == "__main__":
    asyncio.run(PumpEngineV2().run())
