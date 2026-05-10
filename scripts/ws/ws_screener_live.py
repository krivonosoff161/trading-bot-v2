"""Live OKX SWAP screener driven by closed 5m candles from WSFeed."""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.ws_feed import Candle, WSFeed

REST_TICKERS_URL = "https://www.okx.com/api/v5/market/tickers"
CACHE_PATH = Path(__file__).resolve().parent / "cache" / "active_universe.json"
LOG_PATH = ROOT / "logs" / "ws_screener_live.log"

DEFAULT_CONFIG = {
    "vol_spike_min": 2.0,
    "price_move_min_pct": 1.0,
    "inactivity_bars": 3,
    "min_vol_24h_usd": 1_000_000,
    "warmup_bars": 15,
}


def _now() -> str:
    return datetime.utcnow().strftime("%H:%M:%S")


def _ts_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_excluded_symbol(inst_id: str) -> tuple[bool, str]:
    if not inst_id.endswith("-USDT-SWAP"):
        return True, "not_usdt_swap"
    upper = inst_id.upper()
    if any(token in upper for token in ("UP-USDT", "DOWN-USDT", "3L-USDT", "3S-USDT")):
        return True, "leveraged_token"
    if inst_id.count("-") > 2:
        return True, "exotic_name"
    return False, ""


def _load_config() -> dict[str, Any]:
    config_path = ROOT / "config.yaml"
    cfg = DEFAULT_CONFIG.copy()
    try:
        with config_path.open(encoding="utf-8") as f:
            project = yaml.safe_load(f) or {}
        cfg.update(project.get("screener_live", {}) or {})
    except Exception:
        pass
    return cfg


def _setup_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ws_screener_live")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


class LiveScreener:
    def __init__(self) -> None:
        self.config = _load_config()
        self.logger = _setup_logger()
        self.stop_event = asyncio.Event()
        self.state_lock = asyncio.Lock()
        self.session: aiohttp.ClientSession | None = None
        self.feed: WSFeed | None = None
        self.pairs: list[str] = []
        self.active_universe: set[str] = set()
        self.active_meta: dict[str, dict[str, str]] = {}
        self.inactive_counts: dict[str, int] = {}

    def get_active(self) -> list[str]:
        return sorted(self.active_universe)

    async def run(self) -> None:
        feed_task: asyncio.Task[None] | None = None
        stop_task: asyncio.Task[bool] | None = None
        self._install_signal_handlers()
        try:
            self.pairs = await self._fetch_pairs()
            if not self.pairs:
                raise RuntimeError("No eligible OKX SWAP pairs matched screener filters")

            warmup_bars = max(int(self.config["warmup_bars"]), 15)
            self.inactive_counts = {sym: 0 for sym in self.pairs}
            self.feed = WSFeed(
                pairs=self.pairs,
                bars=["candle5m"],
                buffer_size=max(warmup_bars + 5, 30),
            )
            self.feed.on_candle_close("candle5m", self._on_candle_close)
            await self.feed.warmup()
            await self._save_cache()
            self._log(f"started | monitored={len(self.pairs)} | warmup_bars={warmup_bars}")

            feed_task = asyncio.create_task(self.feed.start(), name="ws_screener_live.feed")
            stop_task = asyncio.create_task(self.stop_event.wait(), name="ws_screener_live.stop")
            done, _ = await asyncio.wait({feed_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)

            if feed_task in done:
                exc = feed_task.exception()
                if exc:
                    raise exc
                raise RuntimeError("WSFeed stopped unexpectedly")
        finally:
            self.stop_event.set()
            if self.feed is not None:
                await self.feed.stop()
            for task in (feed_task, stop_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (feed_task, stop_task) if task is not None), return_exceptions=True)
            if self.session is not None and not self.session.closed:
                await self.session.close()
            self._log(f"stopped | active={len(self.active_universe)} | monitored={len(self.pairs)}")

    async def _fetch_pairs(self) -> list[str]:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))

        async with self.session.get(REST_TICKERS_URL, params={"instType": "SWAP"}) as resp:
            payload = await resp.json(content_type=None)
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX tickers failed: code={payload.get('code')} msg={payload.get('msg', '')}")

        min_vol = float(self.config["min_vol_24h_usd"])
        scored: list[tuple[str, float]] = []
        for row in payload.get("data", []):
            inst_id = row.get("instId", "")
            excluded, _ = _is_excluded_symbol(inst_id)
            if excluded:
                continue
            try:
                dollar_vol_24h = float(row.get("volCcy24h", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if dollar_vol_24h < min_vol:
                continue
            scored.append((inst_id, dollar_vol_24h))

        scored.sort(key=lambda item: item[1], reverse=True)
        pairs = [sym for sym, _ in scored]
        self._log(f"universe_bootstrap | monitored={len(pairs)} | min_vol_24h_usd={min_vol:.0f}")
        return pairs

    async def _on_candle_close(self, sym: str, candle: Candle) -> None:
        assert self.feed is not None
        required_bars = max(int(self.config["warmup_bars"]), 15)
        candles = self.feed.get_candles(sym, "candle5m", required_bars + 1)
        if len(candles) < required_bars + 1:
            return

        current = candle
        history = candles[:-1][-15:]
        if len(history) < 15:
            return

        avg_vol = sum(row[5] for row in history) / len(history)
        if avg_vol <= 0 or current[1] <= 0:
            return

        vol_spike = current[5] / avg_vol
        price_move = abs(current[4] - current[1]) / current[1] * 100.0
        qualifies = (
            vol_spike >= float(self.config["vol_spike_min"])
            and price_move >= float(self.config["price_move_min_pct"])
        )

        change_label: str | None = None
        async with self.state_lock:
            if qualifies:
                self.inactive_counts[sym] = 0
                direction = "up" if current[4] > current[1] else "down"
                if sym not in self.active_universe:
                    self.active_universe.add(sym)
                    self.active_meta[sym] = {
                        "direction": direction,
                        "vol_spike": round(vol_spike, 2),
                        "last_spike_at": _ts_utc(),
                        "added_at": _ts_utc(),
                    }
                    change_label = "добавлена"
                else:
                    # Update direction and score on every new spike
                    self.active_meta[sym]["direction"] = direction
                    self.active_meta[sym]["vol_spike"] = round(vol_spike, 2)
                    self.active_meta[sym]["last_spike_at"] = _ts_utc()
            else:
                next_count = self.inactive_counts.get(sym, 0) + 1
                self.inactive_counts[sym] = next_count
                if sym in self.active_universe and next_count >= int(self.config["inactivity_bars"]):
                    self.active_universe.remove(sym)
                    self.active_meta.pop(sym, None)
                    change_label = "удалена"

            if change_label is None:
                return
            await self._save_cache()

        self._log(
            f"{sym} {change_label} | active={len(self.active_universe)} "
            f"| vol_spike={vol_spike:.2f} | move={price_move:.2f}%"
        )

    async def _save_cache(self) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _ts_utc(),
            "active": sorted(self.active_universe),
            "symbols": {sym: self.active_meta[sym] for sym in sorted(self.active_universe) if sym in self.active_meta},
            "all_monitored": len(self.pairs),
        }
        tmp_path = CACHE_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        for _attempt in range(5):
            try:
                tmp_path.replace(CACHE_PATH)
                break
            except PermissionError:
                await asyncio.sleep(0.1)

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
    asyncio.run(LiveScreener().run())
