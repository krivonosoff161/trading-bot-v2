# -*- coding: utf-8 -*-
"""OKX instrument resolver — verify a ticker/symbol against OKX PUBLIC instruments.

Before the scanner trusts ``f"{sym}-USDT-SWAP"``, this module asks OKX what is
actually tradable. It fetches the public instruments universe (no keys, no auth,
no private/order endpoints), caches it with a TTL (+ a disk fallback so a network
blip does not blind the scanner), and resolves a query to a real ``instId``,
preferring ``SWAP`` then ``SPOT``.

Boundary: PUBLIC market data only (`/api/v5/public/instruments`), keyless, UA-only.
It NEVER imports the order client and NEVER touches private account or trade routes.

Structured result (resolved):
    {"query": "RE", "resolved": True, "primary_inst_id": "RE-USDT-SWAP",
     "inst_type": "SWAP", "asset_class": "crypto_alt", "candidates": [...],
     "reason": "exact_swap_match", "confidence": 0.95,
     "state": "live", "list_time_ms": 1700000000000}
Missing:
    {"query": "GEOD", "resolved": False, "primary_inst_id": None,
     "inst_type": None, "asset_class": "crypto_alt", "candidates": [],
     "reason": "no_okx_instrument", "confidence": 0.0}
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from src.scout.router import classify_layer

BASE_URL = "https://www.okx.com"
INSTRUMENTS_PATH = "/api/v5/public/instruments"
UA = "Mozilla/5.0 (trading-bot-v2 scanner-instrument-resolver; keyless)"
TIMEOUT = 15.0
TTL_S = 3600.0
SUPPORTED_INST_TYPES = ("SWAP", "FUTURES", "SPOT")
_ROOT = Path(__file__).resolve().parents[2]

# OKX layer (router) -> OKX-verified asset class. Distinct from asset_identity's
# class so the verified classification can be compared/audited against identity.
_LAYER_ASSET_CLASS = {1: "crypto_major", 2: "crypto_alt", 3: "commodity", 4: "energy", 5: "equity"}

# HTTP getter: (url, timeout) -> parsed JSON dict. Injected in tests (no network).
HttpGet = Callable[[str, float], Any]


def _hot_root() -> Path:
    raw = os.getenv("STRATEGY_LAB_HOT_ROOT", "").strip()
    root = Path(raw) if raw else (_ROOT / "logs" / "scout" / "cache")
    return root


def _default_http_get(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - fixed public OKX host
        return json.loads(resp.read().decode("utf-8"))


def base_ticker(query: str) -> str:
    """Bare base symbol from a query like 'RE', 'RE-USDT-SWAP', 'RE_USDT_SWAP', 'RE/USDT'."""
    text = str(query or "").strip().upper().replace("_", "-").replace("/", "-")
    return text.split("-")[0] if text else ""


def asset_class_for_base(base: str) -> str:
    return _LAYER_ASSET_CLASS.get(classify_layer(base), "crypto_alt")


class OkxInstrumentResolver:
    """Resolve a ticker to a real OKX public instrument. Keyless, cached, public-only."""

    def __init__(
        self,
        *,
        http_get: HttpGet | None = None,
        timeout: float = TIMEOUT,
        base_url: str = BASE_URL,
        ttl_s: float = TTL_S,
        hot_root: Path | None = None,
    ) -> None:
        self._http_get = http_get or _default_http_get
        self.timeout = float(timeout)
        self.base_url = base_url.rstrip("/")
        self.ttl_s = float(ttl_s)
        self._hot_root = hot_root  # None => env/default, resolved lazily
        self._cache: dict[str, dict[str, Any]] = {}  # inst_type -> {ts, rows_by_base}

    # ── instruments universe (cached + disk fallback) ────────────────────────
    def _cache_path(self, inst_type: str) -> Path:
        root = self._hot_root or _hot_root()
        return root / f"okx_instruments_{inst_type.lower()}.json"

    def _fetch_instruments(self, inst_type: str) -> list[dict]:
        query = urllib.parse.urlencode({"instType": inst_type})
        url = f"{self.base_url}{INSTRUMENTS_PATH}?{query}"
        payload = self._http_get(url, self.timeout)
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise RuntimeError(f"okx instruments error code={(payload or {}).get('code')}")
        return [r for r in (payload.get("data") or []) if isinstance(r, dict)]

    def _instruments(self, inst_type: str) -> dict[str, dict]:
        """{instId: row} for one instType, TTL-cached, disk-fallback on network error.

        Keyed by full instId (not bare base) so resolution matches the exact
        USDT-linear instrument the rest of the pipeline uses, never a coin-margined
        ``BTC-USD-SWAP``. Raises if the network fails AND there is no disk fallback,
        so the caller reports provider_error rather than a false "missing symbol".
        """
        entry = self._cache.get(inst_type)
        if entry and (time.monotonic() - entry["ts"]) < self.ttl_s:
            return entry["by_inst"]
        try:
            rows: list[dict] | None = self._fetch_instruments(inst_type)
            if rows:                       # never poison the disk snapshot with an empty fetch
                self._write_disk(inst_type, rows)
            elif not rows:
                rows = self._read_disk(inst_type) or rows  # empty fetch -> prefer last good snapshot
        except Exception:
            rows = self._read_disk(inst_type)  # stale-but-better-than-blind; None if absent
        if rows is None:
            raise RuntimeError(f"okx instruments unavailable for {inst_type} (no network, no cache)")
        by_inst = {str(r.get("instId") or "").upper(): r for r in rows if r.get("instId")}
        self._cache[inst_type] = {"ts": time.monotonic(), "by_inst": by_inst}
        return by_inst

    def _write_disk(self, inst_type: str, rows: list[dict]) -> None:
        try:
            path = self._cache_path(inst_type)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _read_disk(self, inst_type: str) -> list[dict] | None:
        try:
            data = json.loads(self._cache_path(inst_type).read_text(encoding="utf-8"))
            return data if isinstance(data, list) else None
        except (OSError, json.JSONDecodeError):
            return None

    # ── resolution ───────────────────────────────────────────────────────────
    def resolve(self, query: str, *, prefer: tuple[str, ...] = ("SWAP", "SPOT")) -> dict:
        base = base_ticker(query)
        result = {
            "query": str(query or "").strip(),
            "resolved": False,
            "primary_inst_id": None,
            "inst_type": None,
            "asset_class": asset_class_for_base(base) if base else "unknown",
            "candidates": [],
            "reason": "empty_query" if not base else "no_okx_instrument",
            "confidence": 0.0,
            "state": None,
            "list_time_ms": None,
        }
        if not base:
            return result

        candidates: list[dict] = []
        provider_errored = False
        saw_universe = False
        for inst_type in prefer:
            if inst_type not in SUPPORTED_INST_TYPES:
                continue
            try:
                by_inst = self._instruments(inst_type)
            except Exception:
                provider_errored = True
                continue
            if by_inst:
                saw_universe = True       # at least one authoritative, non-empty universe
            row = _match_usdt_linear(by_inst, base, inst_type)
            if not row:
                continue
            inst_id = str(row.get("instId") or "")
            candidates.append({"inst_id": inst_id, "inst_type": inst_type, "state": row.get("state")})
            if result["primary_inst_id"] is None:
                result.update(
                    resolved=True,
                    primary_inst_id=inst_id,
                    inst_type=inst_type,
                    reason="exact_swap_match" if inst_type == "SWAP" else f"exact_{inst_type.lower()}_match",
                    confidence=0.95 if inst_type == "SWAP" else 0.85,
                    state=row.get("state"),
                    list_time_ms=_as_int(row.get("listTime")),
                )
        result["candidates"] = candidates
        # Only call it "no instrument" when we actually saw an authoritative universe;
        # otherwise (network error or empty everywhere) it is provider_error, not absence.
        if not result["resolved"] and not candidates and (provider_errored or not saw_universe):
            result["reason"] = "provider_error"
        return result


def _match_usdt_linear(by_inst: dict[str, dict], base: str, inst_type: str) -> dict | None:
    """Exact USDT-linear instrument for a base (the form the whole pipeline uses).

    SWAP -> ``{base}-USDT-SWAP`` (NOT the coin-margined ``{base}-USD-SWAP``);
    SPOT -> ``{base}-USDT``; FUTURES -> nearest dated ``{base}-USDT-<expiry>``.
    """
    if inst_type == "SWAP":
        return by_inst.get(f"{base}-USDT-SWAP")
    if inst_type == "SPOT":
        return by_inst.get(f"{base}-USDT")
    if inst_type == "FUTURES":
        dated = sorted(iid for iid in by_inst if iid.startswith(f"{base}-USDT-"))
        return by_inst.get(dated[0]) if dated else None
    return None


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── module-level convenience (shared cache; monkeypatchable from scanner) ─────
_DEFAULT_RESOLVER: OkxInstrumentResolver | None = None


def _shared() -> OkxInstrumentResolver:
    global _DEFAULT_RESOLVER
    if _DEFAULT_RESOLVER is None:
        _DEFAULT_RESOLVER = OkxInstrumentResolver()
    return _DEFAULT_RESOLVER


def resolve_instrument(query: str, *, http_get: HttpGet | None = None,
                       prefer: tuple[str, ...] = ("SWAP", "SPOT")) -> dict:
    """Resolve one query. Pass http_get to use a one-off resolver (tests/no network)."""
    resolver = OkxInstrumentResolver(http_get=http_get) if http_get is not None else _shared()
    return resolver.resolve(query, prefer=prefer)


def reset_cache() -> None:
    global _DEFAULT_RESOLVER
    _DEFAULT_RESOLVER = None
