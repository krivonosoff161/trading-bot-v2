"""OKX public trades stream recorder.

Reads .env:
    TAPE_DATA_DIR   default: scripts/tape
    TAPE_SYMBOLS    optional comma-separated instIds, e.g. BTC-USDT-SWAP,ETH-USDT-SWAP
    TAPE_TOP_N_PAIRS default: 30, used when TAPE_SYMBOLS is empty
    TAPE_KEEP_DAYS  default: 365

Writes:
    {TAPE_DATA_DIR}/{symbol}/{date_utc}.csv
    {TAPE_DATA_DIR}/{symbol}/{date_utc}.csv.gz after UTC day rotation
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DEFAULT_TAPE_DIR = ROOT / "scripts" / "tape"
TAPE_DIR = Path(os.getenv("TAPE_DATA_DIR") or DEFAULT_TAPE_DIR)
KEEP_DAYS = int(os.getenv("TAPE_KEEP_DAYS", "365"))
TAPE_TOP_N_PAIRS = int(os.getenv("TAPE_TOP_N_PAIRS", "30"))
TAPE_SYMBOLS = os.getenv("TAPE_SYMBOLS", "")
WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
REST_TICKERS_URL = "https://www.okx.com/api/v5/market/tickers"
PING_SEC = 25
FLUSH_EVERY = int(os.getenv("TAPE_FLUSH_EVERY", "500"))
PINNED_PAIRS = [
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "XRP-USDT-SWAP",
    "DOGE-USDT-SWAP",
    "ADA-USDT-SWAP",
]
LOG_FILE = TAPE_DIR / "tape.log"

try:
    import websockets
except ImportError:
    print("websockets is not installed. Run: pip install websockets")
    sys.exit(1)


def _log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not symbol:
        return ""
    return symbol if symbol.endswith("-SWAP") else f"{symbol}-SWAP"


def _symbol_dir(symbol: str) -> Path:
    return TAPE_DIR / symbol


def _csv_path(symbol: str, date_str: str) -> Path:
    return _symbol_dir(symbol) / f"{date_str}.csv"


def _gz_path(symbol: str, date_str: str) -> Path:
    return _symbol_dir(symbol) / f"{date_str}.csv.gz"


def _is_excluded_symbol(inst_id: str) -> bool:
    if not inst_id.endswith("-USDT-SWAP"):
        return True
    upper = inst_id.upper()
    if any(token in upper for token in ("UP-USDT", "DOWN-USDT", "3L-USDT", "3S-USDT")):
        return True
    return inst_id.count("-") > 2


def _env_symbols() -> list[str]:
    if not TAPE_SYMBOLS.strip():
        return []
    symbols = [_normalize_symbol(item) for item in TAPE_SYMBOLS.replace(";", ",").split(",")]
    return list(dict.fromkeys(symbol for symbol in symbols if symbol))


async def _fetch_top_symbols() -> list[str]:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(REST_TICKERS_URL, params={"instType": "SWAP"}) as resp:
                payload = await resp.json(content_type=None)
        if payload.get("code") != "0":
            raise RuntimeError(payload.get("msg") or f"OKX code={payload.get('code')}")
        scored: list[tuple[str, float]] = []
        for row in payload.get("data", []):
            inst_id = row.get("instId", "")
            if _is_excluded_symbol(inst_id):
                continue
            try:
                scored.append((inst_id, float(row.get("volCcy24h", 0.0) or 0.0)))
            except (TypeError, ValueError):
                continue
        scored.sort(key=lambda item: item[1], reverse=True)
        dynamic = [sym for sym, _ in scored[:TAPE_TOP_N_PAIRS]]
        return list(dict.fromkeys(PINNED_PAIRS + dynamic))[: max(TAPE_TOP_N_PAIRS, len(PINNED_PAIRS))]
    except Exception as exc:
        _log(f"[tape] universe fetch failed: {exc}; using pinned pairs")
        return PINNED_PAIRS


async def _resolve_symbols() -> list[str]:
    return _env_symbols() or await _fetch_top_symbols()


def _rotate_symbol_day(symbol: str, date_str: str) -> None:
    csv_file = _csv_path(symbol, date_str)
    gz_file = _gz_path(symbol, date_str)
    if csv_file.exists() and not gz_file.exists():
        with csv_file.open("rb") as f_in, gzip.open(gz_file, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        csv_file.unlink()
        _log(f"[tape] compressed: {symbol}/{gz_file.name}")


def _cleanup_old_files() -> None:
    cutoff = datetime.now(timezone.utc).timestamp() - KEEP_DAYS * 86400
    for path in TAPE_DIR.glob("*/*.csv.gz"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                _log(f"[tape] deleted old file: {path}")
        except FileNotFoundError:
            continue


class TapeWriter:
    """Append trades to per-symbol daily CSV files and rotate at midnight UTC."""

    def __init__(self) -> None:
        TAPE_DIR.mkdir(parents=True, exist_ok=True)
        self._current_date = _today_utc()
        self._files: dict[str, object] = {}
        self._writers: dict[str, csv.writer] = {}
        self._buf = 0
        self._total = 0

    def _open_symbol(self, symbol: str) -> csv.writer:
        if symbol in self._writers:
            return self._writers[symbol]
        path = _csv_path(symbol, self._current_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists()
        file_obj = path.open("a", newline="", encoding="utf-8")
        writer = csv.writer(file_obj)
        if write_header:
            writer.writerow(["ts_ms", "recv_ts_ms", "symbol", "side", "price", "size", "trade_id"])
        self._files[symbol] = file_obj
        self._writers[symbol] = writer
        _log(f"[tape] writing: {path}")
        return writer

    def _rotate_if_needed(self) -> None:
        today = _today_utc()
        if today == self._current_date:
            return
        prev_date = self._current_date
        self.close()
        for symbol_dir in TAPE_DIR.iterdir():
            if symbol_dir.is_dir():
                _rotate_symbol_day(symbol_dir.name, prev_date)
        _cleanup_old_files()
        self._current_date = today
        self._files = {}
        self._writers = {}
        self._buf = 0

    def write(self, ts_ms: str, symbol: str, side: str, price: str, size: str, trade_id: str) -> None:
        self._rotate_if_needed()
        recv_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        writer = self._open_symbol(symbol)
        writer.writerow([ts_ms, recv_ts_ms, symbol, side, price, size, trade_id])
        self._buf += 1
        self._total += 1
        if self._buf >= FLUSH_EVERY:
            self.flush()

    def write_gap(self, symbol: str) -> None:
        self._rotate_if_needed()
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        writer = self._open_symbol(symbol)
        writer.writerow([ts_ms, ts_ms, symbol, "GAP", "", "", ""])
        self.flush()

    def flush(self) -> None:
        for file_obj in self._files.values():
            file_obj.flush()
        self._buf = 0

    def close(self) -> None:
        for file_obj in self._files.values():
            file_obj.flush()
            file_obj.close()
        self._files = {}
        self._writers = {}


async def _ping_loop(ws) -> None:
    while True:
        await asyncio.sleep(PING_SEC)
        try:
            await ws.send("ping")
        except Exception:
            return


async def _record(writer: TapeWriter, symbols: list[str]) -> None:
    subscribe_msg = json.dumps({
        "op": "subscribe",
        "args": [{"channel": "trades", "instId": symbol} for symbol in symbols],
    })
    reconnect_delay = 5
    stat_interval = 10_000
    stat_next = stat_interval

    while True:
        ping_task = None
        try:
            _log("[tape] connecting to OKX WebSocket...")
            async with websockets.connect(WS_URL, ping_interval=None) as ws:
                await ws.send(subscribe_msg)
                _log(f"[tape] subscribed to {len(symbols)} pairs")
                reconnect_delay = 5
                ping_task = asyncio.create_task(_ping_loop(ws))

                async for raw in ws:
                    if raw == "pong":
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("event"):
                        if msg.get("event") == "error":
                            _log(f"[tape] subscription error: {msg}")
                        continue
                    data = msg.get("data")
                    if not data:
                        continue
                    for trade in data:
                        writer.write(
                            ts_ms=trade.get("ts", ""),
                            symbol=trade.get("instId", ""),
                            side=trade.get("side", ""),
                            price=trade.get("px", ""),
                            size=trade.get("sz", ""),
                            trade_id=trade.get("tradeId", ""),
                        )
                    if writer._total >= stat_next:
                        _log(f"[tape] trades written: {writer._total:,}")
                        stat_next = writer._total + stat_interval
        except Exception as exc:
            for symbol in symbols:
                writer.write_gap(symbol)
            _log(f"[tape] disconnected: {exc}. Reconnect in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
        finally:
            if ping_task and not ping_task.done():
                ping_task.cancel()


async def main() -> None:
    symbols = await _resolve_symbols()
    writer = TapeWriter()
    _log(f"[tape] data dir: {TAPE_DIR}")
    _log(f"[tape] pairs: {', '.join(symbols)}")
    _log(f"[tape] retention: {KEEP_DAYS} days | flush every {FLUSH_EVERY} trades")
    _log("[tape] Ctrl+C to stop")
    try:
        await _record(writer, symbols)
    except KeyboardInterrupt:
        _log(f"[tape] stopped. Total trades written: {writer._total:,}")
    finally:
        writer.close()


if __name__ == "__main__":
    asyncio.run(main())
