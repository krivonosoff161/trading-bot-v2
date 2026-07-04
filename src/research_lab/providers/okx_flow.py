# -*- coding: utf-8 -*-
"""OKX public flow providers — read-only, keyless, market-data only.

Talks ONLY to public endpoints (no API key, no account/private/order endpoints):
  * funding-rate-history (`/api/v5/public/funding-rate-history`) — paginates backward
    via `after` = oldest fundingTime seen;
  * open-interest history (`/api/v5/rubik/stat/contracts/open-interest-history`) —
    per-instId historical OI, paginates backward via `end` = oldest ts seen.
Both use a bounded page count + a short sleep between pages, returning (ts_ms, value)
points inside the requested window. Network/parse failures raise FlowDataError;
callers treat that as "no flow data" and proceed (flow is context, never required).

OI note: a keyless per-instId historical OI series IS public via the rubik
open-interest-history endpoint (verified live: keyless GET, periods 5m..1D, ~100
points/call, paginates backward via `end`). This supersedes the older assumption that
OI had to be a manually recorded slot — though the recorded slot (oi_slot.py) still
works as a fallback / for hand-curated data.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

OKX_BASE_URL = "https://www.okx.com"
FUNDING_HISTORY_PATH = "/api/v5/public/funding-rate-history"
OI_HISTORY_PATH = "/api/v5/rubik/stat/contracts/open-interest-history"
PAGE_LIMIT = 100
MAX_PAGES = 12
DEFAULT_TIMEOUT = 10.0
DEFAULT_SLEEP_SECONDS = 0.2
_USER_AGENT = "strategy-lab-research/1.0 (+public-market-data)"

# Candle timeframe -> OKX OI period token (OI has no 1m; fall back to 5m for 1m candles).
_TF_TO_OI_PERIOD = {"5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H",
                    "2h": "2H", "4h": "4H", "1d": "1D", "1m": "5m"}

HttpGet = Callable[[str, float], Any]


class FlowDataError(RuntimeError):
    """Raised on network/HTTP/parse failure or an OKX API error code."""


def to_inst_id(symbol: str) -> str:
    return str(symbol).strip().upper().replace("_", "-")


def oi_period_for_timeframe(timeframe: str) -> str:
    """Map a candle timeframe to the OKX OI history period (default 5m)."""
    return _TF_TO_OI_PERIOD.get(str(timeframe).strip().lower(), "5m")


def _default_http_get(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - fixed public OKX host
        return json.loads(resp.read().decode("utf-8"))


def _request_data(http_get: HttpGet, url: str, timeout: float, what: str) -> list[Any]:
    """Shared keyless GET + OKX envelope check; returns the data list (never raises on empty)."""
    try:
        payload = http_get(url, timeout)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        raise FlowDataError(f"okx {what} request failed: {type(exc).__name__}") from exc
    except json.JSONDecodeError as exc:
        raise FlowDataError(f"okx {what} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise FlowDataError(f"okx {what} returned an unexpected payload")
    code = str(payload.get("code", "0"))
    if code not in ("0", ""):
        raise FlowDataError(f"okx {what} API error code={code}")
    data = payload.get("data")
    return list(data) if isinstance(data, list) else []


class OkxPublicFundingProvider:
    """Public, read-only OKX funding-rate-history provider. No keys."""

    name = "okx-public-funding"
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

    def fetch_funding(self, symbol: str, start_ts: int, end_ts: int) -> list[tuple[int, float]]:
        """Return [(funding_time_ms, funding_rate)] within [start_ts, end_ts], sorted."""
        start_ts, end_ts = int(start_ts), int(end_ts)
        if end_ts < start_ts:
            raise ValueError("end_ts before start_ts")
        inst_id = to_inst_id(symbol)
        collected: dict[int, float] = {}
        cursor: int | None = None
        for _page in range(self.max_pages):
            rows = self._fetch_page(inst_id, cursor)
            if not rows:
                break
            page_oldest: int | None = None
            for raw in rows:
                ts = _row_ts(raw)
                rate = _row_rate(raw)
                if ts is None or rate is None:
                    continue
                page_oldest = ts if page_oldest is None else min(page_oldest, ts)
                if start_ts <= ts <= end_ts:
                    collected[ts] = rate
            if page_oldest is None or page_oldest <= start_ts or len(rows) < PAGE_LIMIT:
                break
            cursor = page_oldest
            self._sleep(self.sleep_seconds)
        return [(ts, collected[ts]) for ts in sorted(collected)]

    def _fetch_page(self, inst_id: str, after_ts: int | None) -> list[Any]:
        params = {"instId": inst_id, "limit": str(PAGE_LIMIT)}
        if after_ts is not None:
            params["after"] = str(int(after_ts))
        url = f"{self.base_url}{FUNDING_HISTORY_PATH}?{urllib.parse.urlencode(params)}"
        return _request_data(self._http_get, url, self.timeout, "funding")


class OkxPublicOpenInterestProvider:
    """Public, read-only OKX open-interest-history provider (per-instId). No keys.

    Paginates backward via the `end` cursor (= oldest ts seen), mirroring the OHLCV
    pager, bounded by ``max_pages``. Response rows are positional arrays
    ``[ts, oi, oiCcy, oiUsd]``; we carry ``oi`` (contracts). OI is optional context —
    on failure the caller proceeds without it (never required for a run).
    """

    name = "okx-public-oi"
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

    def fetch_open_interest(
        self, symbol: str, timeframe: str, start_ts: int, end_ts: int
    ) -> list[tuple[int, float]]:
        """Return [(ts_ms, oi_contracts)] within [start_ts, end_ts] at the tf period, sorted."""
        start_ts, end_ts = int(start_ts), int(end_ts)
        if end_ts < start_ts:
            raise ValueError("end_ts before start_ts")
        inst_id = to_inst_id(symbol)
        period = oi_period_for_timeframe(timeframe)
        collected: dict[int, float] = {}
        cursor: int | None = None  # 'end' cursor = oldest ts seen -> walks backward
        for _page in range(self.max_pages):
            rows = self._fetch_page(inst_id, period, cursor)
            if not rows:
                break
            page_oldest: int | None = None
            for raw in rows:
                ts = _oi_ts(raw)
                oi = _oi_value(raw)
                if ts is None or oi is None:
                    continue
                page_oldest = ts if page_oldest is None else min(page_oldest, ts)
                if start_ts <= ts <= end_ts:
                    collected[ts] = oi
            if page_oldest is None or page_oldest <= start_ts or len(rows) < PAGE_LIMIT:
                break
            cursor = page_oldest
            self._sleep(self.sleep_seconds)
        return [(ts, collected[ts]) for ts in sorted(collected)]

    def _fetch_page(self, inst_id: str, period: str, end_ts: int | None) -> list[Any]:
        params = {"instId": inst_id, "period": period, "limit": str(PAGE_LIMIT)}
        if end_ts is not None:
            params["end"] = str(int(end_ts))
        url = f"{self.base_url}{OI_HISTORY_PATH}?{urllib.parse.urlencode(params)}"
        return _request_data(self._http_get, url, self.timeout, "open-interest")


def _row_ts(raw: Any) -> int | None:
    try:
        return int(raw["fundingTime"])
    except (KeyError, TypeError, ValueError):
        return None


def _row_rate(raw: Any) -> float | None:
    try:
        return float(raw["fundingRate"])
    except (KeyError, TypeError, ValueError):
        return None


def _oi_ts(raw: Any) -> int | None:
    """OI rows are positional arrays [ts, oi, oiCcy, oiUsd]."""
    try:
        return int(raw[0])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _oi_value(raw: Any) -> float | None:
    try:
        value = float(raw[1])
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    return value if value > 0 else None
