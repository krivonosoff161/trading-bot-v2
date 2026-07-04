# -*- coding: utf-8 -*-
"""Microstructure features — pure, no-look-ahead math for the Theme-40 lane (research-only).

Two groups:
  * tape features (computable on the real historical tick tape NOW): trade delta / CVD, aggressive
    buy-vs-sell pressure, tape speed. Input is a list of executed trades AT OR BEFORE the event time —
    the caller must never pass future trades (the replay slices to [t-window, t]).
  * orderbook features (for the FORWARD recorder; no historical book data exists yet): top-N imbalance,
    spread, and liquidity-wall descriptors (notional, distance from mid, and — over a SEQUENCE of book
    snapshots — persistence / movement / spoof-cancel).

Ported from the live engine's _build_micro_snapshot as pure functions (no live client import, no I/O,
no money path). Nothing here is a signal or edge; it only describes the book/tape.
"""
from __future__ import annotations

from typing import Any, Sequence

Level = Sequence[Any]   # [price, size, ...] as OKX returns
Trade = dict[str, Any]  # {ts_ms|ts, side, price|px, size|sz}


def _levels(book: dict[str, Any] | None, key: str) -> list[list[float]]:
    out: list[list[float]] = []
    for lvl in (book or {}).get(key, []) or []:
        try:
            out.append([float(lvl[0]), float(lvl[1])])
        except (TypeError, ValueError, IndexError):
            continue
    return out


def mid_price(book: dict[str, Any] | None) -> float:
    bids, asks = _levels(book, "bids"), _levels(book, "asks")
    if not bids or not asks:
        return 0.0
    return (bids[0][0] + asks[0][0]) / 2.0


def spread_bps(book: dict[str, Any] | None) -> float:
    bids, asks = _levels(book, "bids"), _levels(book, "asks")
    if not bids or not asks:
        return 0.0
    mid = (bids[0][0] + asks[0][0]) / 2.0
    return round((asks[0][0] - bids[0][0]) / mid * 10000, 3) if mid > 0 else 0.0


def orderbook_imbalance(book: dict[str, Any] | None, *, depth: int = 5) -> float:
    """(bid_size - ask_size) / (bid_size + ask_size) over the top `depth` levels. In [-1, 1]."""
    bids, asks = _levels(book, "bids"), _levels(book, "asks")
    bid_sum = sum(s for _, s in bids[:depth])
    ask_sum = sum(s for _, s in asks[:depth])
    denom = bid_sum + ask_sum
    return round((bid_sum - ask_sum) / denom, 4) if denom > 0 else 0.0


def liquidity_wall(book: dict[str, Any] | None, side: str, *, depth: int = 25) -> dict[str, Any]:
    """Largest resting level (the 'wall') on one side: its notional, price, and distance from mid in bps.
    side = 'bid' or 'ask'. No look-ahead (a single snapshot)."""
    levels = _levels(book, "bids" if side == "bid" else "asks")[:depth]
    mid = mid_price(book)
    if not levels or mid <= 0:
        return {"present": False}
    px, sz = max(levels, key=lambda lvl: lvl[1])
    dist_bps = abs(px - mid) / mid * 10000
    return {"present": True, "price": px, "size": sz, "notional": round(px * sz, 2),
            "distance_bps": round(dist_bps, 2), "side": side}


def wall_sequence_features(walls: Sequence[dict[str, Any]], *, move_tol_bps: float = 2.0) -> dict[str, Any]:
    """Over a time-ordered SEQUENCE of same-side wall snapshots: persistence (how many consecutive
    snapshots a wall of similar price persisted), net movement direction, and a spoof-cancel flag
    (a wall that was present then vanished). For the forward recorder; pure, no look-ahead within the seq."""
    present = [w for w in walls if w.get("present")]
    if not present:
        return {"persistence": 0, "movement": "none", "spoof_cancel": False}
    persistence = 0
    last_px = None
    for w in walls:
        if w.get("present") and (last_px is None or abs(w["price"] - last_px) / max(1e-9, last_px) * 10000 <= move_tol_bps):
            persistence += 1
            last_px = w["price"]
        else:
            persistence = 1 if w.get("present") else 0
            last_px = w["price"] if w.get("present") else last_px
    first_px, end_px = present[0]["price"], present[-1]["price"]
    drift = (end_px - first_px) / first_px * 10000 if first_px else 0.0
    movement = "up" if drift > move_tol_bps else "down" if drift < -move_tol_bps else "stable"
    spoof = bool(walls[-1].get("present") is False and any(w.get("present") for w in walls[:-1]))
    return {"persistence": persistence, "movement": movement, "drift_bps": round(drift, 2),
            "spoof_cancel": spoof}


# ── tape features (real data available now) ──────────────────────────────────────────────
def _trade_fields(t: Trade) -> tuple[int, str, float]:
    ts = int(t.get("ts_ms") or t.get("ts") or 0)
    side = str(t.get("side") or "").lower()
    size = float(t.get("size") or t.get("sz") or 0.0)
    return ts, side, size


def tape_delta(trades: Sequence[Trade]) -> dict[str, Any]:
    """Signed buy-vs-sell pressure (CVD) over the given trades. Caller passes only trades up to t."""
    buy = sum(sz for _, side, sz in map(_trade_fields, trades) if side == "buy")
    sell = sum(sz for _, side, sz in map(_trade_fields, trades) if side == "sell")
    denom = buy + sell
    return {"buy_vol": round(buy, 6), "sell_vol": round(sell, 6),
            "cvd_ratio": round((buy - sell) / denom, 4) if denom > 0 else 0.0,
            "n_trades": sum(1 for t in trades if _trade_fields(t)[1] in ("buy", "sell"))}


def tape_speed(trades: Sequence[Trade], *, window_ms: int) -> float:
    """Trades per second over the window (descriptive intensity). No look-ahead."""
    n = sum(1 for t in trades if _trade_fields(t)[1] in ("buy", "sell"))
    secs = max(1e-9, window_ms / 1000.0)
    return round(n / secs, 4)


def aggressive_pressure(trades: Sequence[Trade]) -> float:
    """(buy_vol - sell_vol) / (buy_vol + sell_vol) in [-1, 1]; the tape's directional aggression."""
    return tape_delta(trades)["cvd_ratio"]
