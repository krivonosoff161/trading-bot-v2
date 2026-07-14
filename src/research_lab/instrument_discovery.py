# -*- coding: utf-8 -*-
"""Classify discovered OKX instruments into bounded farm groups + snapshot/diff.

Filters the public instrument list to LIVE USDT-margined linear perps, classifies
each by a confident known-base-coin set (ambiguous ones become ``unknown`` rather
than being guessed into a real group), persists a snapshot under the private root,
diffs against the previous snapshot, and exposes a ``discovered_<group>`` Universe the
farm loop can grind — bounded by the same resource policy / symbol caps (never a
full-market sweep, never 1m). Pure classification; the provider does the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.candle_library import load_canonical_candles
from src.research_lab.universe import Universe

SCHEMA = "okx_instrument_discovery.v2"
GROUPS = ("crypto_major", "meme_or_high_beta", "tokenized_equity", "commodity", "crypto_alt", "unknown")

_MAJORS = {"BTC", "ETH", "SOL", "BNB", "XRP"}
_MEMES = {"DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI", "PENGU", "PUMP", "BRETT",
          "MOG", "POPCAT", "MEW", "TURBO", "NEIRO", "MOODENG"}
_EQUITY = {"NVDA", "AMD", "COIN", "MSTR", "PLTR", "AAPL", "TSLA", "MSFT", "GOOGL", "AMZN", "META"}
_COMMODITY = {"XAU", "XAG", "XPT", "XPD", "HG", "CL", "NG"}


def _norm(symbol: str) -> str:
    return str(symbol).replace("-", "_").replace("/", "_").upper()


def _base_coin(symbol: str) -> str | None:
    parts = _norm(symbol).split("_")
    return parts[0] if parts and parts[0] else None


def classify_symbol(symbol: str) -> str:
    """Confident group for a canonical symbol; ``unknown`` when ambiguous (never guessed)."""
    base = _base_coin(symbol)
    if not base:
        return "unknown"
    if base in _MAJORS:
        return "crypto_major"
    if base in _MEMES:
        return "meme_or_high_beta"
    if base in _EQUITY:
        return "tokenized_equity"
    if base in _COMMODITY:
        return "commodity"
    return "crypto_alt"


def _is_live_usdt_perp(raw: dict[str, Any]) -> bool:
    return (str(raw.get("instType")) == "SWAP"
            and str(raw.get("settleCcy")).upper() == "USDT"
            and str(raw.get("state")).lower() == "live")


def _is_usdt_perp(raw: dict[str, Any]) -> bool:
    return (str(raw.get("instType")) == "SWAP"
            and str(raw.get("settleCcy")).upper() == "USDT")


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_snapshot(raw_instruments: list[dict[str, Any]], *, generated_at: str, inst_type: str = "SWAP") -> dict[str, Any]:
    """Filter to live USDT perps + classify into bounded groups."""
    instruments: dict[str, dict[str, Any]] = {}
    catalog: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[str]] = {g: [] for g in GROUPS}
    for raw in raw_instruments:
        if _is_usdt_perp(raw):
            inst_id = str(raw.get("instId") or "")
            symbol = _norm(inst_id)
            if symbol:
                catalog[symbol] = {
                    "inst_id": inst_id,
                    "inst_type": str(raw.get("instType") or ""),
                    "state": str(raw.get("state") or ""),
                    "settle_ccy": str(raw.get("settleCcy") or ""),
                    "list_time_ms": _as_int(raw.get("listTime")),
                }
        if not _is_live_usdt_perp(raw):
            continue
        inst_id = str(raw.get("instId") or "")
        symbol = _norm(inst_id)
        if not symbol or symbol in instruments:
            continue
        group = classify_symbol(symbol)
        instruments[symbol] = {
            "group": group,
            **catalog[symbol],
        }
        groups[group].append(symbol)
    return {
        "schema": SCHEMA, "generated_at": generated_at, "inst_type": inst_type,
        "count": len(instruments),
        "catalog_count": len(catalog),
        "groups": {g: sorted(v) for g, v in groups.items()},
        "instruments": instruments,
        "catalog": catalog,
    }


def snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "discovery" / "okx_instruments_snapshot.json"


def save_snapshot(private_root: Path, snapshot: dict[str, Any]) -> Path:
    path = snapshot_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_snapshot(private_root: Path) -> dict[str, Any]:
    path = snapshot_path(private_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def is_fresh(snapshot: dict[str, Any], now_ms: int, ttl_seconds: int) -> bool:
    """True if the snapshot's generated_at is within ttl_seconds of now (ms)."""
    import datetime as dt
    stamp = str(snapshot.get("generated_at") or "")
    if not stamp:
        return False
    try:
        gen = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=dt.timezone.utc)
    return (now_ms - int(gen.timestamp() * 1000)) < ttl_seconds * 1000


def snapshot_age_seconds(snapshot: dict[str, Any], now_ms: int) -> int | None:
    """Age of the snapshot in seconds (None if no/invalid generated_at)."""
    import datetime as dt
    stamp = str(snapshot.get("generated_at") or "")
    if not stamp:
        return None
    try:
        gen = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=dt.timezone.utc)
    return int((now_ms - int(gen.timestamp() * 1000)) / 1000)


def diff_snapshots(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """new_instruments / delisted / group_changes between two snapshots."""
    old_inst = old.get("instruments") or {}
    new_inst = new.get("instruments") or {}
    new_symbols = sorted(set(new_inst) - set(old_inst))
    delisted = sorted(set(old_inst) - set(new_inst))
    group_changes = [
        {"symbol": s, "from": old_inst[s].get("group"), "to": new_inst[s].get("group")}
        for s in sorted(set(old_inst) & set(new_inst))
        if old_inst[s].get("group") != new_inst[s].get("group")
    ]
    return {"new_instruments": new_symbols, "delisted": delisted, "group_changes": group_changes}


def discovered_universe(snapshot: dict[str, Any]) -> Universe:
    """Build a Universe whose groups are ``discovered_<group>`` from the snapshot."""
    groups = {f"discovered_{g}": tuple(symbols)
              for g, symbols in (snapshot.get("groups") or {}).items() if symbols}
    return Universe(groups=groups, relations={})


def farm_readiness(symbols: list[str], private_root: Path, timeframe: str) -> dict[str, list[str]]:
    """Split discovered symbols into those with usable candle data and those missing it."""
    ready, missing = [], []
    for symbol in symbols:
        rows = load_canonical_candles(private_root, symbol, timeframe).rows
        (ready if rows else missing).append(symbol)
    return {"ready": ready, "missing": missing}
