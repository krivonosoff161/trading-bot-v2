# -*- coding: utf-8 -*-
"""
btc_eth_tactical.py - conservative L1 tactical source for BTC/ETH.

This is not a news feed. It reads public OKX market data and emits a small
number of pre-routed tactical events when positioning or liquidation conditions
become abnormal enough to matter:
  - liquidation_regime: strong move plus open-interest flush
  - funding_squeeze: extreme funding plus open-interest build

The source is intentionally narrow. If conditions are not stacked, it stays
silent.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import requests

from src.scout.router import tracked_assets

BASE_URL = "https://www.okx.com"
UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-btc-eth-tactical; keyless)"}
TIMEOUT = 15

PRICE_MOVE_TRIGGER_PCT = 1.25
OI_FLUSH_TRIGGER_PCT = -3.0
OI_BUILD_TRIGGER_PCT = 3.0
FUNDING_EXTREME = 0.00025  # 0.025%


@dataclass(frozen=True)
class AssetRef:
    sym: str
    okx_inst: str
    baseline: str | None


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tracked_majors() -> list[AssetRef]:
    out: list[AssetRef] = []
    for row in tracked_assets(layer=1):
        sym = str(row.get("sym") or "").upper()
        inst = str(row.get("okx_inst") or "")
        if sym not in {"BTC", "ETH"} or not inst:
            continue
        out.append(AssetRef(sym=sym, okx_inst=inst, baseline=row.get("baseline")))
    return out


def _get_json(path: str, params: dict) -> dict:
    resp = requests.get(f"{BASE_URL}{path}", params=params, headers=UA, timeout=TIMEOUT)
    return resp.json() or {}


def _ticker(inst_id: str) -> dict | None:
    try:
        payload = _get_json("/api/v5/market/ticker", {"instId": inst_id})
    except Exception:
        return None
    if payload.get("code") != "0" or not payload.get("data"):
        return None
    row = payload["data"][0]
    last = _safe_float(row.get("last"))
    if last is None or last <= 0:
        return None
    return row


def _funding(inst_id: str) -> float | None:
    try:
        payload = _get_json("/api/v5/public/funding-rate", {"instId": inst_id})
    except Exception:
        return None
    if payload.get("code") != "0" or not payload.get("data"):
        return None
    return _safe_float(payload["data"][0].get("fundingRate"))


def _oi_delta_1h(inst_id: str) -> float | None:
    try:
        payload = _get_json(
            "/api/v5/rubik/stat/contracts/open-interest-history",
            {"instId": inst_id, "period": "1H", "limit": "2"},
        )
    except Exception:
        return None
    rows = payload.get("data") or []
    if len(rows) < 2:
        return None
    current = _oi_value(rows[0])
    prev = _oi_value(rows[1])
    if current is None or prev is None or prev <= 0:
        return None
    return (current - prev) / prev


def _last_closed_1h(inst_id: str) -> float | None:
    try:
        payload = _get_json("/api/v5/market/history-candles", {"instId": inst_id, "bar": "1H", "limit": "1"})
    except Exception:
        return None
    rows = payload.get("data") or []
    if not rows:
        return None
    return _safe_float(rows[0][4] if len(rows[0]) > 4 else None)


def _oi_value(row) -> float | None:
    if isinstance(row, dict):
        return _safe_float(row.get("oi"))
    if isinstance(row, (list, tuple)):
        if len(row) > 1:
            return _safe_float(row[1])
        if row:
            return _safe_float(row[0])
    return None


def _hour_bucket(ts: dt.datetime) -> str:
    return ts.replace(minute=0, second=0, microsecond=0).strftime("%Y%m%dT%H00Z")


def _build_liquidation_event(
    *,
    asset: AssetRef,
    observed_at: dt.datetime,
    last_price: float,
    prev_close: float,
    funding_rate: float | None,
    oi_delta: float,
    vol_ccy_24h: float | None,
) -> dict:
    price_move_pct = (last_price - prev_close) / prev_close * 100.0
    if price_move_pct > 0:
        setup = "short-side pressure / squeeze risk"
        bias = "long"
    else:
        setup = "long-side flush / breakdown risk"
        bias = "short"
    if funding_rate is not None:
        if price_move_pct > 0 and funding_rate > 0:
            setup = "up-move with long crowding still elevated"
            bias = "none"
        elif price_move_pct < 0 and funding_rate < 0:
            setup = "down-move with short crowding already elevated"
            bias = "none"

    title = (
        f"{asset.sym} tactical flush: {price_move_pct:+.2f}% vs 1h close "
        f"as OI changes {oi_delta * 100:+.1f}%"
    )
    text = (
        f"OKX tactical monitor sees {asset.sym} at ${last_price:,.2f} versus last closed 1H price "
        f"${prev_close:,.2f} ({price_move_pct:+.2f}%). Open interest changed {oi_delta * 100:+.1f}% over 1H, "
        f"which is consistent with a liquidation-style move. Funding is "
        f"{(funding_rate * 100):+.3f}% if available, suggesting {setup}."
    )
    if vol_ccy_24h is not None:
        text += f" 24h base volume {vol_ccy_24h:,.0f}."
    return {
        "title": title,
        "text": text,
        "url": f"https://www.okx.com/trade-swap/{asset.okx_inst.lower()}",
        "time": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "btc_eth_tactical",
        "source_class": "api",
        "lead_class": "LEADING",
        "asset": asset.sym,
        "okx_inst": asset.okx_inst,
        "layer": 1,
        "baseline": asset.baseline,
        "phase": "REALIZED",
        "event_type": "liquidation_regime",
        "trigger_type": "tactical_market_regime",
        "event_key": f"tactical:{asset.sym}:liquidation_regime:{_hour_bucket(observed_at)}",
        "last_price": last_price,
        "price_move_1h_pct": round(price_move_pct, 3),
        "oi_delta_1h_pct": round(oi_delta * 100.0, 3),
        "funding_rate": funding_rate,
        "volume_ccy_24h": vol_ccy_24h,
        "bias_hint": bias,
    }


def _build_funding_event(
    *,
    asset: AssetRef,
    observed_at: dt.datetime,
    last_price: float,
    prev_close: float,
    funding_rate: float,
    oi_delta: float,
    vol_ccy_24h: float | None,
) -> dict:
    price_move_pct = (last_price - prev_close) / prev_close * 100.0
    if funding_rate > 0:
        setup = "crowded longs paying up"
        bias = "short"
    else:
        setup = "crowded shorts paying up"
        bias = "long"

    title = (
        f"{asset.sym} positioning extreme: funding {(funding_rate * 100):+.3f}% "
        f"with OI {oi_delta * 100:+.1f}%"
    )
    text = (
        f"OKX tactical monitor sees {asset.sym} positioning stretch. Funding is {(funding_rate * 100):+.3f}% "
        f"and open interest changed {oi_delta * 100:+.1f}% over 1H while price is ${last_price:,.2f} "
        f"({price_move_pct:+.2f}% versus the last closed 1H candle at ${prev_close:,.2f}). "
        f"This usually means {setup}; watch for squeeze or unwind."
    )
    if vol_ccy_24h is not None:
        text += f" 24h base volume {vol_ccy_24h:,.0f}."
    return {
        "title": title,
        "text": text,
        "url": f"https://www.okx.com/trade-swap/{asset.okx_inst.lower()}",
        "time": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "btc_eth_tactical",
        "source_class": "api",
        "lead_class": "LEADING",
        "asset": asset.sym,
        "okx_inst": asset.okx_inst,
        "layer": 1,
        "baseline": asset.baseline,
        "phase": "REALIZED",
        "event_type": "funding_squeeze",
        "trigger_type": "tactical_market_regime",
        "event_key": f"tactical:{asset.sym}:funding_squeeze:{_hour_bucket(observed_at)}",
        "last_price": last_price,
        "price_move_1h_pct": round(price_move_pct, 3),
        "oi_delta_1h_pct": round(oi_delta * 100.0, 3),
        "funding_rate": funding_rate,
        "volume_ccy_24h": vol_ccy_24h,
        "bias_hint": bias,
    }


def fetch_btc_eth_tactical(limit: int = 4) -> list[dict]:
    """Return tactical BTC/ETH events from public OKX market data."""
    observed_at = dt.datetime.now(dt.timezone.utc)
    out: list[tuple[float, dict]] = []

    for asset in _tracked_majors():
        ticker = _ticker(asset.okx_inst)
        if not ticker:
            continue
        last_price = _safe_float(ticker.get("last"))
        prev_close = _last_closed_1h(asset.okx_inst)
        oi_delta = _oi_delta_1h(asset.okx_inst)
        funding_rate = _funding(asset.okx_inst)
        vol_ccy_24h = _safe_float(ticker.get("volCcy24h"))
        if last_price is None or prev_close is None or prev_close <= 0 or oi_delta is None:
            continue

        price_move_pct = (last_price - prev_close) / prev_close * 100.0
        candidate: tuple[float, dict] | None = None

        if abs(price_move_pct) >= PRICE_MOVE_TRIGGER_PCT and oi_delta * 100.0 <= OI_FLUSH_TRIGGER_PCT:
            row = _build_liquidation_event(
                asset=asset,
                observed_at=observed_at,
                last_price=last_price,
                prev_close=prev_close,
                funding_rate=funding_rate,
                oi_delta=oi_delta,
                vol_ccy_24h=vol_ccy_24h,
            )
            score = abs(price_move_pct) * 2.0 + abs(oi_delta * 100.0)
            candidate = (score, row)

        if funding_rate is not None and abs(funding_rate) >= FUNDING_EXTREME and oi_delta * 100.0 >= OI_BUILD_TRIGGER_PCT:
            row = _build_funding_event(
                asset=asset,
                observed_at=observed_at,
                last_price=last_price,
                prev_close=prev_close,
                funding_rate=funding_rate,
                oi_delta=oi_delta,
                vol_ccy_24h=vol_ccy_24h,
            )
            score = abs(funding_rate) * 10000.0 + abs(oi_delta * 100.0)
            if candidate is None or score > candidate[0]:
                candidate = (score, row)

        if candidate:
            out.append(candidate)

    rows = [row for _, row in sorted(out, key=lambda x: x[0], reverse=True)]
    return rows[:limit]
