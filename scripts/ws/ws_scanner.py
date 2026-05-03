"""WebSocket scanner for FAST/SWING signals without auto-trading."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv()

from scripts.subscriptions import is_subscribed, list_users
from src.data.ws_feed import WSFeed
from src.exchange.okx_client import OKXClient
from src.strategy.chart_renderer import generate_chart_png
from src.strategy.signal_engine import _format_telegram, build_analysis_snapshot, compute_signal
from src.utils.llm_formatter import generate_client_text
from src.utils.telegram import send_message_to, send_photo_to

SIGNAL_LOG = ROOT / "logs" / "signals" / "signal_log.jsonl"
NOTRADE_LOG = ROOT / "logs" / "signals" / "signal_log_notrade.jsonl"
SCANNER_LOG_DIR = ROOT / "logs" / "scanner"

_log_file = ROOT / "logs" / "ws_scanner.log"
_handler = logging.handlers.RotatingFileHandler(
    _log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
logging.basicConfig(level=logging.INFO, handlers=[_handler, logging.StreamHandler(sys.stdout)],
                    format="%(message)s")
_logger = logging.getLogger("ws_scanner")


def _now() -> str:
    return datetime.utcnow().strftime("%H:%M:%S")


def _scan_log(text: str) -> None:
    _logger.info(f"[{_now()}] {text}")


def _load_strategy_params() -> dict:
    with (ROOT / "config.yaml").open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("strategy", {})


def _swap_to_base(sym_swap: str) -> str:
    return sym_swap.removesuffix("-SWAP")


def _active_chat_ids() -> list[str]:
    chats: list[str] = []
    for user in list_users():
        cid = str(user["chat_id"])
        if is_subscribed(cid):
            chats.append(cid)
    return chats


def _append_signal_log_entry(result: dict, *, captured_at: str, symbol: str, source: str) -> None:
    if (result or {}).get("entry_signal") != "ENTRY":
        return
    side = result.get("side")
    style = result.get("trade_style") or result.get("trade_style_hint") or ""
    if not side or not style:
        return
    micro = result.get("microstructure", {}) or {}
    ts_ms = int(datetime.fromisoformat(captured_at.replace("Z", "+00:00")).timestamp() * 1000)
    entry = {
        "signal_id": f"{ts_ms}_{symbol}_{side}_{style}",
        "source": source,
        "ts_ms": ts_ms,
        "symbol": symbol,
        "side": side,
        "regime": result.get("regime", ""),
        "style": style,
        "close": result.get("entry_price"),
        "sl": result.get("sl_price"),
        "tp": result.get("tp1_price"),
        "max_hold_min": result.get("max_hold_min") or result.get("max_hold_minutes"),
        "adx_1h": result.get("adx_1h"),
        "adx_4h": result.get("adx_4h"),
        "day_position": result.get("day_position"),
        "vol_ratio": result.get("vol_ratio") or result.get("volume_ratio_15m"),
        "funding": result.get("funding") if result.get("funding") is not None else result.get("funding_rate"),
        "strong_4h_veto": result.get("strong_4h_veto"),
        "slope_1h": result.get("slope_1h"),
        "slope_15m": result.get("slope_15m"),
        "obi5": micro.get("obi_top5"),
        "trade_delta_100": micro.get("trade_delta_100"),
        "spread_bps": micro.get("spread_bps"),
        "is_demo": os.getenv("OKX_IS_DEMO", "1") == "1",
    }
    with SIGNAL_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class WSScanner:
    def __init__(self) -> None:
        api_key = os.getenv("OKX_API_KEY", "")
        secret_key = os.getenv("OKX_SECRET_KEY", "")
        passphrase = os.getenv("OKX_PASSPHRASE", "")
        is_demo = os.getenv("OKX_IS_DEMO", "1") == "1"
        self.params = _load_strategy_params()
        self.feed = WSFeed()
        self.client = OKXClient(api_key, secret_key, passphrase, is_demo)
        self.cooldown_sec = 120
        self.last_signal_wall: dict[str, float] = {}
        self.cache_4h: dict[str, tuple[float, list]] = {}
        self.stop_event = asyncio.Event()

    async def run(self) -> None:
        SCANNER_LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.feed.on_candle_close("candle15m", self._on_fast_close)
        self.feed.on_candle_close("candle1H", self._on_swing_close)
        await self.feed.warmup()
        _scan_log(f"WS scanner started. Pairs: {', '.join(self.feed.pairs)}")
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, sig_name):
                try:
                    loop.add_signal_handler(getattr(signal, sig_name), self.stop_event.set)
                except NotImplementedError:
                    pass
        task = asyncio.create_task(self.feed.start())
        try:
            await self.stop_event.wait()
        finally:
            await self.feed.stop()
            await task
            await self.client.close()
            _scan_log("WS scanner stopped")

    async def _get_cached_4h(self, base_symbol: str, after_ts: int) -> list:
        now = time.time()
        cached = self.cache_4h.get(base_symbol)
        if cached and now - cached[0] < 1800:
            return cached[1]
        rows = await self.client.get_history_candles(base_symbol, "4H", after=after_ts, limit=60)
        self.cache_4h[base_symbol] = (now, rows)
        return rows

    async def _on_fast_close(self, sym_swap: str, candle) -> None:
        await self._evaluate(sym_swap, candle, timeframe="15m", expected_style="FAST")

    async def _on_swing_close(self, sym_swap: str, candle) -> None:
        await self._evaluate(sym_swap, candle, timeframe="1H", expected_style="SWING")

    async def _evaluate(self, sym_swap: str, candle, *, timeframe: str, expected_style: str) -> None:
        base_symbol = _swap_to_base(sym_swap)
        tf_ms = 900_000 if timeframe == "15m" else 3_600_000
        captured_at = datetime.fromtimestamp((candle[0] + tf_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw_15m = list(reversed(self.feed.get_candles(sym_swap, "candle15m", 100)))
        raw_1h = list(reversed(self.feed.get_candles(sym_swap, "candle1H", 100)))
        raw_5m = list(reversed(self.feed.get_candles(sym_swap, "candle5m", 50)))
        if len(raw_15m) < 50 or len(raw_1h) < 50 or len(raw_5m) < 20:
            _scan_log(f"{base_symbol} skipped: not enough WS candles for {timeframe}")
            return

        after_ts = int(datetime.fromisoformat(captured_at.replace("Z", "+00:00")).timestamp() * 1000) + 1
        raw_4h = await self._get_cached_4h(base_symbol, after_ts)
        funding, oi, oi_hist, books5, trades100, raw_idx15 = await asyncio.gather(
            self.client.get_funding_rate(base_symbol),
            self.client.get_open_interest(base_symbol),
            self.client.get_oi_history(base_symbol, period="1H", limit=5),
            self.client.get_books(base_symbol, size=5),
            self.client.get_trades(base_symbol, limit=100),
            self.client.get_history_index_candles(base_symbol, "15m", after=after_ts, limit=10),
        )

        result = compute_signal(
            candles_15m=raw_15m,
            candles_1h=raw_1h,
            candles_4h=raw_4h,
            candles_5m=raw_5m,
            symbol=base_symbol,
            config=self.params,
            captured_at_iso=captured_at,
            funding=funding,
            open_interest=oi,
            oi_history=oi_hist,
            books5=books5,
            trades100=trades100,
            raw_idx15=raw_idx15,
        )
        if result.entry_signal != "ENTRY" or result.trade_style != expected_style:
            return

        now_wall = time.time()
        if now_wall - self.last_signal_wall.get(sym_swap, 0.0) < self.cooldown_sec:
            return
        self.last_signal_wall[sym_swap] = now_wall

        run_dir = SCANNER_LOG_DIR / f"{datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S')}_{base_symbol}_{expected_style}"
        run_dir.mkdir(parents=True, exist_ok=True)
        png_path = run_dir / f"{base_symbol}_chart.png"
        levels = {
            "sl": result.sl_price,
            "tp1": result.tp1_price,
            "tp2": result.tp2_price,
            "entry_price": result.entry_price,
        } if result.sl_price else {}
        generate_chart_png(
            raw_15m,
            result.indicators,
            base_symbol,
            captured_at,
            str(png_path),
            llm_levels=levels,
            entry_signal=result.entry_signal,
            direction=result.side,
            trade_style=result.trade_style,
        )

        snapshot = build_analysis_snapshot(base_symbol, captured_at, result, open_interest=oi)
        llm_text = await generate_client_text(base_symbol, captured_at, snapshot, str(png_path), client_summary=None)
        delivery_text = llm_text or result.engine_summary
        legacy = result.to_legacy_result(delivery_text)
        _append_signal_log_entry(legacy, captured_at=captured_at, symbol=base_symbol, source="ws_scanner")

        active = _active_chat_ids()
        header = f"<b>{base_symbol}</b> — сигнал входа\n<i>Актуально до {legacy['expiry_time']}</i>\n\n"
        text = header + (html.escape(delivery_text) if llm_text else _format_telegram(delivery_text))
        for chat_id in active:
            try:
                await send_message_to(chat_id, text)
                if png_path.exists():
                    await send_photo_to(chat_id, str(png_path))
            except Exception as exc:
                _scan_log(f"{base_symbol} send error {chat_id}: {exc}")
            await asyncio.sleep(0.2)
        _scan_log(f"{base_symbol} {expected_style} ENTRY {legacy['side']} -> sent {len(active)} users")


def main() -> None:
    asyncio.run(WSScanner().run())


if __name__ == "__main__":
    main()
