# -*- coding: utf-8 -*-

from src.research_lab.event_cluster import (
    attach_related,
    detect_move_events,
    pre_move_candles,
)
from src.research_lab.universe import Universe


def _candles(prices: list[float]) -> list[dict]:
    rows = []
    for i, price in enumerate(prices):
        rows.append(
            {
                "ts": 1_700_000_000_000 + i * 900_000,
                "open": price,
                "high": price * 1.001,
                "low": price * 0.999,
                "close": price,
                "vol": 1000.0,
            }
        )
    return rows


def _universe() -> Universe:
    return Universe(
        groups={"alts": ("SOL_USDT_SWAP", "WLD_USDT_SWAP"), "core": ("BTC_USDT_SWAP",)},
        relations={"market_beta": {"SOL_USDT_SWAP": ("BTC_USDT_SWAP",)}},
    )


def test_detects_known_pump():
    prices = [100.0] * 6 + [110.0, 125.0, 140.0] + [140.0] * 6
    events = detect_move_events(
        _candles(prices), anchor_symbol="SOL_USDT_SWAP", timeframe="15m",
        window=3, min_move_pct=8.0,
    )
    assert events
    assert events[0].direction == "up"
    assert events[0].move_pct > 8.0
    assert events[0].move_start_idx < events[0].move_end_idx


def test_detects_known_dump():
    prices = [100.0] * 6 + [90.0, 78.0, 70.0] + [70.0] * 6
    events = detect_move_events(
        _candles(prices), anchor_symbol="SOL_USDT_SWAP", timeframe="15m",
        window=3, min_move_pct=8.0,
    )
    assert events
    assert events[0].direction == "down"
    assert events[0].move_pct < 0


def test_attach_related_uses_universe():
    prices = [100.0] * 6 + [110.0, 125.0, 140.0] + [140.0] * 6
    events = detect_move_events(
        _candles(prices), anchor_symbol="SOL_USDT_SWAP", timeframe="15m",
        window=3, min_move_pct=8.0,
    )
    enriched = attach_related(events[0], _universe())
    assert "BTC_USDT_SWAP" in enriched.related_symbols
    assert "WLD_USDT_SWAP" in enriched.related_symbols  # peer
    assert enriched.anchor_symbol not in enriched.related_symbols


def test_respects_max_events():
    prices = (
        [100.0] * 4 + [130.0, 130.0, 130.0]
        + [100.0, 100.0, 100.0] + [130.0, 130.0, 130.0]
    )
    events = detect_move_events(
        _candles(prices), anchor_symbol="X_USDT_SWAP", timeframe="15m",
        window=3, min_move_pct=8.0, max_events=1,
    )
    assert len(events) == 1


def test_no_event_when_movement_too_small():
    prices = [100.0 + (i % 2) * 0.5 for i in range(20)]
    events = detect_move_events(
        _candles(prices), anchor_symbol="X_USDT_SWAP", timeframe="15m",
        window=3, min_move_pct=8.0,
    )
    assert events == []


def test_pre_move_candles_have_no_lookahead():
    prices = [100.0] * 6 + [110.0, 125.0, 140.0] + [140.0] * 6
    candles = _candles(prices)
    events = detect_move_events(
        candles, anchor_symbol="X_USDT_SWAP", timeframe="15m",
        window=3, min_move_pct=8.0,
    )
    pre = pre_move_candles(candles, events[0], lookback=4)
    # every returned bar is strictly before the move start
    assert all(c["ts"] < candles[events[0].move_start_idx]["ts"] for c in pre)
    assert len(pre) <= 4
