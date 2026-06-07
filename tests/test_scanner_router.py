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

from src.scout.router import layer_plan, route_asset, route_temporal, score_materiality  # noqa: E402


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
    assert route_temporal("Should You Buy, Sell, or Hold MSTR Stock Before Q1 Earnings?")["phase"] == "CONTEXT"
    assert route_temporal("Why Nvidia stock looks cheap ahead of its high-stakes earnings report this month")["phase"] == "CONTEXT"


def test_materiality_noise_dropped():
    m = score_materiality("Bitcoin price prediction for 2027", 1)
    assert m["drop_reason"] == "noise_genre"


def test_materiality_material_family():
    m = score_materiality("SEC approves spot Bitcoin ETF inflow", 1)
    assert m["family"] in ("etf_flow", "regulation") and m["score"] >= 0.5


def test_materiality_no_term_dropped():
    m = score_materiality("Bitcoin moves sideways quietly today", 1)
    assert m["drop_reason"] == "no_material_term"


# ── тёмные слои: металлы(3)/нефть(4)/акции(5) + layer-scoping ────────────────
def test_route_metals_gold():
    r = route_asset("Gold surges to record high as Fed signals rate cut")
    assert r and r["asset"] == "XAU" and r["layer"] == 3


def test_route_oil_opec():
    r = route_asset("OPEC+ agrees surprise output cut to lift prices")
    assert r and r["asset"] == "CL" and r["layer"] == 4


def test_route_layer_scope_excludes_offlayer():
    # без скоупа золото-субъект выигрывает; крипто-лента (allowed={1,2}) → не матчит XAU, берёт BTC
    assert route_asset("Gold hits record as Bitcoin wobbles")["asset"] == "XAU"
    assert route_asset("Gold hits record as Bitcoin wobbles", allowed_layers={1, 2})["asset"] == "BTC"


def test_materiality_oil_opec_family():
    m = score_materiality("OPEC announces surprise output cut at meeting", 4)
    assert m["family"] == "opec" and m["score"] >= 0.5


def test_materiality_equities_earnings_family():
    m = score_materiality("Nvidia beats earnings, raises guidance", 5)
    assert m["family"] == "earnings" and m["score"] >= 0.5


def test_layer_plan_matrix_loaded():
    l1 = layer_plan(1)
    l3 = layer_plan(3)
    l2 = layer_plan(2)
    l5 = layer_plan(5)
    assert l1["name"]
    assert l3["name"]
    assert l2["name"]
    assert l5["name"]
    tactical_sources = {row["source"] for row in l1["tactical_sources"]}
    l1_expected = {row["source"] for row in l1["expected_sources"]}
    l3_expected = {row["source"] for row in l3["expected_sources"]}
    l4 = layer_plan(4)
    l4_expected = {row["source"] for row in l4["expected_sources"]}
    l5_expected = {row["source"] for row in l5["expected_sources"]}
    realized_sources = {row["source"] for row in l2["realized_sources"]}
    expected_sources = {row["source"] for row in l2["expected_sources"]}
    assert "btc_eth_tactical" in tactical_sources
    assert "fred_calendar" in l1_expected
    assert "fred_calendar" in l3_expected
    assert "eia" in l4_expected
    assert "opec" in l4_expected
    assert "okx_listings" in realized_sources
    assert "dexscreener" in realized_sources
    assert "goplus_rugcheck" in realized_sources
    assert "token_unlocks" in expected_sources
    assert "earnings_calendar" in l5_expected


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
