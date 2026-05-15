from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp


ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_PATH = ROOT / "scripts" / "ws" / "cache" / "main_universe.json"
CACHE_DIR = ROOT / "scripts" / "backtest" / "cache" / "screener"
LOG_PATH = ROOT / "scripts" / "backtest" / "loader.log"

ENDPOINT = "https://www.okx.com/api/v5/market/history-candles"
TIMEFRAMES = ["5m", "15m", "1H", "4H"]
BAR_MINUTES = {"5m": 5, "15m": 15, "1H": 60, "4H": 240}
PINNED_PAIRS = [
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "XRP-USDT-SWAP",
    "DOGE-USDT-SWAP",
    "ADA-USDT-SWAP",
]
CACHE_MAX_AGE_HOURS = 23.0
GLOBAL_REQUEST_INTERVAL_SEC = 0.15


class RequestPacer:
    def __init__(self, min_interval_sec: float) -> None:
        self.min_interval_sec = min_interval_sec
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_at
            if elapsed < self.min_interval_sec:
                await asyncio.sleep(self.min_interval_sec - elapsed)
            self._last_request_at = time.monotonic()


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def read_pairs() -> list[str]:
    if not UNIVERSE_PATH.exists():
        logging.warning("Universe file not found, using pinned pairs: %s", UNIVERSE_PATH)
        return PINNED_PAIRS[:]

    try:
        payload = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
        pairs = payload.get("pairs", [])
        pairs = [str(pair).strip() for pair in pairs if str(pair).strip()]
        if not pairs:
            raise ValueError("empty pairs list")
        return pairs[:30]
    except Exception as exc:
        logging.exception("Failed to read universe file %s: %s", UNIVERSE_PATH, exc)
        return PINNED_PAIRS[:]


def cache_path(inst_id: str, bar: str, days: int) -> Path:
    return CACHE_DIR / f"{inst_id}_{bar}_{days}d.pkl"


def cache_age_hours(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 3600.0


def cache_has_required_depth(data: list[Any], days: int, bar: str) -> bool:
    if not data:
        return False

    needed = expected_candles(days, bar)
    min_count = max(1, needed - 2)
    if len(data) < min_count:
        return False

    try:
        first_ts = int(data[0][0])
        last_ts = int(data[-1][0])
    except (TypeError, ValueError, IndexError):
        return False

    span_days = (last_ts - first_ts) / 1000 / 86400
    return span_days >= days - 0.75


def load_fresh_cache(
    path: Path,
) -> tuple[list[list[float | int]] | None, float | None, str | None]:
    if not path.exists():
        return None, None, None

    age = cache_age_hours(path)
    if age > CACHE_MAX_AGE_HOURS:
        return None, age, "stale"

    try:
        with path.open("rb") as fh:
            data = pickle.load(fh)
        if not isinstance(data, list):
            raise ValueError("cache payload is not a list")
        return data, age, None
    except Exception as exc:
        logging.exception("Failed to load cache %s: %s", path, exc)
        return None, age, "invalid"


def save_cache(path: Path, candles: list[list[float | int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("wb") as fh:
        pickle.dump(candles, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp_path.replace(path)


def expected_candles(days: int, bar: str) -> int:
    return int(days * 24 * 60 / BAR_MINUTES[bar])


def parse_confirmed_candle(row: list[Any]) -> list[float | int] | None:
    if len(row) < 9 or str(row[8]) != "1":
        return None

    try:
        return [
            int(row[0]),
            float(row[1]),
            float(row[2]),
            float(row[3]),
            float(row[4]),
            float(row[5]),
            float(row[6]),
            float(row[7]),
        ]
    except (TypeError, ValueError):
        return None


async def fetch_batch(
    session: aiohttp.ClientSession,
    request_sem: asyncio.Semaphore,
    request_pacer: RequestPacer,
    inst_id: str,
    bar: str,
    after: int | None,
) -> list[list[Any]] | None:
    params: dict[str, str | int] = {"instId": inst_id, "bar": bar, "limit": 300}
    if after is not None:
        params["after"] = after

    for attempt in range(3):
        try:
            async with request_sem:
                await request_pacer.wait()
                async with session.get(ENDPOINT, params=params) as response:
                    if response.status != 200:
                        body = await response.text()
                        raise RuntimeError(f"HTTP {response.status}: {body[:300]}")
                    payload = await response.json()

            if str(payload.get("code")) != "0":
                raise RuntimeError(f"OKX code={payload.get('code')} msg={payload.get('msg')}")

            data = payload.get("data", [])
            if not isinstance(data, list):
                raise RuntimeError("OKX response data is not a list")
            return data
        except Exception as exc:
            if attempt >= 2:
                logging.exception(
                    "Failed request inst=%s bar=%s after=%s: %s",
                    inst_id,
                    bar,
                    after,
                    exc,
                )
                return None
            await asyncio.sleep(2.0)

    return None


async def fetch_candles(
    session: aiohttp.ClientSession,
    request_sem: asyncio.Semaphore,
    request_pacer: RequestPacer,
    inst_id: str,
    bar: str,
    days: int,
) -> tuple[list[list[float | int]], bool]:
    needed = expected_candles(days, bar)
    cutoff_ms = int((time.time() - days * 24 * 3600) * 1000)
    candles_by_ts: dict[int, list[float | int]] = {}
    after: int | None = None
    previous_after: int | None = None

    while len(candles_by_ts) < needed:
        rows = await fetch_batch(session, request_sem, request_pacer, inst_id, bar, after)
        if rows is None:
            return [], False
        if not rows:
            break

        batch_ts: list[int] = []
        reached_cutoff = False
        for row in rows:
            try:
                ts_ms = int(row[0])
            except (TypeError, ValueError, IndexError):
                continue

            batch_ts.append(ts_ms)
            if ts_ms < cutoff_ms:
                reached_cutoff = True
                continue

            candle = parse_confirmed_candle(row)
            if candle is not None:
                candles_by_ts[int(candle[0])] = candle

        print(f"[{inst_id}] {bar}: loading... {len(candles_by_ts)}/{needed} candles")

        if not batch_ts:
            break

        oldest_ts = min(batch_ts)
        previous_after, after = after, oldest_ts
        if reached_cutoff or previous_after == after:
            break

        await asyncio.sleep(0.15)

    candles = sorted(candles_by_ts.values(), key=lambda candle: int(candle[0]))
    if candles:
        days_loaded = (int(candles[-1][0]) - int(candles[0][0])) / 1000 / 86400
        print(f"[{inst_id}] {bar}: done ({len(candles)} candles, {days_loaded:.1f} days)")
    else:
        print(f"[{inst_id}] {bar}: done (0 candles, 0.0 days)")

    return candles, True


async def load_pair(
    session: aiohttp.ClientSession,
    request_sem: asyncio.Semaphore,
    request_pacer: RequestPacer,
    inst_id: str,
    days: int,
) -> tuple[str, dict[str, list[list[float | int]]]]:
    pair_data: dict[str, list[list[float | int]]] = {}

    for bar in TIMEFRAMES:
        path = cache_path(inst_id, bar, days)
        cached, age, cache_status = load_fresh_cache(path)
        if cached is not None:
            age_text = f"{age:.1f}h" if age is not None else "unknown"
            print(f"[{inst_id}] {bar}: using cache (age: {age_text})")
            pair_data[bar] = cached
            continue

        if age is not None and cache_status is not None:
            logging.info(
                "Cache not usable inst=%s bar=%s status=%s age=%.2fh",
                inst_id,
                bar,
                cache_status,
                age,
            )

        candles, complete = await fetch_candles(
            session,
            request_sem,
            request_pacer,
            inst_id,
            bar,
            days,
        )
        if complete and candles:
            save_cache(path, candles)
        elif not complete:
            logging.error("Skipped cache save after request failure inst=%s bar=%s", inst_id, bar)
        pair_data[bar] = candles
        await asyncio.sleep(0.15)

    return inst_id, pair_data


def date_range(data: dict[str, dict[str, list[list[float | int]]]]) -> tuple[str, str]:
    timestamps: list[int] = []
    for pair_data in data.values():
        for candles in pair_data.values():
            timestamps.extend(int(candle[0]) for candle in candles)

    if not timestamps:
        now = datetime.now(timezone.utc)
        start_ts = now.timestamp() - 60 * 24 * 3600
        start = datetime.fromtimestamp(start_ts, timezone.utc)
        return start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")

    start = datetime.fromtimestamp(min(timestamps) / 1000, timezone.utc)
    end = datetime.fromtimestamp(max(timestamps) / 1000, timezone.utc)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def pair_has_all_timeframes(pair_data: dict[str, list[list[float | int]]]) -> bool:
    return all(pair_data.get(bar) for bar in TIMEFRAMES)


def pair_has_full_depth(pair_data: dict[str, list[list[float | int]]], days: int) -> bool:
    return all(cache_has_required_depth(pair_data.get(bar, []), days, bar) for bar in TIMEFRAMES)


def short_history_pairs(
    data: dict[str, dict[str, list[list[float | int]]]],
    days: int,
) -> list[str]:
    short: list[str] = []
    for inst_id, pair_data in sorted(data.items()):
        if not pair_has_all_timeframes(pair_data):
            short.append(f"{inst_id}: missing timeframe")
            continue
        if pair_has_full_depth(pair_data, days):
            continue

        spans: list[float] = []
        for candles in pair_data.values():
            if candles:
                span_days = (int(candles[-1][0]) - int(candles[0][0])) / 1000 / 86400
                spans.append(span_days)
        max_span = max(spans) if spans else 0.0
        short.append(f"{inst_id}: {max_span:.1f}d")
    return short


async def run_loader(days: int, concurrency: int) -> dict[str, dict[str, list[list[float | int]]]]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pairs = read_pairs()
    request_sem = asyncio.Semaphore(concurrency)
    request_pacer = RequestPacer(GLOBAL_REQUEST_INTERVAL_SEC)
    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(limit=max(concurrency, 1))
    loaded: dict[str, dict[str, list[list[float | int]]]] = {}

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        tasks = [load_pair(session, request_sem, request_pacer, inst_id, days) for inst_id in pairs]
        for completed in asyncio.as_completed(tasks):
            inst_id, pair_data = await completed
            loaded[inst_id] = pair_data

    loaded_pairs = sum(1 for pair_data in loaded.values() if pair_has_all_timeframes(pair_data))
    full_depth_pairs = sum(1 for pair_data in loaded.values() if pair_has_full_depth(pair_data, days))
    short_pairs = short_history_pairs(loaded, days)
    if loaded_pairs == len(pairs):
        print(f"Done: {loaded_pairs}/{len(pairs)} pairs loaded. Ready for backtest.")
    else:
        print(f"Done: {loaded_pairs}/{len(pairs)} pairs loaded. Missing data remains.")
    print(f"Full {days}d depth: {full_depth_pairs}/{len(pairs)} pairs.")
    if short_pairs:
        print("Short history from OKX: " + "; ".join(short_pairs))
    return loaded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load OKX historical candles for screener backtests.")
    parser.add_argument("--days", type=int, default=60, help="History depth in days, minimum 60.")
    parser.add_argument("--concurrency", type=int, default=5, help="Maximum parallel OKX requests.")
    args = parser.parse_args()

    if args.days < 60:
        parser.error("--days must be at least 60")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    return args


def main() -> None:
    setup_logging()
    args = parse_args()
    data = asyncio.run(run_loader(days=args.days, concurrency=args.concurrency))
    loaded_pairs = sum(1 for pair_data in data.values() if pair_has_all_timeframes(pair_data))
    full_depth_pairs = sum(1 for pair_data in data.values() if pair_has_full_depth(pair_data, args.days))
    short_pairs = short_history_pairs(data, args.days)
    start_date, end_date = date_range(data)

    print()
    print("=== DATA READY ===")
    print(f"Pairs loaded: {loaded_pairs}")
    print(f"Full {args.days}d pairs: {full_depth_pairs}")
    if short_pairs:
        print("Short history from OKX: " + "; ".join(short_pairs))
    print(f"Timeframes: {', '.join(TIMEFRAMES)}")
    print(f"Date range: {start_date} - {end_date}")
    print(f"Cache dir: {CACHE_DIR.relative_to(ROOT).as_posix()}/")
    print("Run bt_screener_sim.py to start backtesting.")


if __name__ == "__main__":
    main()
