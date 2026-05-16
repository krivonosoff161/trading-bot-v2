"""
BB Fade WS screener — MTF mean reversion channel.

Setup:  15m candle touches BB band (HIGH>=upper OR LOW<=lower)
Entry:  next 1-3 five-minute candles show wick rejection
        SHORT: 5m HIGH >= upper15 AND 5m close < upper15
        LONG:  5m LOW  <= lower15 AND 5m close > lower15

Filters (from backtest F.1, 15.05.2026):
  - BB width >= 2.0% of price  (narrow bands = squeeze, skip)
  - Vol ratio < 1.5            (high vol = breakout impulse, skip)
  - SELL only if 15m RSI <= 60 (RSI>60 = momentum breakout, skip)
  - BUY  only if 15m RSI >= 40 (RSI<40 = downtrend impulse, skip)
  - No Asia session 00-08 UTC  (underperforms: avg_net -0.067%)
  - No trending on 1H ADX>=22 + DI_spread>=10 (BB walk)
  - Cooldown 30 min per pair

TP = 15m BB midline, SL = band edge +/- band_width * 0.5
Max hold: 80 min
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import signal
import sys
import time
import uuid

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv()

from scripts.subscriptions import is_subscribed, list_users
from src.data.ws_feed import WSFeed
from src.strategy.indicators import calc_adx
from src.utils.telegram import send_message, send_message_to

SIGNALS_LOG  = ROOT / "logs" / "bb_fade" / "bb_fade_signals.jsonl"
LOG_PATH     = ROOT / "logs" / "bb_fade" / "ws_bb_fade.log"
UNIVERSE_URL = "https://www.okx.com/api/v5/market/tickers"

PINNED_PAIRS = [
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
    "XRP-USDT-SWAP", "DOGE-USDT-SWAP", "ADA-USDT-SWAP",
]

DEFAULT_CFG = {
    "top_n_pairs":            24,
    "universe_refresh_hours":  8,
    "bb_period":              20,
    "bb_std":                2.0,
    "sl_mult":               0.5,
    "min_width_pct":         2.0,
    "max_vol_ratio":         1.5,
    "rsi_period":             14,
    "rsi_sell_max":           60,
    "rsi_buy_min":            40,
    "confirm_bars":            3,
    "cooldown_min":           30,
    "hold_min":               80,
    "adx_period":              9,
    "adx_trending_min":       22,
    "di_spread_min":          10,
    "skip_asia_utc":        True,
}


# ── helpers ────────────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hour_utc() -> int:
    return datetime.now(timezone.utc).hour


def _load_cfg() -> dict[str, Any]:
    cfg = DEFAULT_CFG.copy()
    try:
        with (ROOT / "config.yaml").open(encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}
        cfg.update(payload.get("bb_fade", {}) or {})
    except Exception:
        pass
    return cfg


def _setup_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ws_bb_fade")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.addHandler(stream)
    return logger


def _calc_bb(closes: list[float], period: int, std: float) -> tuple[float, float, float]:
    """Return (mid, upper, lower) for the last bar."""
    if len(closes) < period:
        return float("nan"), float("nan"), float("nan")
    w = np.array(closes[-period:], dtype=float)
    m = float(np.mean(w))
    s = float(np.std(w, ddof=0))
    return m, m + std * s, m - std * s


def _calc_rsi(closes: list[float], period: int) -> float:
    if len(closes) < period + 1:
        return 50.0
    arr = np.array(closes[-(period * 3):], dtype=float)
    deltas = np.diff(arr)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = float(np.mean(gains[:period]))
    avg_l = float(np.mean(losses[:period]))
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_g / avg_l), 2)


def _trending_1h(candles_1h: list, period: int, adx_min: float, di_min: float) -> bool:
    if len(candles_1h) < period + 5:
        return False
    H = [float(c[2]) for c in candles_1h]
    L = [float(c[3]) for c in candles_1h]
    C = [float(c[4]) for c in candles_1h]
    try:
        adx, pdi, ndi = calc_adx(H, L, C, period=period)
        return adx >= adx_min and abs(pdi - ndi) >= di_min
    except Exception:
        return False


def _active_chats() -> list[str]:
    return [
        str(u["chat_id"]) for u in list_users()
        if is_subscribed(str(u["chat_id"]))
    ]


def _format_signal(sym: str, side: str, entry: float, tp: float, sl: float,
                   bw_pct: float, rsi: float, vol_ratio: float, hold_min: int) -> str:
    side_label = "LONG" if side == "buy" else "SHORT"
    direction  = "up" if side == "buy" else "down"
    arrow      = "🟢" if side == "buy" else "🔴"
    base       = sym.replace("-USDT-SWAP", "")
    return (
        f"{arrow} <b>[BB FADE] {base} {side_label}</b>\n"
        f"Entry: <code>{entry}</code>\n"
        f"TP:    <code>{tp}</code>  (+{abs(tp - entry) / entry * 100:.2f}%)\n"
        f"SL:    <code>{sl}</code>  (-{abs(sl - entry) / entry * 100:.2f}%)\n"
        f"BB width: {bw_pct:.2f}%  RSI: {rsi:.0f}  Vol×: {vol_ratio:.2f}\n"
        f"Hold: {hold_min}m  |  Trend: mean reversion {direction}"
    )


# ── main class ─────────────────────────────────────────────────────────────

class BBFadeScreener:
    def __init__(self) -> None:
        self.cfg    = _load_cfg()
        self.logger = _setup_logger()
        self.stop_event = asyncio.Event()
        self.session: aiohttp.ClientSession | None = None

        self.feed = WSFeed(
            pairs=[],
            bars=["candle5m", "candle15m", "candle1H"],
            buffer_size=200,
        )
        self.feed.on_candle_close("candle15m", self._on_15m_close)
        self.feed.on_candle_close("candle5m",  self._on_5m_close)

        # sym → setup dict (armed when 15m touches band)
        self.armed: dict[str, dict] = {}
        # sym → last signal ts (cooldown)
        self.last_signal: dict[str, float] = {}

        # daily counters
        self.signals_today = 0
        self.tp_today      = 0
        self.sl_today      = 0
        self._signal_date  = datetime.now(timezone.utc).date()

        self.subscribed_pairs: list[str] = []

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        self._install_signal_handlers()
        try:
            pairs = await self._fetch_universe()
            await self._ensure_warmed(pairs)
            self.subscribed_pairs = list(pairs)
            self._log(f"started | pairs={len(pairs)}")

            feed_task      = asyncio.create_task(self.feed.start(),               name="bb_fade.feed")
            universe_task  = asyncio.create_task(self._universe_loop(),            name="bb_fade.universe")
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(),           name="bb_fade.heartbeat")
            stop_task      = asyncio.create_task(self.stop_event.wait(),           name="bb_fade.stop")

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
                if task is feed_task:
                    raise RuntimeError("WSFeed stopped unexpectedly")
        finally:
            self.stop_event.set()
            await self.feed.stop()
            if self.session and not self.session.closed:
                await self.session.close()
            self._log(f"stopped | signals_today={self.signals_today}")

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop_event.set)
            except NotImplementedError:
                pass

    # ── universe management ────────────────────────────────────────────────

    async def _fetch_universe(self) -> list[str]:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        async with self.session.get(UNIVERSE_URL, params={"instType": "SWAP"}) as resp:
            payload = await resp.json(content_type=None)
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX tickers: {payload.get('msg')}")
        scored = []
        for row in payload.get("data", []):
            inst = row.get("instId", "")
            if not inst.endswith("-USDT-SWAP") or inst.count("-") > 2:
                continue
            try:
                scored.append((inst, float(row.get("volCcy24h", 0) or 0)))
            except (TypeError, ValueError):
                continue
        scored.sort(key=lambda x: x[1], reverse=True)
        dynamic = [s for s, _ in scored[: int(self.cfg["top_n_pairs"])]]
        return list(dict.fromkeys(PINNED_PAIRS + dynamic))

    async def _ensure_warmed(self, pairs: list[str]) -> None:
        for sym in pairs:
            self.feed.ensure_pair(sym)
        await self.feed.warmup()

    async def _universe_loop(self) -> None:
        every = int(self.cfg["universe_refresh_hours"]) * 3600
        while not self.stop_event.is_set():
            await asyncio.sleep(every)
            try:
                fresh = await self._fetch_universe()
                added   = [p for p in fresh if p not in self.subscribed_pairs]
                removed = [p for p in self.subscribed_pairs if p not in fresh and p not in self.armed]
                if added or removed:
                    for sym in added:
                        self.feed.ensure_pair(sym)
                    new_pairs = [p for p in self.subscribed_pairs if p not in removed] + added
                    self.subscribed_pairs = new_pairs
                    self._log(f"universe refresh | +{len(added)} -{len(removed)} total={len(new_pairs)}")
            except Exception as exc:
                self._log(f"universe refresh error | {exc}")

    # ── candle handlers ────────────────────────────────────────────────────

    async def _on_15m_close(self, sym: str, candle: Any) -> None:
        """Detect BB band touch on 15m — arm setup."""
        cfg = self.cfg

        # Asia session filter
        if cfg["skip_asia_utc"] and 0 <= _hour_utc() < 8:
            return

        candles_15m = list(reversed(self.feed.get_candles(sym, "candle15m", 60)))
        if len(candles_15m) < cfg["bb_period"] + 5:
            return

        closes15 = [float(c[4]) for c in candles_15m]
        highs15  = [float(c[2]) for c in candles_15m]
        lows15   = [float(c[3]) for c in candles_15m]
        vols15   = [float(c[5]) for c in candles_15m]

        mid, upper, lower = _calc_bb(closes15, cfg["bb_period"], cfg["bb_std"])
        if any(np.isnan(x) for x in (mid, upper, lower)):
            return

        bw     = upper - lower
        bw_pct = bw / closes15[-1] * 100
        if bw_pct < cfg["min_width_pct"]:
            return

        # Cooldown check
        cooldown_sec = cfg["cooldown_min"] * 60
        if time.time() - self.last_signal.get(sym, 0) < cooldown_sec:
            return

        # Trending filter (1H)
        candles_1h = list(reversed(self.feed.get_candles(sym, "candle1H", 40)))
        if _trending_1h(candles_1h, cfg["adx_period"], cfg["adx_trending_min"], cfg["di_spread_min"]):
            return

        h15 = highs15[-1]
        l15 = lows15[-1]

        touched_upper = h15 >= upper
        touched_lower = l15 <= lower
        if not touched_upper and not touched_lower:
            return
        if touched_upper and touched_lower:
            return  # ambiguous

        side = "sell" if touched_upper else "buy"

        # RSI filter
        rsi = _calc_rsi(closes15, cfg["rsi_period"])
        if side == "sell" and rsi > cfg["rsi_sell_max"]:
            return
        if side == "buy" and rsi < cfg["rsi_buy_min"]:
            return

        # Vol ratio filter
        baseline = float(np.mean(vols15[-11:-1])) if len(vols15) >= 11 else vols15[-1]
        vol_ratio = vols15[-1] / baseline if baseline > 0 else 1.0
        if vol_ratio > cfg["max_vol_ratio"]:
            return

        # Arm setup — wait for 5m wick rejection
        self.armed[sym] = {
            "side":      side,
            "upper":     upper,
            "lower":     lower,
            "mid":       mid,
            "bw":        bw,
            "bw_pct":    bw_pct,
            "rsi":       rsi,
            "vol_ratio": vol_ratio,
            "armed_ts":  time.time(),
            "bars_seen": 0,
        }
        self._log(f"ARMED | {sym} {side.upper()} | bw={bw_pct:.2f}% RSI={rsi:.0f} vol×={vol_ratio:.2f}")

    async def _on_5m_close(self, sym: str, candle: Any) -> None:
        """Check armed setups for 5m wick rejection entry."""
        setup = self.armed.get(sym)
        if setup is None:
            return

        setup["bars_seen"] += 1
        if setup["bars_seen"] > self.cfg["confirm_bars"]:
            del self.armed[sym]
            return

        candles_5m = list(reversed(self.feed.get_candles(sym, "candle5m", 5)))
        if not candles_5m:
            return

        last = candles_5m[-1]
        h5, l5, c5 = float(last[2]), float(last[3]), float(last[4])
        upper, lower, mid = setup["upper"], setup["lower"], setup["mid"]
        side = setup["side"]

        confirmed = False
        if side == "sell" and h5 >= upper and c5 < upper:
            confirmed = True
        elif side == "buy" and l5 <= lower and c5 > lower:
            confirmed = True

        if not confirmed:
            return

        del self.armed[sym]

        entry = c5
        tp    = mid
        sl    = upper + setup["bw"] * self.cfg["sl_mult"] if side == "sell" else lower - setup["bw"] * self.cfg["sl_mult"]

        tp_dist = abs(entry - tp)
        sl_dist = abs(entry - sl)
        if tp_dist < sl_dist * 0.3:
            self._log(f"SKIP bad R:R | {sym} {side}")
            return

        await self._emit_signal(sym, side, entry, tp, sl, setup)

    # ── signal emit ────────────────────────────────────────────────────────

    async def _emit_signal(self, sym: str, side: str, entry: float,
                           tp: float, sl: float, setup: dict) -> None:
        self._roll_day()
        self.last_signal[sym] = time.time()
        self.signals_today += 1

        signal_id = str(uuid.uuid4())
        payload = {
            "id":        signal_id,
            "ts":        _now_utc(),
            "symbol":    sym,
            "side":      side,
            "entry":     round(entry, 8),
            "tp":        round(tp, 8),
            "sl":        round(sl, 8),
            "bw_pct":    round(setup["bw_pct"], 3),
            "rsi":       round(setup["rsi"], 1),
            "vol_ratio": round(setup["vol_ratio"], 3),
            "hold_min":  self.cfg["hold_min"],
            "outcome":   None,
            "net_pct":   None,
            "source":    "ws_bb_fade",
        }

        SIGNALS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SIGNALS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

        self._log(
            f"SIGNAL | {sym} {'BUY' if side == 'buy' else 'SELL'} | "
            f"entry={entry} tp={tp:.6f} sl={sl:.6f} | "
            f"bw={setup['bw_pct']:.2f}% RSI={setup['rsi']:.0f} vol×={setup['vol_ratio']:.2f}"
        )

        text = _format_signal(sym, side, entry, tp, sl,
                              setup["bw_pct"], setup["rsi"], setup["vol_ratio"],
                              self.cfg["hold_min"])
        await self._notify(text)

    async def _notify(self, text: str) -> None:
        chats = _active_chats()
        if not chats:
            # fallback — broadcast to all configured chat IDs
            try:
                await send_message(text)
            except Exception as exc:
                self._log(f"NOTIFY broadcast error | {exc}")
            return
        for chat_id in chats:
            try:
                await send_message_to(chat_id, text)
            except Exception as exc:
                self._log(f"NOTIFY error | chat={chat_id} | {exc}")

    # ── heartbeat & stats ──────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(3600)
            # Evict armed setups that never got a 5m confirm (pair went silent or delisted)
            armed_ttl = self.cfg["confirm_bars"] * 5 * 60 * 3  # 3× confirm window in seconds
            stale = [sym for sym, s in self.armed.items() if time.time() - s["armed_ts"] > armed_ttl]
            for sym in stale:
                del self.armed[sym]
                self._log(f"ARMED expire TTL | {sym}")
            self._log(
                f"heartbeat | pairs={len(self.subscribed_pairs)} "
                f"armed={len(self.armed)} "
                f"signals_today={self.signals_today}"
            )

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._signal_date:
            self._log(
                f"DAY END | {self._signal_date} | "
                f"signals={self.signals_today}"
            )
            self.signals_today = 0
            self._signal_date  = today

    def _log(self, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.logger.info(f"[{ts}] {msg}")


# ── entry point ─────────────────────────────────────────────────────────────

async def _main() -> None:
    screener = BBFadeScreener()
    await screener.run()


if __name__ == "__main__":
    asyncio.run(_main())
