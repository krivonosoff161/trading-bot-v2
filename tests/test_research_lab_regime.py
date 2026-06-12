# -*- coding: utf-8 -*-

from src.research_lab.regime import regime_at, regime_breakdown, regime_matches


def _candles(n: int, range_pct: float, drift: float, vol: float) -> list[dict]:
    rows = []
    price = 100.0
    for i in range(n):
        price *= 1 + drift
        half = price * range_pct / 200
        rows.append(
            {
                "ts": 1_700_000_000_000 + i * 86_400_000,
                "open": price,
                "high": price + half,
                "low": price - half,
                "close": price,
                "vol": vol,
            }
        )
    return rows


def test_regime_at_low_vol_sideways():
    candles = _candles(40, range_pct=1.0, drift=0.0, vol=1000)
    regime = regime_at(candles, 30)
    assert regime == {"volatility": "low", "trend": "sideways", "volume": "normal"}


def test_regime_at_high_vol_uptrend():
    candles = _candles(40, range_pct=15.0, drift=0.01, vol=1000)
    regime = regime_at(candles, 30)
    assert regime["volatility"] == "high"
    assert regime["trend"] == "up"


def test_regime_at_downtrend_and_elevated_volume():
    candles = _candles(40, range_pct=5.0, drift=-0.01, vol=1000)
    for c in candles[-8:]:
        c["vol"] = 5000
    regime = regime_at(candles, 40)
    assert regime["trend"] == "down"
    assert regime["volume"] == "elevated"


def test_regime_at_insufficient_history_is_unknown():
    candles = _candles(10, range_pct=2.0, drift=0.0, vol=1000)
    assert regime_at(candles, 5) == {
        "volatility": "unknown", "trend": "unknown", "volume": "unknown",
    }


def test_regime_matches_empty_filters_match_all():
    regime = {"volatility": "high", "trend": "up", "volume": "normal"}
    assert regime_matches(regime, {})
    assert regime_matches(regime, {"volatility": ["high", "medium"]})
    assert not regime_matches(regime, {"volatility": ["low"]})
    assert not regime_matches(regime, {"trend": ["down"], "volatility": ["high"]})


def test_regime_breakdown_aggregates_buckets():
    trades = [
        {"net_pct": 1.0, "regime": {"volatility": "high", "trend": "up", "volume": "normal"}},
        {"net_pct": -0.5, "regime": {"volatility": "high", "trend": "up", "volume": "normal"}},
        {"net_pct": 2.0, "regime": {"volatility": "low", "trend": "sideways", "volume": "thin"}},
    ]

    buckets = regime_breakdown(trades)

    assert buckets["high|up|normal"]["n_trades"] == 2
    assert buckets["high|up|normal"]["avg_net_pct"] == 0.25
    assert buckets["high|up|normal"]["win_rate"] == 0.5
    assert buckets["low|sideways|thin"]["n_trades"] == 1
