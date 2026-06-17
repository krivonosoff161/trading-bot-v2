# -*- coding: utf-8 -*-
"""OKX public market-tape source for broad move detection.

This is not a news source. It reads public OKX SWAP tickers and a bounded set of
1H candles, then emits pre-routed ``market_mover`` items when price movement is
large enough to audit what the scanner/farm missed.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass

import requests

from src.scout.router import baseline_for_layer, classify_layer

BASE_URL = "https://www.okx.com"
UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-okx-market-tape; keyless)"}
TIMEOUT = 15

DEFAULT_CANDIDATE_LIMIT = 30

MAJOR_1H_TRIGGER_PCT = 3.0
MAJOR_24H_TRIGGER_PCT = 6.0
ALT_1H_TRIGGER_PCT = 7.5
ALT_24H_TRIGGER_PCT = 15.0
OTHER_1H_TRIGGER_PCT = 3.0
OTHER_24H_TRIGGER_PCT = 7.0


@dataclass(frozen=True)
class TickerMove:
    inst_id: str
    asset: str
    layer: int
    last: float
    open_24h: float | None
    move_24h_pct: float | None
    volume_quote_24h: float | None


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_json(path: str, params: dict | None = None) -> dict:
    resp = requests.get(f"{BASE_URL}{path}", params=params or {}, headers=UA, timeout=TIMEOUT)
    return resp.json() or {}


def _base_from_inst(inst_id: str) -> str:
    return str(inst_id or "").split("-")[0].upper()


def _ticker_moves(inst_type: str = "SWAP") -> list[TickerMove]:
    try:
        payload = _get_json("/api/v5/market/tickers", {"instType": inst_type})
    except Exception:
        return []
    if payload.get("code") != "0":
        return []

    out: list[TickerMove] = []
    for row in payload.get("data") or []:
        inst_id = str(row.get("instId") or "")
        if not inst_id.endswith("-USDT-SWAP"):
            continue
        asset = _base_from_inst(inst_id)
        last = _safe_float(row.get("last"))
        open_24h = _safe_float(row.get("open24h"))
        if last is None or last <= 0:
            continue
        move_24h = None
        if open_24h is not None and open_24h > 0:
            move_24h = (last - open_24h) / open_24h * 100.0
        out.append(
            TickerMove(
                inst_id=inst_id,
                asset=asset,
                layer=classify_layer(asset),
                last=last,
                open_24h=open_24h,
                move_24h_pct=move_24h,
                volume_quote_24h=_safe_float(row.get("volCcy24h") or row.get("volCcyQuote")),
            )
        )
    return out


def _candidate_score(row: TickerMove) -> float:
    move = abs(row.move_24h_pct or 0.0)
    vol = row.volume_quote_24h or 0.0
    return move * 1000.0 + min(vol / 1_000_000.0, 1000.0)


def _closed_hour_move_pct(inst_id: str, last: float) -> float | None:
    try:
        payload = _get_json(
            "/api/v5/market/history-candles",
            {"instId": inst_id, "bar": "1H", "limit": "2"},
        )
    except Exception:
        return None
    if payload.get("code") != "0":
        return None
    rows = payload.get("data") or []
    closes: list[float] = []
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) > 4:
            close = _safe_float(row[4])
            if close and close > 0:
                closes.append(close)
    if not closes:
        return None
    ref_close = closes[-1] if len(closes) > 1 else closes[0]
    return (last - ref_close) / ref_close * 100.0


def _thresholds(layer: int) -> tuple[float, float]:
    if layer == 1:
        return (
            _env_float("SCANNER_OKX_MAJOR_1H_PCT", MAJOR_1H_TRIGGER_PCT),
            _env_float("SCANNER_OKX_MAJOR_24H_PCT", MAJOR_24H_TRIGGER_PCT),
        )
    if layer == 2:
        return (
            _env_float("SCANNER_OKX_ALT_1H_PCT", ALT_1H_TRIGGER_PCT),
            _env_float("SCANNER_OKX_ALT_24H_PCT", ALT_24H_TRIGGER_PCT),
        )
    return (
        _env_float("SCANNER_OKX_OTHER_1H_PCT", OTHER_1H_TRIGGER_PCT),
        _env_float("SCANNER_OKX_OTHER_24H_PCT", OTHER_24H_TRIGGER_PCT),
    )


def _hour_bucket(ts: dt.datetime) -> str:
    return ts.replace(minute=0, second=0, microsecond=0).strftime("%Y%m%dT%H00Z")


def _event_from_move(row: TickerMove, observed_at: dt.datetime, move_1h_pct: float | None) -> dict | None:
    min_1h, min_24h = _thresholds(row.layer)
    abs_1h = abs(move_1h_pct or 0.0)
    abs_24h = abs(row.move_24h_pct or 0.0)
    trigger = None
    if move_1h_pct is not None and abs_1h >= min_1h:
        trigger = "1h_move"
    elif row.move_24h_pct is not None and abs_24h >= min_24h:
        trigger = "24h_move"
    if not trigger:
        return None

    direction = "up" if ((move_1h_pct if trigger == "1h_move" else row.move_24h_pct) or 0.0) > 0 else "down"
    observed_iso = observed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    title = (
        f"{row.asset} OKX market mover: "
        f"1h {move_1h_pct:+.2f}% / 24h {row.move_24h_pct:+.2f}%"
        if row.move_24h_pct is not None and move_1h_pct is not None
        else f"{row.asset} OKX market mover on {row.inst_id}"
    )
    text = (
        f"OKX public market tape observed {row.inst_id} moving {direction}. "
        f"Last price {row.last:g}. "
        f"Move versus recent 1H close: {move_1h_pct:+.2f}%." if move_1h_pct is not None
        else f"OKX public market tape observed {row.inst_id} moving {direction}. Last price {row.last:g}."
    )
    if row.move_24h_pct is not None:
        text += f" 24h move versus open24h: {row.move_24h_pct:+.2f}%."
    if row.volume_quote_24h is not None:
        text += f" 24h quote volume: {row.volume_quote_24h:,.0f}."
    text += " This is a realized market-tape trigger for missed-move audit, not standalone news."

    return {
        "title": title,
        "text": text,
        "url": f"https://www.okx.com/trade-swap/{row.inst_id.lower()}",
        "time": observed_iso,
        "source": "okx_market_tape",
        "source_class": "api",
        "lead_class": "COINCIDENT",
        "asset": row.asset,
        "okx_inst": row.inst_id,
        "layer": row.layer,
        "baseline": baseline_for_layer(row.layer),
        "phase": "REALIZED",
        "event_type": "market_mover",
        "trigger_type": "okx_market_tape",
        "event_key": f"okx_tape:{row.inst_id}:{trigger}:{_hour_bucket(observed_at)}",
        "asset_class": "crypto_major" if row.layer == 1 else "crypto_alt",
        "trigger_role": "context_trigger",
        "requires_context": False,
        "identity_reason": "okx_public_traded_instrument",
        "identity_confidence": 0.85,
        "move_1h_pct": round(move_1h_pct, 3) if move_1h_pct is not None else None,
        "move_24h_pct": round(row.move_24h_pct, 3) if row.move_24h_pct is not None else None,
        "volume_quote_24h": row.volume_quote_24h,
        "market_tape_trigger": trigger,
    }


def fetch_okx_market_tape(limit: int = 12, candidate_limit: int = DEFAULT_CANDIDATE_LIMIT) -> list[dict]:
    """Return broad OKX market-mover events from public market data."""
    observed_at = dt.datetime.now(dt.timezone.utc)
    rows = _ticker_moves("SWAP")
    if not rows:
        return []

    majors = [r for r in rows if r.layer == 1]
    ranked = sorted(rows, key=_candidate_score, reverse=True)
    candidates: dict[str, TickerMove] = {}
    for row in majors:
        candidates.setdefault(row.inst_id, row)
    for row in ranked[:candidate_limit]:
        candidates.setdefault(row.inst_id, row)

    events: list[tuple[float, dict]] = []
    for row in candidates.values():
        move_1h = _closed_hour_move_pct(row.inst_id, row.last)
        event = _event_from_move(row, observed_at, move_1h)
        if not event:
            continue
        score = abs(event.get("move_1h_pct") or 0.0) * 2.0 + abs(event.get("move_24h_pct") or 0.0)
        events.append((score, event))
        if len(events) >= limit:
            break

    return [event for _, event in sorted(events, key=lambda x: x[0], reverse=True)[:limit]]
