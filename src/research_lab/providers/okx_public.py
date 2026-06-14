# -*- coding: utf-8 -*-
"""OKX public candles provider — read-only, public market-data only, no keys.

Talks ONLY to the OKX public history-candles endpoint
(`/api/v5/market/history-candles`). No API key, no account/private/order endpoints,
no symbol discovery. Supports 1m, 15m, 1h, 4h, and 1d public candles. It fetches
the requested window, paginating backward (`after` = oldest ts seen) with a bounded
page count and a short sleep between pages, then normalizes rows to the canonical
OHLCV format (deduped, sorted, UTC, window-filtered, confirmed candles only).

Network/parse failures raise `MarketDataError`; the caller (data_prepare.prepare)
records this as a structured `provider_error` and writes nothing — it never crashes
the run and never writes partial/misleading data.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

OKX_BASE_URL = "https://www.okx.com"
HISTORY_CANDLES_PATH = "/api/v5/market/history-candles"
MINUTE_MS = 60_000
PAGE_LIMIT = 100            # OKX history-candles max per request
MAX_PAGES = 20             # hard stop: no infinite pagination
# Guard equals the pager's capacity so an accepted window is always fully fetchable
# (never silently truncated). The lab's requirement windows are far smaller (<=360).
MAX_WINDOW_BARS = MAX_PAGES * PAGE_LIMIT  # 2000
DEFAULT_TIMEOUT = 10.0
DEFAULT_SLEEP_SECONDS = 0.2
_USER_AGENT = "strategy-lab-research/1.0 (+public-market-data)"

# Timeframe -> OKX bar parameter + interval in milliseconds
_SUPPORTED_TIMEFRAMES: dict[str, tuple[str, int]] = {
    "1m": ("1m", MINUTE_MS),
    "15m": ("15m", 15 * MINUTE_MS),
    "1h": ("1H", 60 * MINUTE_MS),
    "4h": ("4H", 4 * 60 * MINUTE_MS),
    "1d": ("1D", 24 * 60 * 60_000),
}
SUPPORTED_TIMEFRAMES = tuple(_SUPPORTED_TIMEFRAMES.keys())

# HTTP getter: (url, timeout) -> parsed JSON dict. Injected in tests (no real network).
HttpGet = Callable[[str, float], Any]


class MarketDataError(RuntimeError):
    """Raised on network/HTTP/parse failure or an OKX API error code."""


def to_inst_id(symbol: str) -> str:
    """Map an internal symbol (BTC_USDT_SWAP) to an OKX instId (BTC-USDT-SWAP)."""
    return str(symbol).strip().upper().replace("_", "-")


def _default_http_get(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - fixed public OKX host
        return json.loads(resp.read().decode("utf-8"))


def _resolve_timeframe(timeframe: str) -> tuple[str, int]:
    """Return (okx_bar, interval_ms) for a supported timeframe."""
    key = str(timeframe).strip().lower()
    if key not in _SUPPORTED_TIMEFRAMES:
        raise ValueError(
            f"okx-public supports {', '.join(sorted(_SUPPORTED_TIMEFRAMES))}; got {timeframe!r}"
        )
    return _SUPPORTED_TIMEFRAMES[key]


class OkxPublicMarketDataProvider:
    """Public, read-only OKX candle provider. Supports 1m/15m/1h/4h/1d. No keys."""

    name = "okx-public"
    configured = True

    def __init__(
        self,
        *,
        base_url: str = OKX_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
        max_pages: int = MAX_PAGES,
        http_get: HttpGet | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.sleep_seconds = float(sleep_seconds)
        self.max_pages = max(1, int(max_pages))
        self._http_get = http_get or _default_http_get
        self._sleep = sleep or time.sleep

    def fetch_ohlcv(self, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        okx_bar, interval_ms = _resolve_timeframe(timeframe)
        start_ts, end_ts = int(start_ts), int(end_ts)
        if end_ts < start_ts:
            raise ValueError("end_ts before start_ts")
        if (end_ts - start_ts) // interval_ms + 1 > MAX_WINDOW_BARS:
            raise ValueError(f"window exceeds {MAX_WINDOW_BARS} bars; refuse unbounded fetch")

        inst_id = to_inst_id(symbol)
        collected: dict[int, dict[str, Any]] = {}
        cursor = end_ts + interval_ms  # 'after' returns candles strictly older than cursor

        for _page in range(self.max_pages):
            rows = self._fetch_page(inst_id, okx_bar, cursor)
            if not rows:
                break
            page_oldest: int | None = None
            for raw in rows:
                ts = _row_ts(raw)
                if ts is None or _is_unconfirmed(raw):
                    continue
                page_oldest = ts if page_oldest is None else min(page_oldest, ts)
                if start_ts <= ts <= end_ts and ts not in collected:
                    row = _normalize_row(raw, ts)
                    if row is not None:
                        collected[ts] = row
            if page_oldest is None or page_oldest <= start_ts or len(rows) < PAGE_LIMIT:
                break
            cursor = page_oldest  # next page: candles older than the oldest we just saw
            self._sleep(self.sleep_seconds)

        return [collected[ts] for ts in sorted(collected)]

    def _fetch_page(self, inst_id: str, bar: str, after_ts: int) -> list[Any]:
        query = urllib.parse.urlencode({
            "instId": inst_id, "bar": bar, "after": str(int(after_ts)), "limit": str(PAGE_LIMIT),
        })
        url = f"{self.base_url}{HISTORY_CANDLES_PATH}?{query}"
        try:
            payload = self._http_get(url, self.timeout)
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            raise MarketDataError(f"okx-public request failed: {type(exc).__name__}") from exc
        except json.JSONDecodeError as exc:
            raise MarketDataError("okx-public returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise MarketDataError("okx-public returned an unexpected payload")
        code = str(payload.get("code", "0"))
        if code not in ("0", ""):
            raise MarketDataError(f"okx-public API error code={code}")
        data = payload.get("data")
        return list(data) if isinstance(data, list) else []


def _row_ts(raw: Any) -> int | None:
    try:
        return int(raw[0])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _is_unconfirmed(raw: Any) -> bool:
    # OKX candle index 8 = confirm flag ("1" closed, "0" still forming). Skip "0".
    try:
        return len(raw) > 8 and str(raw[8]) == "0"
    except TypeError:
        return False


def _normalize_row(raw: Any, ts: int) -> dict[str, Any] | None:
    try:
        return {
            "ts": ts,
            "date": dt.datetime.fromtimestamp(ts / 1000, tz=dt.timezone.utc).isoformat(),
            "open": float(raw[1]),
            "high": float(raw[2]),
            "low": float(raw[3]),
            "close": float(raw[4]),
            "vol": float(raw[5]),
        }
    except (IndexError, TypeError, ValueError):
        return None
