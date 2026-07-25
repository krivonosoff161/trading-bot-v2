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
import atexit
import json
import multiprocessing as mp
import queue
import random
import threading
import time
import urllib.parse
from typing import Any, Callable

import httpx

OKX_BASE_URL = "https://www.okx.com"
HISTORY_CANDLES_PATH = "/api/v5/market/history-candles"
MINUTE_MS = 60_000
PAGE_LIMIT = 300  # OKX history-candles max per request
MAX_PAGES = 60  # hard stop: enough for bounded multi-year research windows
# Guard equals the pager's capacity so an accepted window is always fully fetchable
# (never silently truncated). The lab's requirement windows are far smaller (<=360).
MAX_WINDOW_BARS = MAX_PAGES * PAGE_LIMIT  # 18,000
DEFAULT_TIMEOUT = 10.0
DEFAULT_FETCH_DEADLINE_SECONDS = 120.0
DEFAULT_PROCESS_GRACE_SECONDS = 1.0
DEFAULT_SLEEP_SECONDS = 0.2
_USER_AGENT = "strategy-lab-research/1.0 (+public-market-data)"

# Timeframe -> OKX bar parameter + interval in milliseconds
_SUPPORTED_TIMEFRAMES: dict[str, tuple[str, int]] = {
    "1m": ("1m", MINUTE_MS),
    "5m": ("5m", 5 * MINUTE_MS),
    "15m": ("15m", 15 * MINUTE_MS),
    "1h": ("1H", 60 * MINUTE_MS),
    "4h": ("4H", 4 * 60 * MINUTE_MS),
    "1d": ("1Dutc", 24 * 60 * 60_000),
}
SUPPORTED_TIMEFRAMES = tuple(_SUPPORTED_TIMEFRAMES.keys())

# HTTP getter: (url, timeout) -> parsed JSON dict. Injected in tests (no real network).
HttpGet = Callable[[str, float], Any]


class MarketDataError(RuntimeError):
    """Raised on network/HTTP/parse failure or an OKX API error code."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "provider_error",
        transient: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = str(reason)
        self.transient = bool(transient)
        self.status_code = status_code


class _WorkerRequestError(RuntimeError):
    def __init__(self, kind: str, *, status_code: int | None = None) -> None:
        super().__init__(kind)
        self.kind = str(kind)
        self.status_code = status_code


def to_inst_id(symbol: str) -> str:
    """Map an internal symbol (BTC_USDT_SWAP) to an OKX instId (BTC-USDT-SWAP)."""
    return str(symbol).strip().upper().replace("_", "-")


def _default_http_get(
    url: str,
    timeout: float,
    *,
    worker: Callable[[str, float, Any], None] | None = None,
) -> Any:
    return _httpx_get_with_hard_deadline(
        url, timeout, worker=worker or _httpx_get_worker
    )


def _httpx_get_direct(url: str, timeout: float) -> Any:
    timeout = max(0.1, float(timeout))
    response = httpx.get(
        url,
        headers={"User-Agent": _USER_AGENT},
        timeout=httpx.Timeout(
            timeout, connect=timeout, read=timeout, write=timeout, pool=timeout
        ),
    )
    response.raise_for_status()
    return response.json()


def _httpx_get_worker(url: str, timeout: float, out_queue: Any) -> None:
    try:
        out_queue.put(("ok", _httpx_get_direct(url, timeout)))
    except Exception as exc:  # noqa: BLE001 - serialized back to parent process
        out_queue.put(("error", type(exc).__name__, str(exc)[:500]))


def _persistent_http_worker(request_queue: Any, response_queue: Any) -> None:
    """Own one reusable TLS client until the parent asks it to stop."""
    with httpx.Client(headers={"User-Agent": _USER_AGENT}) as client:
        while True:
            request = request_queue.get()
            if request is None:
                return
            request_id, url, timeout = request
            try:
                bounded = max(0.1, float(timeout))
                response = client.get(
                    url,
                    timeout=httpx.Timeout(
                        bounded,
                        connect=bounded,
                        read=bounded,
                        write=bounded,
                        pool=bounded,
                    ),
                )
                if response.status_code >= 400:
                    response_queue.put(
                        ("http_error", request_id, int(response.status_code))
                    )
                    continue
                response_queue.put(("ok", request_id, response.json()))
            except json.JSONDecodeError:
                response_queue.put(("invalid_json", request_id))
            except httpx.TimeoutException:
                response_queue.put(("timeout", request_id))
            except httpx.TransportError as exc:
                response_queue.put(("transport", request_id, type(exc).__name__))
            except Exception as exc:  # noqa: BLE001 - safe type only crosses process boundary
                response_queue.put(("error", request_id, type(exc).__name__))


class PersistentHttpGet:
    """Killable persistent Windows worker with one reusable ``httpx.Client``."""

    def __init__(self, *, grace_seconds: float = DEFAULT_PROCESS_GRACE_SECONDS) -> None:
        self.grace_seconds = max(0.1, float(grace_seconds))
        self._ctx = mp.get_context("spawn")
        self._request_queue: Any = None
        self._response_queue: Any = None
        self._process: Any = None
        self._next_id = 0
        self._lock = threading.Lock()
        atexit.register(self.close)

    def __call__(self, url: str, timeout: float) -> Any:
        bounded = max(0.1, float(timeout))
        with self._lock:
            self._ensure_worker()
            self._next_id += 1
            request_id = self._next_id
            self._request_queue.put((request_id, url, bounded))
            try:
                response = self._response_queue.get(
                    timeout=bounded + self.grace_seconds
                )
            except queue.Empty as exc:
                self._terminate_worker()
                raise TimeoutError("okx-public worker deadline exceeded") from exc
            status, returned_id, *payload = response
            if int(returned_id) != request_id:
                self._terminate_worker()
                raise RuntimeError("okx-public worker response identity mismatch")
            if status == "ok":
                return payload[0]
            if status == "http_error":
                raise _WorkerRequestError("http", status_code=int(payload[0]))
            raise _WorkerRequestError(str(status))

    def close(self) -> None:
        with self._lock:
            proc = self._process
            if proc is None:
                return
            if proc.is_alive():
                try:
                    self._request_queue.put_nowait(None)
                except Exception:  # noqa: BLE001 - shutdown is best effort
                    pass
                proc.join(0.5)
            self._terminate_worker()

    def _ensure_worker(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._terminate_worker()
        self._request_queue = self._ctx.Queue(maxsize=1)
        self._response_queue = self._ctx.Queue(maxsize=1)
        self._process = self._ctx.Process(
            target=_persistent_http_worker,
            args=(self._request_queue, self._response_queue),
            name="okx-public-http",
            daemon=True,
        )
        self._process.start()

    def _terminate_worker(self) -> None:
        proc = self._process
        if proc is not None and proc.is_alive():
            proc.terminate()
            proc.join(0.5)
            if proc.is_alive():
                proc.kill()
                proc.join(0.5)
        for channel in (self._request_queue, self._response_queue):
            if channel is not None:
                try:
                    channel.close()
                    channel.join_thread()
                except Exception:  # noqa: BLE001 - cleanup only
                    pass
        self._process = None
        self._request_queue = None
        self._response_queue = None


def _httpx_get_with_hard_deadline(
    url: str,
    timeout: float,
    *,
    worker: Callable[[str, float, Any], None] = _httpx_get_worker,
) -> Any:
    """Run one public HTTP fetch in a killable child process.

    Some Windows/TLS paths can stall below Python's socket timeout during the
    handshake. The paper farm is long-running, so a single public market-data
    call must be disposable instead of holding the main loop.
    """
    timeout = max(0.1, float(timeout))
    ctx = mp.get_context("spawn")
    out_queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=worker, args=(url, timeout, out_queue))
    proc.daemon = True
    proc.start()
    proc.join(timeout + DEFAULT_PROCESS_GRACE_SECONDS)
    if proc.is_alive():
        proc.terminate()
        proc.join(0.5)
        if proc.is_alive():
            proc.kill()
            proc.join(0.5)
        raise TimeoutError("okx-public process deadline exceeded")
    try:
        status, *payload = out_queue.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError(
            f"okx-public worker exited without payload code={proc.exitcode}"
        ) from exc
    if status == "ok":
        return payload[0]
    err_name = payload[0] if payload else "Error"
    raise RuntimeError(f"okx-public worker failed: {err_name}")


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
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 0.25,
        fetch_deadline_seconds: float = DEFAULT_FETCH_DEADLINE_SECONDS,
        http_get: HttpGet | None = None,
        sleep: Callable[[float], None] | None = None,
        jitter: Callable[[float, float], float] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.sleep_seconds = float(sleep_seconds)
        self.max_pages = max(1, int(max_pages))
        self.retry_attempts = max(1, int(retry_attempts))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.fetch_deadline_seconds = max(self.timeout, float(fetch_deadline_seconds))
        self._owned_http_get = PersistentHttpGet() if http_get is None else None
        resolved_http_get = http_get or self._owned_http_get
        if resolved_http_get is None:
            raise RuntimeError("public HTTP transport is unavailable")
        self._http_get: HttpGet = resolved_http_get
        self._sleep = sleep or time.sleep
        self._jitter = jitter or random.uniform

    def close(self) -> None:
        if self._owned_http_get is not None:
            self._owned_http_get.close()

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, start_ts: int, end_ts: int
    ) -> list[dict[str, Any]]:
        okx_bar, interval_ms = _resolve_timeframe(timeframe)
        start_ts, end_ts = int(start_ts), int(end_ts)
        if end_ts < start_ts:
            raise ValueError("end_ts before start_ts")
        requested_bars = (end_ts - start_ts) // interval_ms + 1
        fetch_capacity = min(MAX_WINDOW_BARS, self.max_pages * PAGE_LIMIT)
        if requested_bars > fetch_capacity:
            raise ValueError(
                f"window exceeds configured fetch capacity ({fetch_capacity} bars)",
            )

        inst_id = to_inst_id(symbol)
        collected: dict[int, dict[str, Any]] = {}
        cursor = (
            end_ts + interval_ms
        )  # 'after' returns candles strictly older than cursor
        fetch_started = time.monotonic()

        for _page in range(self.max_pages):
            if time.monotonic() - fetch_started >= self.fetch_deadline_seconds:
                raise MarketDataError(
                    "okx-public whole-fetch deadline exceeded",
                    reason="fetch_deadline",
                    transient=True,
                )
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
        query = urllib.parse.urlencode(
            {
                "instId": inst_id,
                "bar": bar,
                "after": str(int(after_ts)),
                "limit": str(PAGE_LIMIT),
            }
        )
        url = f"{self.base_url}{HISTORY_CANDLES_PATH}?{query}"
        last_error: MarketDataError | None = None
        for attempt in range(self.retry_attempts):
            try:
                payload = self._http_get(url, self.timeout)
                if not isinstance(payload, dict):
                    raise MarketDataError(
                        "okx-public returned an unexpected payload",
                        reason="unexpected_payload",
                    )
                code = str(payload.get("code", "0"))
                if code not in ("0", ""):
                    transient = code in {"50004", "50011", "50013", "50026"}
                    reason = "rate_limited" if code == "50011" else "api_error"
                    raise MarketDataError(
                        f"okx-public API error code={code}",
                        reason=reason,
                        transient=transient,
                    )
                break
            except (
                Exception
            ) as exc:  # classified below; safe detail never leaves provider
                last_error = _classify_request_error(exc)
                if not last_error.transient or attempt + 1 >= self.retry_attempts:
                    raise last_error from exc
                delay = self.retry_backoff_seconds * (2**attempt)
                self._sleep(delay + self._jitter(0.0, max(0.0, delay * 0.25)))
        else:  # pragma: no cover - loop either succeeds or raises
            raise last_error or MarketDataError("okx-public request failed")
        data = payload.get("data")
        return list(data) if isinstance(data, list) else []


def _classify_request_error(exc: Exception) -> MarketDataError:
    if isinstance(exc, MarketDataError):
        return exc
    status: int | None = None
    if isinstance(exc, _WorkerRequestError):
        status = exc.status_code
        if exc.kind == "invalid_json":
            return MarketDataError(
                "okx-public returned invalid JSON", reason="invalid_json"
            )
        if exc.kind == "timeout":
            return MarketDataError(
                "okx-public request timed out", reason="timeout", transient=True
            )
        if exc.kind == "transport":
            return MarketDataError(
                "okx-public transport failed", reason="tls_or_transport", transient=True
            )
    if isinstance(exc, httpx.HTTPStatusError):
        status = int(exc.response.status_code)
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return MarketDataError(
            "okx-public request timed out", reason="timeout", transient=True
        )
    if isinstance(exc, json.JSONDecodeError):
        return MarketDataError(
            "okx-public returned invalid JSON", reason="invalid_json"
        )
    if isinstance(exc, (httpx.TransportError, OSError)):
        return MarketDataError(
            "okx-public transport failed", reason="tls_or_transport", transient=True
        )
    if status == 429:
        return MarketDataError(
            "okx-public rate limited",
            reason="rate_limited",
            transient=True,
            status_code=status,
        )
    if status is not None and status >= 500:
        return MarketDataError(
            "okx-public server error",
            reason="http_5xx",
            transient=True,
            status_code=status,
        )
    if status is not None:
        return MarketDataError(
            "okx-public HTTP error",
            reason="http_4xx",
            transient=False,
            status_code=status,
        )
    return MarketDataError(
        f"okx-public request failed: {type(exc).__name__}",
        reason="worker_or_network_error",
        transient=isinstance(exc, RuntimeError),
    )


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
            "date": dt.datetime.fromtimestamp(
                ts / 1000, tz=dt.timezone.utc
            ).isoformat(),
            "open": float(raw[1]),
            "high": float(raw[2]),
            "low": float(raw[3]),
            "close": float(raw[4]),
            "vol": float(raw[5]),
        }
    except (IndexError, TypeError, ValueError):
        return None
