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

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOLS   = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
             "XRP-USDT-SWAP", "DOGE-USDT-SWAP"]
TAPE_DIR  = Path(__file__).parent / "tape"
KEEP_DAYS = 60          # delete files older than this
WS_URL    = "wss://ws.okx.com:8443/ws/v5/public"
PING_SEC  = 25          # OKX drops connection after 30s without ping
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
        print(f"[tape] сжат: {gz_file.name}")

    # Cleanup old files
    cutoff = datetime.now(timezone.utc).timestamp() - KEEP_DAYS * 86400
    for f in TAPE_DIR.glob("*.csv.gz"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            print(f"[tape] удалён старый файл: {f.name}")


class TapeWriter:
    """Append trades to daily CSV file, rotate at midnight UTC."""

    def __init__(self):
        TAPE_DIR.mkdir(parents=True, exist_ok=True)
        self._current_date = _today_utc()
        self._file   = None
        self._writer = None
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
        print(f"[tape] пишем в: {path.name}")

    def write(self, ts_ms: str, symbol: str, side: str,
              price: str, size: str, trade_id: str) -> None:
        today = _today_utc()
        if today != self._current_date:
            # Midnight rotation
            prev_date = self._current_date
            self._current_date = today
            self._file.close()
            _rotate(prev_date)
            self._open()
        self._writer.writerow([ts_ms, symbol, side, price, size, trade_id])
        self._file.flush()

    def write_gap(self, symbol: str) -> None:
        """Mark reconnection gap so analyst knows data was missing."""
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        self._writer.writerow([ts_ms, symbol, "GAP", "", "", ""])
        self._file.flush()

    def close(self):
        if self._file:
            self._file.close()


async def _ping_loop(ws):
    """Send ping every PING_SEC to keep OKX connection alive."""
    while True:
        await asyncio.sleep(PING_SEC)
        try:
            await ws.send("ping")
        except Exception:
            break


async def _record(writer: TapeWriter):
    subscribe_msg = json.dumps({
        "op": "subscribe",
        "args": [{"channel": "trades", "instId": s} for s in SYMBOLS]
    })

    reconnect_delay = 5
    while True:
        try:
            print(f"[tape] подключение к OKX WebSocket...")
            async with websockets.connect(WS_URL, ping_interval=None) as ws:
                await ws.send(subscribe_msg)
                print(f"[tape] подписка на {len(SYMBOLS)} пар")
                reconnect_delay = 5  # reset on success

                asyncio.create_task(_ping_loop(ws))

                async for raw in ws:
                    if raw == "pong":
                        continue

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("event"):
                        # subscribe confirm / error
                        if msg.get("event") == "error":
                            print(f"[tape] ошибка подписки: {msg}")
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

        except Exception as e:
            # Mark gap for all symbols on disconnect
            for sym in SYMBOLS:
                writer.write_gap(sym)
            print(f"[tape] разрыв: {e}. Переподключение через {reconnect_delay}с...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)


async def main():
    TAPE_DIR.mkdir(parents=True, exist_ok=True)
    writer = TapeWriter()
    print(f"[tape] запись в {TAPE_DIR}")
    print(f"[tape] пары: {', '.join(SYMBOLS)}")
    print(f"[tape] хранение: {KEEP_DAYS} дней")
    print("[tape] Ctrl+C для остановки")
    try:
        await _record(writer)
    except KeyboardInterrupt:
        print("\n[tape] остановка.")
    finally:
        writer.close()


if __name__ == "__main__":
    asyncio.run(main())
