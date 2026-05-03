"""
Load and cache 30d of OKX 1m candles for WS pump backtests.

Creates one pickle per symbol in scripts/ws/cache/ with a UTC-indexed DataFrame:
    index   -> datetime64[ns, UTC]
    columns -> open, high, low, close, vol
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import pandas as pd


REST_URL = "https://www.okx.com/api/v5/market/history-candles"
BAR = "1m"
LIMIT = 100
REQUEST_DELAY_S = 0.2
FRESH_CACHE_MAX_AGE = timedelta(days=1)
EXPECTED_GAP_MINUTES = 5

DEFAULT_PAIRS = [
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "DOGE-USDT-SWAP",
    "XRP-USDT-SWAP",
    "AI-USDT-SWAP",
    "LAB-USDT-SWAP",
    "PEPE-USDT-SWAP",
    "PENGU-USDT-SWAP",
    "WIF-USDT-SWAP",
    "BONK-USDT-SWAP",
]

CACHE_DIR = Path(__file__).resolve().parent / "cache"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def cache_path_for(symbol: str, days: int) -> Path:
    return CACHE_DIR / f"{symbol}_1m_{days}d.pkl"


def is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = utc_now() - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return age < FRESH_CACHE_MAX_AGE


def summarize_symbol(symbol: str, df: pd.DataFrame) -> None:
    if df.empty:
        print(f"{symbol}: no candles")
        return

    first_ts = df.index[0]
    last_ts = df.index[-1]
    print(
        f"{symbol}: candles={len(df)}  "
        f"first={first_ts.strftime('%Y-%m-%d %H:%M:%S %Z')}  "
        f"last={last_ts.strftime('%Y-%m-%d %H:%M:%S %Z')}"
    )

    gaps = df.index.to_series().diff().dropna()
    bad_gaps = gaps[gaps > pd.Timedelta(minutes=EXPECTED_GAP_MINUTES)]
    if bad_gaps.empty:
        print(f"{symbol}: gaps>{EXPECTED_GAP_MINUTES}m = 0")
        return

    print(f"{symbol}: gaps>{EXPECTED_GAP_MINUTES}m = {len(bad_gaps)}")
    for ts, gap in bad_gaps.head(10).items():
        gap_min = gap.total_seconds() / 60.0
        prev_ts = ts - gap
        print(
            f"  GAP {symbol}: {prev_ts.strftime('%Y-%m-%d %H:%M:%S %Z')} -> "
            f"{ts.strftime('%Y-%m-%d %H:%M:%S %Z')}  ({gap_min:.1f} min)"
        )
    if len(bad_gaps) > 10:
        print(f"  ... {len(bad_gaps) - 10} more gaps omitted")


class CandleLoader:
    def __init__(self, session: aiohttp.ClientSession, days: int):
        self.session = session
        self.days = days

    async def fetch_symbol(self, symbol: str) -> pd.DataFrame | None:
        path = cache_path_for(symbol, self.days)
        if is_cache_fresh(path):
            print(f"{symbol}: using fresh cache {path.name}")
            df = pd.read_pickle(path)
            summarize_symbol(symbol, df)
            return df

        end_ts = utc_now()
        start_ts = end_ts - timedelta(days=self.days)
        start_ms = int(start_ts.timestamp() * 1000)

        all_rows: dict[int, tuple[float, float, float, float, float]] = {}
        after: str | None = None
        loaded_rows = 0
        page = 0

        while True:
            payload = await self._fetch_batch(symbol, after=after)
            if payload is None:
                return None

            rows = payload.get("data", [])
            if not rows:
                break

            page += 1
            oldest_ts_in_batch = None
            reached_start = False

            for row in rows:
                if len(row) < 6:
                    continue

                ts_ms = int(row[0])
                confirm = row[8] if len(row) > 8 else "1"
                if confirm != "1":
                    continue

                if ts_ms < start_ms:
                    reached_start = True
                    oldest_ts_in_batch = ts_ms
                    continue

                all_rows[ts_ms] = (
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    float(row[5]),
                )
                loaded_rows += 1
                if loaded_rows % 1000 == 0:
                    print(f"{symbol}: loaded {loaded_rows} rows")

                oldest_ts_in_batch = ts_ms

            if oldest_ts_in_batch is None:
                break

            if reached_start:
                break

            # OKX returns newest-first; using the oldest ts from the current page
            # avoids duplicating nearly the whole previous page.
            after = str(oldest_ts_in_batch)

            if len(rows) < LIMIT:
                break

        df = self._rows_to_frame(all_rows, start_ts=start_ts, end_ts=end_ts)
        if df.empty:
            print(f"{symbol}: no usable candles returned")
            return None

        df.to_pickle(path)
        print(f"{symbol}: saved {path.name}")
        summarize_symbol(symbol, df)
        return df

    async def _fetch_batch(self, symbol: str, after: str | None) -> dict | None:
        params = {"instId": symbol, "bar": BAR, "limit": str(LIMIT)}
        if after is not None:
            params["after"] = after

        try:
            async with self.session.get(REST_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                payload = await resp.json(content_type=None)
        except Exception as exc:
            print(f"{symbol}: request failed after={after or '-'} error={exc}")
            return None

        if payload.get("code") != "0":
            print(
                f"{symbol}: skip code={payload.get('code')} "
                f"msg={payload.get('msg', '')}"
            )
            return None

        await asyncio.sleep(REQUEST_DELAY_S)
        return payload

    @staticmethod
    def _rows_to_frame(
        rows: dict[int, tuple[float, float, float, float, float]],
        start_ts: datetime,
        end_ts: datetime,
    ) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "vol"])

        ordered = sorted(rows.items())
        index = pd.to_datetime([ts for ts, _ in ordered], unit="ms", utc=True)
        values = [value for _, value in ordered]
        df = pd.DataFrame(values, index=index, columns=["open", "high", "low", "close", "vol"], dtype="float64")
        return df.loc[(df.index >= start_ts) & (df.index <= end_ts)]


async def main(days: int, pairs: list[str], concurrency: int) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession() as session:
        loader = CandleLoader(session=session, days=days)
        semaphore = asyncio.Semaphore(concurrency)

        async def _run_symbol(symbol: str) -> bool:
            async with semaphore:
                df = await loader.fetch_symbol(symbol)
                return df is not None and not df.empty

        results = await asyncio.gather(*[_run_symbol(symbol) for symbol in pairs])
        loaded = sum(1 for ok in results if ok)
    print(f"Done: cached symbols={loaded}/{len(pairs)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--pairs", nargs="+", default=DEFAULT_PAIRS)
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(main(days=args.days, pairs=args.pairs, concurrency=args.concurrency))
