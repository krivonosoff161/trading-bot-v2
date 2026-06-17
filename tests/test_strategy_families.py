# -*- coding: utf-8 -*-
"""Every registered strategy family runs without error and emits a valid contract.

Also checks the no-lookahead entry convention (entry idx within range) and that the
flow/microstructure families degrade to ZERO signals when their optional data is
absent (NEEDS_DATA), instead of inventing values.
"""

from __future__ import annotations

import math
import random

from src.research_lab import strategy_registry as reg

FLOW_FAMILIES = {"oi_funding_squeeze", "oi_price_quadrant"}
MICRO_FAMILIES = {"microstructure_confirmed_breakout"}


def _base_series(n: int = 320, seed: int = 11) -> list[dict]:
    rng = random.Random(seed)
    price = 100.0
    out: list[dict] = []
    ts = 1_700_000_000_000
    for i in range(n):
        # a tight range for the first 120 bars, then trending bursts -> several families fire
        if i < 120:
            price += rng.uniform(-0.3, 0.3)
        else:
            price += math.sin(i / 9.0) * 1.5 + rng.uniform(-0.8, 0.8)
        price = max(1.0, price)
        high = price + abs(rng.uniform(0.1, 1.2))
        low = price - abs(rng.uniform(0.1, 1.2))
        out.append({
            "ts": ts + i * 3_600_000,
            "open": round(low + (high - low) * rng.random(), 4),
            "high": round(high, 4), "low": round(low, 4),
            "close": round(low + (high - low) * rng.random(), 4),
            "vol": round(rng.uniform(100, 2000) * (3.0 if i % 17 == 0 else 1.0), 2),
        })
    return out


def _with_flow(candles: list[dict]) -> list[dict]:
    """Clearly rising OI (so dOI over lookback exceeds the surge threshold) plus
    periodic funding extremes — gives the flow families a fireable context."""
    out = []
    for i, c in enumerate(candles):
        oi = 1_000_000.0 * (1.0 + 0.01 * i)  # monotonic build, dOI(5) ~ +5%
        funding = 0.0012 if i % 13 == 0 else 0.0001
        out.append({**c, "oi": round(oi, 2), "funding": funding,
                    "index_px": round(float(c["close"]) * 1.0005, 6)})
    return out


def _with_micro(candles: list[dict]) -> list[dict]:
    return [{**c, "obi_top5": 0.4 if i % 2 == 0 else -0.4,
             "trade_delta_100": 50.0 if i % 2 == 0 else -50.0,
             "spread_bps": 2.0} for i, c in enumerate(candles)]


def _assert_valid(signals, n: int):
    assert isinstance(signals, list)
    for s in signals:
        assert set(("idx", "side", "reason")) <= set(s)
        assert 0 <= int(s["idx"]) < n
        assert s["side"] in ("long", "short")
        assert isinstance(s["reason"], str) and s["reason"]


def test_all_families_run_and_emit_valid_contract():
    base = _base_series()
    enriched = _with_micro(_with_flow(base))
    assert len(reg.strategy_ids()) >= 23
    for sid in reg.strategy_ids():
        sdef = reg.get_strategy(sid)
        candles = enriched if sid in (FLOW_FAMILIES | MICRO_FAMILIES) else base
        signals = sdef.generate_signals(candles, dict(sdef.parameter_defaults))
        _assert_valid(signals, len(candles))


def test_flow_families_need_data():
    base = _base_series()  # no oi/funding fields
    for sid in FLOW_FAMILIES:
        sdef = reg.get_strategy(sid)
        assert sdef.generate_signals(base, dict(sdef.parameter_defaults)) == []
    enriched = _with_flow(base)
    fired = sum(len(reg.get_strategy(sid).generate_signals(enriched, dict(reg.get_strategy(sid).parameter_defaults)))
                for sid in FLOW_FAMILIES)
    assert fired > 0, "flow families should fire when OI/funding present"


def test_microstructure_needs_data():
    base = _base_series()
    sdef = reg.get_strategy("microstructure_confirmed_breakout")
    assert sdef.generate_signals(base, dict(sdef.parameter_defaults)) == []


def test_new_families_registered():
    expected = {
        "main_fast_swing_regime", "range_volume_breakout", "volatility_squeeze_breakout_v2",
        "vwap_reclaim_reject", "fvg_reclaim_reject", "fractal_swing_break_retest",
        "oi_funding_squeeze", "oi_price_quadrant", "bb_volume_fade", "pump_dump_scalp",
        "microstructure_confirmed_breakout",
    }
    assert expected <= set(reg.strategy_ids())
