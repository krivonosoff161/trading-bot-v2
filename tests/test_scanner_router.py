# -*- coding: utf-8 -*-
"""
test_scanner_router.py — тесты детерминированного ядра «вывода».

Роутер актива/слоя, темпорал (будет/произошло), материальность. Ловит регресс
дизамбигуации (SOL-в-Zcash) и фаз. Запуск: python tests/test_scanner_router.py (или pytest).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.router import route_asset, route_temporal, score_materiality  # noqa: E402


def test_route_asset_strong_name():
    r = route_asset("Bitcoin gets new $50K target after BTC crash")
    assert r and r["asset"] == "BTC" and r["layer"] == 1 and r["confidence"] >= 0.7


def test_route_asset_rejects_bare_short_ticker():
    # «sol» в «sol energy» — голый тикер без имени/cashtag → НЕ роутить (анти-SOL-в-Zcash)
    assert route_asset("New solar panel tech uses sol energy") is None
    assert route_asset("Orchard fixes emergency bug after protocol upgrade") is None


def test_route_asset_cashtag_confirms():
    r = route_asset("$SOL surges 20% on ETF news")
    assert r and r["asset"] == "SOL"


def test_route_asset_subject_first():
    r = route_asset("Ethereum and Bitcoin both rally on macro data")
    assert r and r["asset"] == "ETH"   # субъект (первый) выигрывает


def test_temporal_realized_headlinese():
    # present-simple результативный = уже случилось (не future)
    assert route_temporal("Bitcoin crashes 6% in a day")["phase"] == "REALIZED"
    assert route_temporal("SEC approved spot ETF")["phase"] == "REALIZED"


def test_temporal_future():
    assert route_temporal("Ethereum upgrade scheduled for next week")["phase"] == "FUTURE"
    assert route_temporal("Company plans to launch token")["phase"] == "FUTURE"


def test_temporal_context():
    assert route_temporal("Bitcoin price analysis: key levels")["phase"] == "CONTEXT"


def test_materiality_noise_dropped():
    m = score_materiality("Bitcoin price prediction for 2027", 1)
    assert m["drop_reason"] == "noise_genre"


def test_materiality_material_family():
    m = score_materiality("SEC approves spot Bitcoin ETF inflow", 1)
    assert m["family"] in ("etf_flow", "regulation") and m["score"] >= 0.5


def test_materiality_no_term_dropped():
    m = score_materiality("Bitcoin moves sideways quietly today", 1)
    assert m["drop_reason"] == "no_material_term"


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except AssertionError as e:
                failed += 1
                print(f"[FAIL] {name}: {e}")
    print(f"\n{'ВСЕ ТЕСТЫ ПРОШЛИ' if not failed else f'{failed} упало'}")
    sys.exit(1 if failed else 0)
