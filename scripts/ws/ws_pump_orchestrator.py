"""
Pump Orchestrator — replaces ws_pump_engine_v2.py.

Architecture:
  ws_screener_live → active_universe.json → Orchestrator
                                               ├── MAIN POOL   (trade screener pairs, engine direction)
                                               └── COUNTER POOL (opposite direction after N SLs)

Per-pair flow:
  vol spike → enter in signal candle direction
  2 SL streak in main → move to COUNTER (opposite direction)
  2 SL streak in counter → 24h ban
  TP resets SL streak
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
from collections import deque
from dataclasses import dataclass, field
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
ENGINE_LOG = LOG_DIR / "ws_pump_orchestrator.log"

DEFAULT_CONFIG: dict[str, Any] = {
    "max_main_slots": 4,
    "universe_poll_sec": 1,
    "vol_mult": 1.5,
    "price_pct": 1.2,
    "min_usd_vol": 30_000,
    "pump_phase_max_pct": 5.0,
    "alert_cooldown_sec": 120,
    "position_usd": 100.0,
    "fee_rt_pct": 0.10,
    "sl_atr_mult": 1.5,
    "tp_atr_mult": 2.5,
    "paper_max_hold_min": 9999,
    "main_sl_to_counter": 2,
    "counter_sl_to_ban": 2,
    "cb_cooldown_sl": 3,
    "cb_cooldown_min": 30,
    "cb_daily_loss_pct": 5.0,
    "heartbeat_interval": 30,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
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


@dataclass
class PairState:
    sym: str
    section: str           # "main" | "counter" | "banned"
    main_direction: str    # direction when first entered main ("buy" | "sell")
    baseline_price: float  # price when pair entered pool
    added_at: float        # wall time
    last_signal_at: float  # last screener spike timestamp (for eviction)
    main_sl_streak: int = 0
    counter_sl_streak: int = 0
    total_sl_streak: int = 0   # for cb_cooldown_sl
    sl_today: int = 0
    tp_today: int = 0
    trade_count: int = 0       # how many times pair has been traded (stats)
    cooldown_until: float = 0.0
    banned_until: float = 0.0
    position: OpenPosition | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().strftime("%H:%M:%S")


def _ts_utc(dt: pd.Timestamp | None = None) -> str:
    if dt is None:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    if dt.tzinfo is None:
        return dt.tz_localize("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_config() -> dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    try:
        with (ROOT / "config.yaml").open(encoding="utf-8") as f:
            project = yaml.safe_load(f) or {}
        cfg.update(project.get("pump_orchestrator", {}) or {})
    except Exception:
        pass
    return cfg


def _setup_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ws_pump_orchestrator")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    fmt = logging.Formatter("%(message)s")
    fh = logging.handlers.RotatingFileHandler(
        ENGINE_LOG, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class PumpOrchestrator:
    def __init__(self) -> None:
        self.config = _load_config()
        self.logger = _setup_logger()
        self.feed = WSFeed(pairs=[], bars=["candle1m"], buffer_size=30)
        self.stop_event = asyncio.Event()
        self.pool_lock = asyncio.Lock()

        # Pair pool: sym → PairState
        self.pool: dict[str, PairState] = {}

        # Pairs currently subscribed in WSFeed
        self.subscribed: set[str] = set()

        # Last mtime of active_universe.json for change detection
        self._universe_mtime: float = 0.0

        # Contract values for position sizing
        self.ctval: dict[str, float] = {}

        # Global daily halt
        self.cb_halted: bool = False
        self.cb_daily_pnl: float = 0.0

        # Last signal wall time per pair (cooldown between entries)
        self.last_signal_wall: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._install_signal_handlers()
        self.feed.on_candle_close("candle1m", self._on_candle_close)
        self.feed.on_candle_update("candle1m", self._on_candle_update)
        self.ctval = await asyncio.to_thread(fetch_ctvals)

        self._log(
            f"started | max_main_slots={self.config['max_main_slots']} "
            f"vol_mult={self.config['vol_mult']} price_pct={self.config['price_pct']} "
            f"sl_atr={self.config['sl_atr_mult']} tp_atr={self.config['tp_atr_mult']}"
        )

        feed_task = asyncio.create_task(self.feed.start(), name="orchestrator.feed")
        universe_task = asyncio.create_task(self._universe_watch_loop(), name="orchestrator.universe")
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="orchestrator.heartbeat")
        stop_task = asyncio.create_task(self.stop_event.wait(), name="orchestrator.stop")

        try:
            done, _ = await asyncio.wait(
                {feed_task, universe_task, heartbeat_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                if task is stop_task:
                    continue
                exc = task.exception()
                if exc:
                    raise exc
        finally:
            self.stop_event.set()
            for t in (feed_task, universe_task, heartbeat_task, stop_task):
                if not t.done():
                    t.cancel()
            await asyncio.gather(feed_task, universe_task, heartbeat_task, stop_task, return_exceptions=True)
            await self.feed.stop()
            self._log(f"stopped | pool={len(self.pool)} | open={sum(1 for p in self.pool.values() if p.position)}")

    # ------------------------------------------------------------------
    # Universe watcher (push model: react to file mtime change)
    # ------------------------------------------------------------------

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
            await self._refresh_pool()

    async def _refresh_pool(self) -> None:
        screener_meta = self._read_universe()
        async with self.pool_lock:
            now = time.time()
            added, removed = [], []

            # Add new pairs from screener
            for sym, meta in screener_meta.items():
                if sym in self.pool:
                    # Update last_signal_at so pair isn't evicted
                    self.pool[sym].last_signal_at = now
                    continue

                # Check if banned
                state = self.pool.get(sym)
                if state and state.section == "banned" and now < state.banned_until:
                    continue

                # Pool full → evict oldest-without-signal if needed
                main_count = sum(1 for p in self.pool.values() if p.section == "main")
                if main_count >= int(self.config["max_main_slots"]):
                    evicted = self._evict_candidate()
                    if evicted is None:
                        continue  # all slots taken by active positions
                    await self._remove_pair(evicted)
                    removed.append(evicted)

                # Determine initial direction from screener meta
                direction = "buy" if meta.get("direction", "up") == "up" else "sell"
                first_price = self._get_last_price(sym) or 0.0

                self.pool[sym] = PairState(
                    sym=sym,
                    section="main",
                    main_direction=direction,
                    baseline_price=first_price,
                    added_at=now,
                    last_signal_at=now,
                )
                await self._ensure_subscribed([sym])
                added.append(sym)

            # Remove pairs no longer in screener (only if no open position)
            for sym in list(self.pool.keys()):
                state = self.pool[sym]
                if sym not in screener_meta and state.position is None and state.section != "banned":
                    await self._remove_pair(sym)
                    removed.append(sym)

            if added or removed:
                self._log(
                    f"POOL update | main={sum(1 for p in self.pool.values() if p.section == 'main')} "
                    f"counter={sum(1 for p in self.pool.values() if p.section == 'counter')} "
                    f"| added={','.join(added) or '-'} | removed={','.join(removed) or '-'}"
                )

    def _evict_candidate(self) -> str | None:
        """Return sym of main-section pair with no open position, oldest without signal."""
        candidates = [
            p for p in self.pool.values()
            if p.section == "main" and p.position is None
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda p: p.last_signal_at).sym

    def _get_last_price(self, sym: str) -> float | None:
        candles = self.feed.get_candles(sym, "candle1m", 1)
        if candles:
            return float(candles[-1][4])
        return None

    # ------------------------------------------------------------------
    # Candle callbacks
    # ------------------------------------------------------------------

    async def _on_candle_update(self, sym: str, candle: Candle) -> None:
        """1-second live update — only used to monitor open positions."""
        async with self.pool_lock:
            state = self.pool.get(sym)
            if state and state.position:
                self._check_position_live(state, candle)

    async def _on_candle_close(self, sym: str, candle: Candle) -> None:
        async with self.pool_lock:
            state = self.pool.get(sym)
            if not state:
                return

            # 1. Check and close open position
            if state.position:
                closed = self._close_position_if_hit(state, candle)
                if closed:
                    return  # cooldown reset, no new entry this candle

            # 2. Global halt check
            if self.cb_halted:
                return

            # 3. Cooldown / ban check
            now = time.time()
            if now < state.cooldown_until or now < state.banned_until:
                return

            # 4. No position — evaluate entry
            if state.position is None:
                await self._evaluate_entry(state, candle)

    # ------------------------------------------------------------------
    # Position: live check (1-sec updates)
    # ------------------------------------------------------------------

    def _check_position_live(self, state: PairState, candle: Candle) -> None:
        pos = state.position
        if not pos:
            return
        _, _o, high, low, close, _v, _vu = candle
        if pos.side == "buy":
            if low <= pos.sl_price or high >= pos.tp_price:
                # Will be caught properly on candle close; live update just for early awareness
                pass
        else:
            if high >= pos.sl_price or low <= pos.tp_price:
                pass

    # ------------------------------------------------------------------
    # Position: close on candle close
    # ------------------------------------------------------------------

    def _close_position_if_hit(self, state: PairState, candle: Candle) -> bool:
        pos = state.position
        if not pos:
            return False

        ts_ms, _o, high, low, close, _v, _vu = candle
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

        if exit_reason is None:
            hold_min = (pd.Timestamp(ts_ms, unit="ms", tz="UTC") - pos.opened_at).total_seconds() / 60
            if hold_min >= float(self.config["paper_max_hold_min"]):
                exit_reason, exit_price = "TIME", close

        if exit_reason is None or exit_price is None:
            return False

        # Calculate PnL
        if pos.side == "buy":
            gross = (exit_price - pos.entry_price) / pos.entry_price * 100.0
        else:
            gross = (pos.entry_price - exit_price) / pos.entry_price * 100.0
        net = gross - float(self.config["fee_rt_pct"])
        hold_min = (pd.Timestamp(ts_ms, unit="ms", tz="UTC") - pos.opened_at).total_seconds() / 60

        # Log label
        label = {
            "signal_id": pos.signal_id,
            "type": "EXIT",
            "sym": state.sym,
            "exit_reason": exit_reason,
            "entry_price": pos.entry_price,
            "exit_price": round(exit_price, 8),
            "gross_pnl_pct": round(gross, 4),
            "fee_pct": float(self.config["fee_rt_pct"]),
            "net_pnl_pct": round(net, 4),
            "hold_min": round(hold_min, 1),
            "section": state.section,
            "trade_count": state.trade_count,
            "opened_at": pos.opened_at.isoformat(),
            "closed_at": pd.Timestamp(ts_ms, unit="ms", tz="UTC").isoformat(),
        }
        with LABELS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(label, ensure_ascii=False) + "\n")

        self._log(f"CLOSE | {state.sym:<20} | {exit_reason} | pnl={net:+.2f}% | hold={hold_min:.0f}m | section={state.section}")

        # Reset cooldown on any close (prevents immediate re-entry on same candle)
        self.last_signal_wall[state.sym] = time.time()
        state.position = None

        # Update daily PnL + global halt check
        self.cb_daily_pnl += net
        if self.cb_daily_pnl <= -float(self.config["cb_daily_loss_pct"]):
            self.cb_halted = True
            self._log(f"CB HALT | daily_pnl={self.cb_daily_pnl:.2f}% — all trading stopped")
            return True

        is_sl = (exit_reason == "SL")
        self._update_streaks(state, is_sl)
        return True

    def _update_streaks(self, state: PairState, is_sl: bool) -> None:
        now = time.time()
        if is_sl:
            state.sl_today += 1
            state.total_sl_streak += 1

            # Global cooldown after N consecutive SLs
            if state.total_sl_streak >= int(self.config["cb_cooldown_sl"]):
                cooldown_sec = float(self.config["cb_cooldown_min"]) * 60
                state.cooldown_until = now + cooldown_sec
                state.total_sl_streak = 0
                self._log(f"CB COOLDOWN | {state.sym} | {self.config['cb_cooldown_sl']} SL streak → {self.config['cb_cooldown_min']}m pause")

            if state.section == "main":
                state.main_sl_streak += 1
                if state.main_sl_streak >= int(self.config["main_sl_to_counter"]):
                    state.section = "counter"
                    state.counter_sl_streak = 0
                    self._log(f"COUNTER | {state.sym} | {state.main_sl_streak} SL in main → switching to counter direction")

            elif state.section == "counter":
                state.counter_sl_streak += 1
                if state.counter_sl_streak >= int(self.config["counter_sl_to_ban"]):
                    ban_sec = 24 * 3600
                    state.banned_until = now + ban_sec
                    state.section = "banned"
                    self._log(f"BAN | {state.sym} | {state.counter_sl_streak} SL in counter → 24h ban")
        else:
            # TP resets streaks
            state.tp_today += 1
            state.main_sl_streak = 0
            state.counter_sl_streak = 0
            state.total_sl_streak = 0

    # ------------------------------------------------------------------
    # Entry evaluation
    # ------------------------------------------------------------------

    async def _evaluate_entry(self, state: PairState, candle: Candle) -> None:
        sym = state.sym
        now = time.time()

        # Cooldown between entries
        if now - self.last_signal_wall.get(sym, 0.0) < float(self.config["alert_cooldown_sec"]):
            return

        # Need 11 bars history (10 baseline + 1 current)
        history = self.feed.get_candles(sym, "candle1m", 11)
        if len(history) < 11:
            return

        current = history[-1]
        previous = history[-11:-1]

        baseline_vol = sum(row[5] for row in previous) / 10.0
        if baseline_vol <= 0 or current[1] <= 0:
            return

        vol_spike = current[5] / baseline_vol
        price_move = abs(current[4] - current[1]) / current[1] * 100.0

        if vol_spike < float(self.config["vol_mult"]) or price_move < float(self.config["price_pct"]):
            return

        # Stagnation filter: vol spike but price barely moved
        if vol_spike >= 2.0 and price_move < 0.5:
            return

        dollar_vol = current[6]
        if dollar_vol < float(self.config["min_usd_vol"]):
            return

        atr = sum(abs(row[2] - row[3]) for row in previous) / 10.0
        if atr <= 0:
            return

        # Determine direction from signal candle
        if state.section == "main":
            side = "buy" if current[4] > current[1] else "sell"
            state.main_direction = side
        else:
            # Counter: opposite of main direction
            side = "sell" if state.main_direction == "buy" else "buy"

        # Pump phase gate: don't enter if already far from baseline (main only)
        if state.section == "main" and state.baseline_price > 0:
            phase_pct = abs(current[4] - state.baseline_price) / state.baseline_price * 100.0
            if phase_pct > float(self.config["pump_phase_max_pct"]):
                return

        self.last_signal_wall[sym] = now
        state.trade_count += 1
        await self._open_position(state, current, side, vol_spike, price_move, dollar_vol, atr)

    # ------------------------------------------------------------------
    # Open position
    # ------------------------------------------------------------------

    async def _open_position(
        self,
        state: PairState,
        candle: Candle,
        side: str,
        vol_spike: float,
        price_move: float,
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
        else:
            sl_price = round(entry_price + atr * sl_mult, 8)
            tp_price = round(entry_price - atr * tp_mult, 8)

        signal_id = str(uuid.uuid4())[:12]
        sig = {
            "signal_id": signal_id,
            "type": "ENTRY",
            "ts_utc": _ts_utc(ts),
            "sym": state.sym,
            "section": state.section,
            "trade_count": state.trade_count,
            "direction": "PUMP" if side == "buy" else "DUMP",
            "signal_close": entry_price,
            "paper_tp": tp_price,
            "paper_sl": sl_price,
            "vol_ratio": round(vol_spike, 3),
            "pct_move": round(price_move, 3),
            "dollar_vol": round(dollar_vol, 0),
            "atr": round(atr, 8),
        }
        with SIGNALS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(sig, ensure_ascii=False) + "\n")

        state.position = OpenPosition(
            signal_id=signal_id,
            sym=state.sym,
            side=side,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            opened_at=ts,
            position_usd=float(self.config["position_usd"]),
            atr=atr,
        )
        self._log(
            f"OPEN  | {state.sym:<20} {side.upper():<4} | entry={entry_price:.8g} "
            f"sl={sl_price:.8g} tp={tp_price:.8g} | atr={atr:.8g} "
            f"| section={state.section} vol={vol_spike:.1f}x"
        )

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    async def _ensure_subscribed(self, pairs: list[str]) -> None:
        new = [sym for sym in pairs if sym not in self.subscribed]
        if not new:
            return
        for sym in new:
            if sym not in self.feed.buffers:
                self.feed.pairs.append(sym)
                self.feed.buffers[sym] = {"candle1m": deque(maxlen=30)}
                self.feed.last_close_ts[sym] = {}
        if self.feed.connected_event.is_set() and self.feed.ws:
            args = [{"channel": "candle1m", "instId": sym} for sym in new]
            for chunk in _chunked(args, 45):
                await self.feed.ws.send_str(json.dumps({"op": "subscribe", "args": chunk}))
            # Warmup new pairs
            for sym in new:
                await self._warmup_pair(sym)
        self.subscribed.update(new)

    async def _remove_pair(self, sym: str) -> None:
        state = self.pool.get(sym)
        if state and state.position:
            return  # don't remove while position is open
        self.pool.pop(sym, None)
        if sym in self.subscribed:
            if self.feed.connected_event.is_set() and self.feed.ws:
                args = [{"channel": "candle1m", "instId": sym}]
                await self.feed.ws.send_str(json.dumps({"op": "unsubscribe", "args": args}))
            self.subscribed.discard(sym)
            self.feed.buffers.pop(sym, None)
            self.feed.last_close_ts.pop(sym, None)
            self.last_signal_wall.pop(sym, None)
            if sym in self.feed.pairs:
                self.feed.pairs.remove(sym)

    async def _warmup_pair(self, sym: str) -> None:
        await self.feed.connected_event.wait()
        try:
            rows = await self.feed._rest_candles(sym, "1m", 20)
            closed = [r for r in rows if len(r) > 8 and r[8] == "1"]
            closed.reverse()
            buf = self.feed.buffers[sym]["candle1m"]
            buf.clear()
            for row in closed:
                c = self.feed._parse_candle(row)
                buf.append(c)
                self.feed.last_close_ts[sym]["candle1m"] = c[0]
            # Set baseline price from warmup
            state = self.pool.get(sym)
            if state and state.baseline_price == 0.0 and closed:
                state.baseline_price = float(closed[-1][4])
        except Exception as exc:
            self._log(f"WARMUP fail | {sym} | {exc}")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _read_universe(self) -> dict[str, dict]:
        try:
            payload = json.loads(ACTIVE_UNIVERSE_PATH.read_text(encoding="utf-8"))
            meta = payload.get("symbols", {})
            if isinstance(meta, dict) and meta:
                return {str(sym): info for sym, info in meta.items() if isinstance(info, dict)}
            active = payload.get("active", [])
            return {str(sym): {"direction": "up"} for sym in active}
        except Exception:
            return {}

    async def _heartbeat_loop(self) -> None:
        interval = int(self.config["heartbeat_interval"])
        while not self.stop_event.is_set():
            await asyncio.sleep(interval)
            main_n = sum(1 for p in self.pool.values() if p.section == "main")
            counter_n = sum(1 for p in self.pool.values() if p.section == "counter")
            banned_n = sum(1 for p in self.pool.values() if p.section == "banned")
            open_n = sum(1 for p in self.pool.values() if p.position)
            self._log(
                f"HEARTBEAT | main={main_n} counter={counter_n} banned={banned_n} "
                f"| open={open_n} | daily_pnl={self.cb_daily_pnl:+.2f}%"
            )

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
    asyncio.run(PumpOrchestrator().run())
