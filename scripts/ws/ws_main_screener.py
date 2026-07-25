from __future__ import annotations

import asyncio
import html
import json
import logging
import logging.handlers
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import aiohttp
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.utils.runtime_root import load_runtime_dotenv  # noqa: E402

if __name__ == "__main__":
    load_runtime_dotenv(ROOT)

from scripts.subscriptions import is_subscribed, list_users  # noqa: E402
from src.data.snapshot_writer import write_snapshot  # noqa: E402
from src.data.feature_writer import write_feature_row  # noqa: E402
from src.data.ws_feed import Candle, WSFeed, _chunked  # noqa: E402
from src.strategy.chart_renderer import generate_chart_png  # noqa: E402
from src.strategy.indicators import calc_adx, find_fvg, parse_candles  # noqa: E402
from src.strategy.signal_engine import _format_telegram, build_analysis_snapshot, compute_signal  # noqa: E402
from src.utils.llm_formatter import generate_client_text  # noqa: E402
from src.utils.telegram import send_message_to, send_photo_to  # noqa: E402


REST_TICKERS_URL = "https://www.okx.com/api/v5/market/tickers"
PINNED_PAIRS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP", "ADA-USDT-SWAP"]
SIGNALS_LOG = ROOT / "logs" / "signals" / "main_signals.jsonl"
LOG_PATH = ROOT / "logs" / "ws_main_screener.log"
SCREENER_LOG_DIR = ROOT / "logs" / "main_screener"
UNIVERSE_CACHE = Path(__file__).resolve().parent / "cache" / "main_universe.json"
DEFAULT_CONFIG = {
    "top_n_pairs": 24,
    "universe_refresh_hours": 8,
    "cooldown_minutes": 60,
    "warmup_bars": 20,
    "prefilter_vol_ratio_min": 0.5,
    "prefilter_adx_min": 10,
}


def _now() -> str:
    return datetime.utcnow().strftime("%H:%M:%S")


def _ts_utc(ts_ms: int | None = None, tf_minutes: int = 0) -> str:
    base = datetime.now(timezone.utc) if ts_ms is None else datetime.fromtimestamp((ts_ms + tf_minutes * 60_000) / 1000, tz=timezone.utc)
    return base.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_excluded_symbol(inst_id: str) -> tuple[bool, str]:
    if not inst_id.endswith("-USDT-SWAP"):
        return True, "not_usdt_swap"
    upper = inst_id.upper()
    if any(token in upper for token in ("UP-USDT", "DOWN-USDT", "3L-USDT", "3S-USDT")):
        return True, "leveraged_token"
    if inst_id.count("-") > 2:
        return True, "exotic_name"
    return False, ""


def _swap_to_base(sym_swap: str) -> str:
    return sym_swap.removesuffix("-SWAP")


def _active_chat_ids() -> list[str]:
    chats: list[str] = []
    for user in list_users():
        cid = str(user["chat_id"])
        if is_subscribed(cid):
            chats.append(cid)
    return chats


def _load_yaml_section(section: str, defaults: dict[str, Any]) -> dict[str, Any]:
    cfg = defaults.copy()
    try:
        with (ROOT / "config.yaml").open(encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}
        cfg.update(payload.get(section, {}) or {})
    except Exception:
        pass
    return cfg


def _setup_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ws_main_screener")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.addHandler(stream)
    return logger


class MainScreener:
    def __init__(self) -> None:
        self.main_cfg = _load_yaml_section("main_screener", DEFAULT_CONFIG)
        self.strategy_cfg = _load_yaml_section("strategy", {})
        self.logger = _setup_logger()
        self.stop_event = asyncio.Event()
        self.state_lock = asyncio.Lock()
        self.universe_lock = asyncio.Lock()
        self.session: aiohttp.ClientSession | None = None
        self.feed = WSFeed(pairs=[], bars=["candle5m", "candle15m", "candle1H", "candle4H"], buffer_size=180)
        self.feed.on_candle_close("candle15m", self._on_15m_close)
        self.feed.on_candle_close("candle5m", self._on_5m_close)
        self.feed.on_candle_close("candle1H", self._on_1h_close)
        self.feed.on_candle_close("candle4H", self._on_4h_close)
        self.subscribed_pairs: list[str] = []
        self.active_setups: dict[str, dict[str, Any]] = {}
        self.last_regime_by_symbol: dict[str, dict[str, Any]] = {}
        self.signals_today = 0
        self.signal_day = datetime.now(timezone.utc).date()

    async def run(self) -> None:
        feed_task = poll_task = heartbeat_task = stop_task = None
        self._install_signal_handlers()
        try:
            initial = await self._fetch_universe()
            await self._ensure_pairs_ready(initial)
            await self.feed.warmup()
            self.subscribed_pairs = list(initial)
            await self._write_universe_cache(initial)
            self._log(f"started | monitored={len(initial)} | bars={','.join(self.feed.bars)}")
            feed_task = asyncio.create_task(self.feed.start(), name="ws_main.feed")
            poll_task = asyncio.create_task(self._refresh_universe_loop(), name="ws_main.universe")
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="ws_main.heartbeat")
            stop_task = asyncio.create_task(self.stop_event.wait(), name="ws_main.stop")
            done, _ = await asyncio.wait({feed_task, poll_task, heartbeat_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
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
            await asyncio.gather(*(task for task in (feed_task, poll_task, heartbeat_task, stop_task) if task is not None), return_exceptions=True)
            await self.feed.stop()
            if self.session is not None and not self.session.closed:
                await self.session.close()
            self._log(f"stopped | subscribed={len(self.subscribed_pairs)} | active_setups={len(self.active_setups)}")

    async def _fetch_universe(self) -> list[str]:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        async with self.session.get(REST_TICKERS_URL, params={"instType": "SWAP"}) as resp:
            payload = await resp.json(content_type=None)
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX tickers failed: code={payload.get('code')} msg={payload.get('msg', '')}")
        scored: list[tuple[str, float]] = []
        for row in payload.get("data", []):
            inst_id = row.get("instId", "")
            excluded, _ = _is_excluded_symbol(inst_id)
            if excluded:
                continue
            try:
                scored.append((inst_id, float(row.get("volCcy24h", 0.0) or 0.0)))
            except (TypeError, ValueError):
                continue
        scored.sort(key=lambda item: item[1], reverse=True)
        dynamic = [sym for sym, _ in scored[: int(self.main_cfg["top_n_pairs"])]]
        universe = list(dict.fromkeys(PINNED_PAIRS + dynamic))
        return universe

    async def _refresh_universe_loop(self) -> None:
        every = int(self.main_cfg["universe_refresh_hours"]) * 3600
        while not self.stop_event.is_set():
            await asyncio.sleep(every)
            await self._refresh_universe()

    async def _refresh_universe(self) -> None:
        async with self.universe_lock:
            fresh = await self._fetch_universe()
            keep = {sym for sym in self.subscribed_pairs if self._has_active_setup(sym)}
            desired = sorted(set(fresh) | keep)
            current = set(self.subscribed_pairs)
            target = set(desired)
            added = sorted(target - current)
            removed = sorted(current - target)
            if added:
                await self._ensure_pairs_ready(added)
                await self._warmup_pairs(added)
                await self._change_subscriptions(added, "subscribe")
            if removed:
                await self._change_subscriptions(removed, "unsubscribe")
                for sym in removed:
                    self.feed.buffers.pop(sym, None)
                    self.feed.last_close_ts.pop(sym, None)
                    self.feed.last_msg_ts.pop(sym, None)
                    self.feed.pairs = [pair for pair in self.feed.pairs if pair != sym]
                    self.last_regime_by_symbol.pop(sym, None)
            self.subscribed_pairs = list(desired)
            self.feed.pairs = list(desired)
            await self._write_universe_cache(desired)
            self._log(f"UNIVERSE update | active={len(desired)} | added={','.join(added) if added else '-'} | removed={','.join(removed) if removed else '-'}")

    async def _ensure_pairs_ready(self, pairs: list[str]) -> None:
        for sym in pairs:
            self.feed.ensure_pair(sym)

    async def _warmup_pairs(self, pairs: list[str]) -> None:
        if self.feed.session is None or self.feed.session.closed:
            await self.feed.warmup()
            return
        for sym in pairs:
            for bar in self.feed.bars:
                rows = await self.feed._rest_candles(sym, bar.replace("candle", ""), self.feed._warmup_limit_for_bar(bar))
                closed = [row for row in rows if len(row) > 8 and row[8] == "1"]
                closed.reverse()
                buf = self.feed.buffers[sym][bar]
                buf.clear()
                for row in closed:
                    candle = self.feed._parse_candle(row)
                    buf.append(candle)
                    self.feed.last_close_ts[sym][bar] = candle[0]
                await asyncio.sleep(0.05)

    async def _change_subscriptions(self, pairs: list[str], op: str) -> None:
        if not pairs:
            return
        await self.feed.connected_event.wait()
        if not self.feed.ws:
            return
        args = [{"channel": bar, "instId": sym} for sym in pairs for bar in self.feed.bars]
        for chunk in _chunked(args, 45):
            await self.feed.ws.send_str(json.dumps({"op": op, "args": chunk}))

    async def _on_15m_close(self, sym: str, candle: Candle) -> None:
        feature_result = self._write_feature_close(sym, candle, "15m", 15)
        candles_15m = self.feed.get_candles(sym, "candle15m", 30)
        candles_1h = self.feed.get_candles(sym, "candle1H", 30)
        if len(candles_15m) < max(20, int(self.main_cfg["warmup_bars"])) or len(candles_1h) < 10:
            return
        hist_vol = [row[5] for row in candles_15m[-16:-1]]
        vol_ratio = candle[5] / mean(hist_vol) if hist_vol and mean(hist_vol) > 0 else 0.0
        adx_approx = self._cheap_adx_check(candles_1h)
        if vol_ratio < float(self.main_cfg["prefilter_vol_ratio_min"]) and adx_approx < float(self.main_cfg["prefilter_adx_min"]):
            return
        result = feature_result or self._compute(sym, tf_minutes=15)
        if result is None:
            return
        self.last_regime_by_symbol[sym] = {"regime": result.regime, "updated_at": time.time(), "fade_hint": result.five_m_fade_hint}
        if result.entry_signal != "ENTRY" or not result.side:
            return
        if self._context_gate_reject(result, sym):
            return
        await self._maybe_emit_signal(sym, result, detected_on="15m", entry=result.entry_price, sl=result.sl_price, tp1=result.tp1_price, tp2=result.tp2_price, hold_min=result.max_hold_min, trade_style=result.trade_style or "FAST", regime=result.regime, side=result.side, vol_ratio=vol_ratio, adx_1h=result.context.get("adx_1h"), fvg_confirmed=self._fvg_confirmed(sym, result.side))

    async def _on_5m_close(self, sym: str, candle: Candle) -> None:
        feature_result = self._write_feature_close(sym, candle, "5m", 5)
        state = self.last_regime_by_symbol.get(sym)
        if not state or state["regime"] not in ("RANGING", "CHOPPY"):
            return
        result = feature_result or self._compute(sym, tf_minutes=5)
        if result is None or result.five_m_fade_hint not in ("LONG", "SHORT"):
            return
        side = "buy" if result.five_m_fade_hint == "LONG" else "sell"
        fade = result.five_m_fade_data or {}
        await self._maybe_emit_signal(sym, result, detected_on="5m", entry=fade.get("entry"), sl=fade.get("sl"), tp1=fade.get("tp"), tp2=None, hold_min=60, trade_style="BB_FADE", regime=result.regime, side=side, vol_ratio=None, adx_1h=result.context.get("adx_1h"), fvg_confirmed=False)

    async def _on_1h_close(self, sym: str, candle: Candle) -> None:
        self._write_feature_close(sym, candle, "1H", 60)

    async def _on_4h_close(self, sym: str, candle: Candle) -> None:
        self._write_feature_close(sym, candle, "4H", 240)

    def _write_feature_close(self, sym: str, candle: Candle, tf: str, tf_minutes: int) -> Any | None:
        result = self._compute(sym, tf_minutes=tf_minutes)
        if result is None:
            return None
        try:
            write_feature_row(result, symbol=sym, tf=tf, candle=candle)
        except Exception as exc:
            self._log(f"FEATURE write error | {sym} {tf} | {exc}")
        return result

    def _compute(self, sym: str, tf_minutes: int) -> Any | None:
        candles_15m = list(reversed(self.feed.get_candles(sym, "candle15m", 120)))
        candles_1h = list(reversed(self.feed.get_candles(sym, "candle1H", 100)))
        candles_4h = list(reversed(self.feed.get_candles(sym, "candle4H", 80)))
        candles_5m = list(reversed(self.feed.get_candles(sym, "candle5m", 180)))
        if len(candles_15m) < 50 or len(candles_1h) < 50 or len(candles_4h) < 50 or len(candles_5m) < 30:
            return None
        return compute_signal(candles_15m=candles_15m, candles_1h=candles_1h, candles_4h=candles_4h, candles_5m=candles_5m, symbol=_swap_to_base(sym), config=self.strategy_cfg, captured_at_iso=_ts_utc(candles_15m[0][0], tf_minutes))

    def _cheap_adx_check(self, candles_1h: list[Candle]) -> float:
        if len(candles_1h) < 20:
            return 0.0
        newest_first = list(reversed(candles_1h[-30:]))
        highs, lows, closes = parse_candles(newest_first)
        return float(calc_adx(highs, lows, closes, period=9, bar_index=-2)[0])

    def _fvg_confirmed(self, sym: str, side: str) -> bool:
        if not self.strategy_cfg.get("trending_require_fvg", False):
            return False
        candles_15m = list(reversed(self.feed.get_candles(sym, "candle15m", 120)))
        if not candles_15m:
            return False
        price = float(candles_15m[-1][4])
        direction = "bull" if side == "buy" else "bear"
        for gap in find_fvg(candles_15m, direction, lookback=10):
            if gap["low"] <= price <= gap["high"]:
                return True
        return False

    async def _maybe_emit_signal(self, sym: str, result: Any, *, detected_on: str, entry: float | None, sl: float | None, tp1: float | None, tp2: float | None, hold_min: int | None, trade_style: str, regime: str, side: str, vol_ratio: float | None, adx_1h: float | None, fvg_confirmed: bool) -> None:
        if entry is None or sl is None or tp1 is None or hold_min is None:
            return
        self._cleanup_active_setups()
        now = time.time()
        key = f"{sym}:{trade_style if trade_style == 'BB_FADE' else regime}:{side}"
        setup = self.active_setups.get(key)
        cooldown = int(self.main_cfg["cooldown_minutes"]) * 60
        if setup and now - float(setup["ts"]) < cooldown:
            return
        payload = {
            "id": str(uuid.uuid4()),
            "ts": _ts_utc(),
            "symbol": sym,
            "regime": regime,
            "side": side,
            "trade_style": trade_style,
            "entry": round(float(entry), 8),
            "sl": round(float(sl), 8),
            "tp1": round(float(tp1), 8),
            "tp2": round(float(tp2), 8) if tp2 is not None else None,
            "hold_min": int(hold_min),
            "detected_on": detected_on,
            "vol_ratio": round(float(vol_ratio), 3) if vol_ratio is not None else None,
            "adx_1h": round(float(adx_1h), 2) if adx_1h is not None else None,
            "fvg_confirmed": bool(fvg_confirmed),
            "source": "ws_main_screener",
        }
        SIGNALS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SIGNALS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        try:
            write_snapshot(result, payload["id"], "ws_main_screener", detected_on, payload=payload)
        except Exception as exc:
            self._log(f"SNAPSHOT write error | {sym} | {exc}")
        self.active_setups[key] = {"ts": now, "signal_id": payload["id"], "expire_ts": now + int(hold_min + 30) * 60}
        self._roll_signal_day()
        self.signals_today += 1
        self._log(f"SIGNAL | {sym} {trade_style} {regime} {side} | on={detected_on} hold={hold_min}m")
        try:
            await self._deliver_signal(sym, result, payload, detected_on=detected_on)
        except Exception as exc:
            self._log(f"DELIVERY error | {sym} | {exc}")

    def _delivery_snapshot(self, symbol: str, result: Any, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = build_analysis_snapshot(symbol, result.captured_at, result, open_interest=None)
        ctx = snapshot.setdefault("llm_context", {})
        ctx.update(
            {
                "entry_signal": "ENTRY",
                "trade_style_hint": payload["trade_style"],
                "side": payload["side"],
                "entry_price": payload["entry"],
                "sl_price": payload["sl"],
                "tp1_price": payload["tp1"],
                "tp2_price": payload["tp2"],
                "max_hold_minutes": payload["hold_min"],
                "regime": payload["regime"],
            }
        )
        snapshot["entry_signal"] = "ENTRY"
        snapshot["trade_style"] = payload["trade_style"]
        snapshot["side"] = payload["side"]
        snapshot["regime"] = payload["regime"]
        return snapshot

    def _payload_summary(self, symbol: str, payload: dict[str, Any]) -> str:
        side_label = "LONG" if payload["side"] == "buy" else "SHORT"
        lines = [
            f"{symbol} {side_label} {payload['trade_style']} {payload['regime']}",
            f"Entry: {payload['entry']}",
            f"SL: {payload['sl']}",
            f"TP1: {payload['tp1']}",
        ]
        if payload.get("tp2") is not None:
            lines.append(f"TP2: {payload['tp2']}")
        lines.append(f"Max hold: {payload['hold_min']} min")
        return "\n".join(lines)

    async def _deliver_signal(self, sym: str, result: Any, payload: dict[str, Any], *, detected_on: str) -> None:
        active = _active_chat_ids()
        if not active:
            return

        base_symbol = _swap_to_base(sym)
        run_dir = SCREENER_LOG_DIR / f"{datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S')}_{base_symbol}_{payload['trade_style']}"
        run_dir.mkdir(parents=True, exist_ok=True)
        png_path = run_dir / f"{base_symbol}_chart.png"
        raw_15m = list(reversed(self.feed.get_candles(sym, "candle15m", 100)))

        if raw_15m:
            levels = {
                "sl": payload["sl"],
                "tp1": payload["tp1"],
                "tp2": payload.get("tp2"),
                "entry_price": payload["entry"],
            }
            generate_chart_png(
                raw_15m,
                result.indicators,
                base_symbol,
                result.captured_at,
                str(png_path),
                llm_levels=levels,
                entry_signal="ENTRY",
                direction=payload["side"],
                trade_style=payload["trade_style"],
            )

        snapshot = self._delivery_snapshot(base_symbol, result, payload)
        llm_text = await generate_client_text(base_symbol, result.captured_at, snapshot, str(png_path), client_summary=None)
        delivery_text = llm_text or (
            self._payload_summary(base_symbol, payload) if payload["trade_style"] == "BB_FADE" else result.engine_summary
        )

        arrow = "🟢" if payload["side"] == "buy" else "🔴"
        header = f"{arrow} <b>{base_symbol}</b> — сигнал входа\n<i>Актуально до {result.expiry_time}</i>\n\n"
        text = header + (html.escape(delivery_text) if llm_text else _format_telegram(delivery_text))

        sent = 0
        for chat_id in active:
            try:
                await send_message_to(chat_id, text)
                if png_path.exists():
                    await send_photo_to(chat_id, str(png_path))
                sent += 1
            except Exception as exc:
                self._log(f"SEND error | {sym} chat={chat_id} | {exc}")
            await asyncio.sleep(0.2)
        self._log(f"DELIVERED | {sym} {payload['trade_style']} {detected_on} | users={sent}/{len(active)}")

    def _cleanup_active_setups(self) -> None:
        now = time.time()
        stale = [key for key, value in self.active_setups.items() if now >= float(value.get("expire_ts", 0))]
        for key in stale:
            self.active_setups.pop(key, None)

    def _has_active_setup(self, sym: str) -> bool:
        self._cleanup_active_setups()
        return any(key.startswith(f"{sym}:") for key in self.active_setups)

    def _roll_signal_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self.signal_day:
            self.signal_day = today
            self.signals_today = 0

    async def _write_universe_cache(self, universe: list[str]) -> None:
        payload = {"updated_at": _ts_utc(), "pairs": universe, "count": len(universe)}
        await self._atomic_write_json(UNIVERSE_CACHE, payload)

    async def _atomic_write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        for _ in range(5):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                await asyncio.sleep(0.1)

    async def _heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(60)
            self._cleanup_active_setups()
            self._roll_signal_day()
            self._log(f"HEARTBEAT | pairs={len(self.subscribed_pairs)} | signals_today={self.signals_today} | active_setups={len(self.active_setups)}")

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGINT", "SIGTERM"):
            if not hasattr(signal, sig_name):
                continue
            try:
                loop.add_signal_handler(getattr(signal, sig_name), self.stop_event.set)
            except NotImplementedError:
                pass

    def _context_gate_reject(self, result: Any, sym: str) -> bool:
        gate = self.main_cfg.get("context_gate", {})
        if not gate.get("skip_asia_trending", False):
            return False
        if result.regime != "TRENDING":
            return False
        utc_hour = datetime.now(timezone.utc).hour
        if utc_hour < int(gate.get("asia_utc_end", 6)):
            self._log(f"GATE skip | asia_trending | {sym} | regime={result.regime} | {utc_hour:02d}:00 UTC")
            return True
        return False

    def _log(self, message: str) -> None:
        self.logger.info(f"[{_now()}] {message}")


def main() -> None:
    asyncio.run(MainScreener().run())


if __name__ == "__main__":
    main()
