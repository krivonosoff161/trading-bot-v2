# -*- coding: utf-8 -*-
"""Market-data provider boundary for the 1m loader — offline-safe by default.

The default provider is `NullMarketDataProvider`: it never touches the network and
always reports `provider_not_configured`. A real exchange adapter is a future step
and must be **market-data only** (no order or private-account endpoints, no secrets
printed, no broad symbol discovery) and disabled unless explicitly configured.

`SyntheticMarketDataProvider` generates deterministic OFFLINE candles for pipeline
testing/demos only — it is NOT real market data and is gated behind an explicit
opt-in (`allow_synthetic`). All timestamps are UTC ms; rows use the canonical
OHLCV format (ts/date/open/high/low/close/vol).
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Protocol, runtime_checkable

MINUTE_MS = 60_000
_SYNTHETIC_MAX_BARS = 50_000  # defensive cap so even misuse cannot generate unbounded data


class ProviderNotConfigured(RuntimeError):
    """Raised when a non-configured provider is asked to fetch."""


@runtime_checkable
class MarketDataProvider(Protocol):
    """Market-data only. Implementations must never expose orders/accounts/secrets."""

    name: str
    configured: bool

    def fetch_ohlcv(self, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]: ...


class NullMarketDataProvider:
    """Default provider: no network, never configured."""

    name = "null"
    configured = False

    def fetch_ohlcv(self, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        raise ProviderNotConfigured("provider not configured")


class SyntheticMarketDataProvider:
    """Deterministic OFFLINE candles for testing/demos only. NOT real market data."""

    name = "synthetic"
    configured = True

    _TF_INTERVAL: dict[str, int] = {
        "1m": MINUTE_MS,
        "15m": 15 * MINUTE_MS,
        "1h": 60 * MINUTE_MS,
        "4h": 4 * 60 * MINUTE_MS,
        "1d": 24 * 60 * 60_000,
    }

    def fetch_ohlcv(self, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        tf = str(timeframe).strip().lower()
        if tf not in self._TF_INTERVAL:
            raise ValueError(f"synthetic provider supports 1m/15m/1h/4h/1d, got {timeframe!r}")
        interval = self._TF_INTERVAL[tf]
        start = int(start_ts) - (int(start_ts) % interval)
        end = int(end_ts)
        n = (end - start) // interval + 1
        if n <= 0:
            return []
        if n > _SYNTHETIC_MAX_BARS:
            raise ValueError(f"synthetic window {n} bars exceeds cap {_SYNTHETIC_MAX_BARS}")
        rows: list[dict[str, Any]] = []
        for i in range(n):
            ts = start + i * interval
            base = 100.0 + 5.0 * math.sin(i / 20.0)
            close = base + 0.5 * math.sin(i / 7.0)
            high = max(base, close) + 0.3
            low = min(base, close) - 0.3
            rows.append({
                "ts": ts,
                "date": dt.datetime.fromtimestamp(ts / 1000, tz=dt.timezone.utc).isoformat(),
                "open": round(base, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "vol": 1000.0,
                "synthetic": True,
            })
        return rows


def get_provider(name: str | None, *, allow_synthetic: bool = False) -> MarketDataProvider:
    """Resolve a provider by name. Unknown/unconfigured names fall back to Null.

    'okx-public' is a real, read-only public-candles provider (no key, no network
    until fetch). 'synthetic' is only returned when allow_synthetic is True (explicit
    opt-in). Everything else (incl. bare 'okx', unknown) falls back to Null.
    """
    token = (name or "null").strip().lower()
    if token == "synthetic" and allow_synthetic:
        return SyntheticMarketDataProvider()
    if token in ("okx-public", "okx_public", "okxpublic"):
        from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
        return OkxPublicMarketDataProvider()
    return NullMarketDataProvider()
