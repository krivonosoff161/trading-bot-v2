# -*- coding: utf-8 -*-
"""OKX public funding-rate-history provider — read-only, keyless, market-data only.

Talks ONLY to the public funding-rate-history endpoint
(`/api/v5/public/funding-rate-history`). No API key, no account/private/order
endpoints. Paginates backward (`after` = oldest fundingTime seen) with a bounded
page count and a short sleep between pages, returning (ts_ms, funding_rate) points
inside the requested window. Network/parse failures raise FlowDataError; callers
treat that as "no flow data" and proceed (flow is context, never required).

Note on OI: a clean keyless historical open-interest series per instId is not
reliably public, so this module ships funding only. ``oi`` enrichment is left as a
provider slot (see flow_merge.merge_oi) rather than faked.
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
PAGE_LIMIT = 100
MAX_PAGES = 12
DEFAULT_TIMEOUT = 10.0
DEFAULT_SLEEP_SECONDS = 0.2
_USER_AGENT = "strategy-lab-research/1.0 (+public-market-data)"

HttpGet = Callable[[str, float], Any]


class FlowDataError(RuntimeError):
    """Raised on network/HTTP/parse failure or an OKX API error code."""


def to_inst_id(symbol: str) -> str:
    return str(symbol).strip().upper().replace("_", "-")


def _default_http_get(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - fixed public OKX host
        return json.loads(resp.read().decode("utf-8"))


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
        try:
            payload = self._http_get(url, self.timeout)
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            raise FlowDataError(f"okx funding request failed: {type(exc).__name__}") from exc
        except json.JSONDecodeError as exc:
            raise FlowDataError("okx funding returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise FlowDataError("okx funding returned an unexpected payload")
        code = str(payload.get("code", "0"))
        if code not in ("0", ""):
            raise FlowDataError(f"okx funding API error code={code}")
        data = payload.get("data")
        return list(data) if isinstance(data, list) else []


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
