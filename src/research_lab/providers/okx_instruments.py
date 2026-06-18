# -*- coding: utf-8 -*-
"""OKX public instruments provider — read-only, keyless, market-data only.

Talks ONLY to the public instruments endpoint (`/api/v5/public/instruments`). No API
key, no account/order endpoints. Returns the raw instrument descriptors so the
discovery layer can filter to live USDT-margined linear perps and classify them. One
request per instType (the endpoint returns the full list); no pagination needed.
Network/parse failures raise InstrumentsError; the caller treats that as "no
discovery this cycle" and keeps using the manual universe.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

OKX_BASE_URL = "https://www.okx.com"
INSTRUMENTS_PATH = "/api/v5/public/instruments"
DEFAULT_TIMEOUT = 10.0
_USER_AGENT = "strategy-lab-research/1.0 (+public-market-data)"

HttpGet = Callable[[str, float], Any]


class InstrumentsError(RuntimeError):
    """Raised on network/HTTP/parse failure or an OKX API error code."""


def _default_http_get(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - fixed public OKX host
        return json.loads(resp.read().decode("utf-8"))


class OkxPublicInstrumentsProvider:
    """Public, read-only OKX instruments provider. No keys."""

    name = "okx-public-instruments"
    configured = True

    def __init__(
        self,
        *,
        base_url: str = OKX_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        http_get: HttpGet | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._http_get = http_get or _default_http_get

    def fetch_instruments(self, inst_type: str = "SWAP") -> list[dict[str, Any]]:
        """Return the raw instrument descriptors for an instType (e.g. SWAP / SPOT)."""
        query = urllib.parse.urlencode({"instType": inst_type})
        url = f"{self.base_url}{INSTRUMENTS_PATH}?{query}"
        try:
            payload = self._http_get(url, self.timeout)
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            raise InstrumentsError(f"okx instruments request failed: {type(exc).__name__}") from exc
        except json.JSONDecodeError as exc:
            raise InstrumentsError("okx instruments returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise InstrumentsError("okx instruments returned an unexpected payload")
        code = str(payload.get("code", "0"))
        if code not in ("0", ""):
            raise InstrumentsError(f"okx instruments API error code={code}")
        data = payload.get("data")
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
