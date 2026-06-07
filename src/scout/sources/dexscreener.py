# -*- coding: utf-8 -*-
"""
dexscreener.py - realized L2 source from DexScreener public API.

Goal: feed alt/meme layer with native DEX events instead of lagging news.
This module emits pre-routed scanner items, not articles:
  - launch: fresh pair with meaningful liquidity
  - dex_momentum: strong 24h move + real liquidity/volume

The source is COINCIDENT by design. It improves visibility and watchability
for L2 without pretending to be an early official trigger.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import requests

from src.scout.router import tracked_assets

UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-dexscreener; keyless)"}
SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
TIMEOUT = 15

MIN_LIQUIDITY_USD = 150_000.0
MIN_MOMENTUM_VOLUME_24H_USD = 250_000.0
MIN_ABS_CHANGE_24H_PCT = 2.0
MIN_TURNOVER_TO_LIQUIDITY = 1.0
NEW_PAIR_MAX_AGE_HOURS = 72.0
MIN_LAUNCH_VOLUME_24H_USD = 100_000.0


@dataclass(frozen=True)
class AssetRef:
    sym: str
    okx_inst: str | None
    baseline: str | None
    aliases: tuple[str, ...]


def _safe_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _normalize(s: str | None) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _tracked_l2_assets() -> list[AssetRef]:
    out: list[AssetRef] = []
    for row in tracked_assets(layer=2):
        aliases = [row.get("sym")]
        aliases.extend(row.get("strong") or [])
        aliases.extend(row.get("weak") or [])
        uniq = tuple(dict.fromkeys(_normalize(x) for x in aliases if _normalize(x)))
        out.append(
            AssetRef(
                sym=str(row.get("sym") or "").upper(),
                okx_inst=row.get("okx_inst"),
                baseline=row.get("baseline"),
                aliases=uniq,
            )
        )
    return out


def _match_asset(row: dict, asset: AssetRef) -> bool:
    base = row.get("baseToken") or {}
    base_tokens = {
        _normalize(base.get("symbol")),
        _normalize(base.get("name")),
    }
    base_tokens.discard("")
    return bool(base_tokens.intersection(asset.aliases))


def _pair_age_hours(created_ms: int | None, observed_at: dt.datetime) -> float | None:
    if not created_ms:
        return None
    try:
        created = dt.datetime.fromtimestamp(int(created_ms) / 1000, tz=dt.timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return max((observed_at - created).total_seconds() / 3600.0, 0.0)


def _signal_from_pair(asset: AssetRef, row: dict, observed_at: dt.datetime) -> dict | None:
    liquidity = _safe_float((row.get("liquidity") or {}).get("usd"))
    volume_24h = _safe_float((row.get("volume") or {}).get("h24"))
    change_24h = _safe_float((row.get("priceChange") or {}).get("h24"))
    if liquidity is None or volume_24h is None:
        return None
    if liquidity < MIN_LIQUIDITY_USD:
        return None

    pair = row.get("pairAddress") or ""
    pair_url = row.get("url") or ""
    chain = row.get("chainId") or "dex"
    dex = row.get("dexId") or "dex"
    quote = row.get("quoteToken") or {}
    quote_symbol = str(quote.get("symbol") or "").upper() or "?"
    price_usd = row.get("priceUsd")
    created_ms = row.get("pairCreatedAt")
    age_hours = _pair_age_hours(created_ms, observed_at)

    event_type = None
    if (
        age_hours is not None
        and age_hours <= NEW_PAIR_MAX_AGE_HOURS
        and volume_24h >= MIN_LAUNCH_VOLUME_24H_USD
    ):
        event_type = "launch"
    elif (
        volume_24h >= MIN_MOMENTUM_VOLUME_24H_USD
        and abs(change_24h or 0.0) >= MIN_ABS_CHANGE_24H_PCT
        and (volume_24h / liquidity) >= MIN_TURNOVER_TO_LIQUIDITY
    ):
        event_type = "dex_momentum"
    if not event_type:
        return None

    observed_iso = observed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    if event_type == "launch":
        title = (
            f"{asset.sym} DEX pair launch on {dex}: "
            f"${liquidity:,.0f} liquidity on {chain}"
        )
    else:
        title = (
            f"{asset.sym} DEX volume spike: {change_24h:+.1f}% / 24h "
            f"with ${volume_24h:,.0f} volume on {dex}"
        )

    text = (
        f"DexScreener observed {asset.sym} against {quote_symbol} on {chain}/{dex}. "
        f"Liquidity ${liquidity:,.0f}, 24h volume ${volume_24h:,.0f}, "
        f"24h change {change_24h if change_24h is not None else 'n/a'}%, "
        f"price ${price_usd or 'n/a'}."
    )
    if age_hours is not None:
        text += f" Pair age about {age_hours:.1f}h."

    return {
        "title": title,
        "text": text,
        "url": pair_url,
        "time": observed_iso,
        "source": "dexscreener",
        "source_class": "api",
        "lead_class": "COINCIDENT",
        "asset": asset.sym,
        "okx_inst": asset.okx_inst,
        "layer": 2,
        "baseline": asset.baseline,
        "phase": "REALIZED",
        "event_type": event_type,
        "trigger_type": "dexscreener_signal",
        "event_key": f"dex:{asset.sym}:{event_type}:{pair}",
        "pair_address": pair,
        "chain": chain,
        "dex": dex,
        "liquidity_usd": liquidity,
        "volume_24h_usd": volume_24h,
        "price_change_24h_pct": change_24h,
    }


def fetch_alt_flow_signals(limit: int = 8) -> list[dict]:
    """Return top L2 DexScreener events as pre-routed scanner items."""
    observed_at = dt.datetime.now(dt.timezone.utc)
    best_by_asset: dict[str, tuple[float, dict]] = {}

    for asset in _tracked_l2_assets():
        query = asset.sym
        try:
            resp = requests.get(SEARCH_URL, params={"q": query}, headers=UA, timeout=TIMEOUT)
            rows = (resp.json() or {}).get("pairs") or []
        except Exception:
            continue

        for row in rows:
            if not _match_asset(row, asset):
                continue
            signal = _signal_from_pair(asset, row, observed_at)
            if not signal:
                continue
            score = float(signal.get("volume_24h_usd") or 0.0)
            if signal.get("event_type") == "launch":
                score += 10_000_000.0
            current = best_by_asset.get(asset.sym)
            if current is None or score > current[0]:
                best_by_asset[asset.sym] = (score, signal)

    signals = [row for _, row in sorted(best_by_asset.values(), key=lambda x: x[0], reverse=True)]
    return signals[:limit]
