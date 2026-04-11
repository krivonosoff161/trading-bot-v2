"""
tape_recorder.py — OKX trades stream recorder.

Subscribes to public 'trades' WebSocket channel for configured pairs.
Writes every trade to daily CSV files in scripts/tape/.
Compresses previous day's file on midnight rotation.
Deletes files older than KEEP_DAYS.

Does NOT require API keys — public channel only.

Usage:
    python scripts/tape_recorder.py

Storage estimate: ~2-5 MB/day gzipped for 5 pairs.
"""
import asyncio
import csv
import gzip
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# ── Logging ───────────────────────────────────────────────────────────────────
_LOG_FILE = Path(__file__).parent / "tape" / "tape.log"

def _log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}"
    print(line)
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOLS   = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
             "XRP-USDT-SWAP", "DOGE-USDT-SWAP"]
TAPE_DIR  = Path(__file__).parent / "tape"
KEEP_DAYS   = 60        # delete files older than this
WS_URL      = "wss://ws.okx.com:8443/ws/v5/public"
PING_SEC    = 25        # OKX drops connection after 30s without ping
FLUSH_EVERY = 100       # flush to disk every N trades
# ──────────────────────────────────────────────────────────────────────────────

try:
    import websockets
except ImportError:
    print("websockets не установлен. Запустите: pip install websockets")
    sys.exit(1)


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _csv_path(date_str: str) -> Path:
    return TAPE_DIR / f"{date_str}.csv"


def _gz_path(date_str: str) -> Path:
    return TAPE_DIR / f"{date_str}.csv.gz"


def _rotate(date_str: str) -> None:
    """Gzip yesterday's CSV and delete files older than KEEP_DAYS."""
    csv_file = _csv_path(date_str)
    gz_file  = _gz_path(date_str)

    if csv_file.exists() and not gz_file.exists():
        with open(csv_file, "rb") as f_in, gzip.open(gz_file, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        csv_file.unlink()
        _log(f"[tape] сжат: {gz_file.name}")

    # Cleanup old files
    cutoff = datetime.now(timezone.utc).timestamp() - KEEP_DAYS * 86400
    for f in TAPE_DIR.glob("*.csv.gz"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            _log(f"[tape] удалён старый файл: {f.name}")


class TapeWriter:
    """Append trades to daily CSV file, rotate at midnight UTC."""

    def __init__(self):
        TAPE_DIR.mkdir(parents=True, exist_ok=True)
        self._current_date = _today_utc()
        self._file    = None
        self._writer  = None
        self._buf     = 0       # trades since last flush
        self._total   = 0       # trades this session
        self._open()

    def _open(self):
        if self._file:
            self._file.close()
        path = _csv_path(self._current_date)
        write_header = not path.exists()
        self._file   = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        if write_header:
            self._writer.writerow(["ts_ms", "symbol", "side", "price", "size", "trade_id"])
        _log(f"[tape] пишем в: {path.name}")

    def write(self, ts_ms: str, symbol: str, side: str,
              price: str, size: str, trade_id: str) -> None:
        today = _today_utc()
        if today != self._current_date:
            # Midnight rotation
            prev_date = self._current_date
            self._current_date = today
            self._file.flush()
            self._file.close()
            _rotate(prev_date)
            self._open()
            self._buf = 0
        self._writer.writerow([ts_ms, symbol, side, price, size, trade_id])
        self._buf   += 1
        self._total += 1
        if self._buf >= FLUSH_EVERY:
            self._file.flush()
            self._buf = 0

    def write_gap(self, symbol: str) -> None:
        """Mark reconnection gap so analyst knows data was missing."""
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        self._writer.writerow([ts_ms, symbol, "GAP", "", "", ""])
        self._file.flush()
        self._buf = 0

    def close(self):
        if self._file:
            self._file.flush()
            self._file.close()


async def _ping_loop(ws):
    """Send ping every PING_SEC to keep OKX connection alive."""
    while True:
        await asyncio.sleep(PING_SEC)
        try:
            await ws.send("ping")
        except Exception:
            return  # connection closed — task exits cleanly


async def _record(writer: TapeWriter):
    subscribe_msg = json.dumps({
        "op": "subscribe",
        "args": [{"channel": "trades", "instId": s} for s in SYMBOLS]
    })

    reconnect_delay = 5
    _stat_interval  = 10_000   # log stats every N trades
    _stat_next      = _stat_interval

    while True:
        ping_task = None
        try:
            _log("[tape] подключение к OKX WebSocket...")
            async with websockets.connect(WS_URL, ping_interval=None) as ws:
                await ws.send(subscribe_msg)
                _log(f"[tape] подписка на {len(SYMBOLS)} пар")
                reconnect_delay = 5  # reset on success

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
                            _log(f"[tape] ошибка подписки: {msg}")
                        continue

                    data = msg.get("data")
                    if not data:
                        continue

                    for trade in data:
                        writer.write(
                            ts_ms    = trade.get("ts", ""),
                            symbol   = trade.get("instId", ""),
                            side     = trade.get("side", ""),
                            price    = trade.get("px", ""),
                            size     = trade.get("sz", ""),
                            trade_id = trade.get("tradeId", ""),
                        )

                    # Periodic stats
                    if writer._total >= _stat_next:
                        _log(f"[tape] записано сделок: {writer._total:,}")
                        _stat_next = writer._total + _stat_interval

        except Exception as e:
            for sym in SYMBOLS:
                writer.write_gap(sym)
            _log(f"[tape] разрыв: {e}. Переподключение через {reconnect_delay}с...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
        finally:
            if ping_task and not ping_task.done():
                ping_task.cancel()


async def main():
    TAPE_DIR.mkdir(parents=True, exist_ok=True)
    writer = TapeWriter()
    _log(f"[tape] запись в {TAPE_DIR}")
    _log(f"[tape] пары: {', '.join(SYMBOLS)}")
    _log(f"[tape] хранение: {KEEP_DAYS} дней | флаш каждые {FLUSH_EVERY} трейдов")
    _log("[tape] Ctrl+C для остановки")
    try:
        await _record(writer)
    except KeyboardInterrupt:
        _log(f"[tape] остановка. Всего записано: {writer._total:,} сделок.")
    finally:
        writer.close()


if __name__ == "__main__":
    asyncio.run(main())
