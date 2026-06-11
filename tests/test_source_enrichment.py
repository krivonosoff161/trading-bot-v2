# -*- coding: utf-8 -*-
"""
test_source_enrichment.py — интерфейсы обогащения источников (без сети).

Контракты P1-пакета 11.06: ETF flow = контекст L1 (не сигнал, honest not-configured);
token unlocks/EIA surprise = graceful-disabled без ключа; dexscreener flow_metrics =
структурное качество без фейковой точности; обогащение само по себе НЕ создаёт GO.
"""
import asyncio
import datetime as dt
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.sources import etf_flow as EF  # noqa: E402
from src.scout.sources import eia as EIA  # noqa: E402
from src.scout.sources import token_unlocks as TU  # noqa: E402
from src.scout.sources.dexscreener import _signal_from_pair, AssetRef  # noqa: E402
from src.scout.agents import orchestrator as O  # noqa: E402


# ── ETF flow ──────────────────────────────────────────────────────────────────
CSV_FIXTURE = """date,ticker,asset,flow_usd_m,source
2026-06-10,IBIT,BTC,-120.5,farside
2026-06-10,FBTC,BTC,35.0,farside
2026-06-10,ETHA,ETH,,farside
2026-06-09,IBIT,BTC,80.0,farside
"""


def test_etf_flow_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SCANNER_ETF_FLOW_PROVIDER", raising=False)
    st = EF.status()
    assert st["configured"] is False and "not_configured" in st["reason"]
    assert EF.fetch_etf_flow_records() == []
    assert EF.context_line(records=[]) == ""


def test_etf_flow_csv_parse_normalized_shape():
    recs = EF.parse_manual_csv(CSV_FIXTURE)
    assert len(recs) == 4
    r = recs[0]
    assert set(r) == {"date", "ticker", "asset", "flow_usd_m", "direction",
                      "source", "source_quality"}
    assert r["direction"] == "outflow" and r["flow_usd_m"] == -120.5
    assert recs[1]["direction"] == "inflow"
    # пропущенное число → None + unknown, не выдумано
    assert recs[2]["flow_usd_m"] is None and recs[2]["direction"] == "unknown"
    assert all(x["source_quality"] == "manual" for x in recs)


def test_etf_flow_context_line_latest_date_only():
    line = EF.context_line(records=EF.parse_manual_csv(CSV_FIXTURE))
    assert "2026-06-10" in line and "BTC" in line
    assert "-85.5" in line                       # сумма по BTC за последнюю дату
    assert "ETH n/a" in line                     # пропуск помечен явно
    assert "контекст, не сигнал" in line
    assert "2026-06-09" not in line


def test_etf_context_does_not_escalate_by_itself():
    """Контекст в market_ctx не меняет гейт: JOURNAL_NO_GO остаётся без chief."""
    calls = {"chief": 0}

    async def fake_analyze(event, layer_, asset=None):
        return {"asset": "BTC", "event_type": "news", "phase": "realized",
                "direction": "none", "materiality": 0.4, "confidence": 0.4,
                "key_facts": [], "numbers": [], "red_flags": [], "veto_flags": [],
                "no_edge_flags": [], "mechanics": [], "pre_verdict": "JOURNAL_NO_GO",
                "should_escalate": False, "escalation_reason": "", "no_go_reason": "x",
                "trigger_text": "", "suggested_horizon_hours": 24,
                "reason_to_escalate": "", "_usage": {}, "_ok": True}

    async def fake_chief(*a, **kw):
        calls["chief"] += 1
        return None

    orig_a, orig_c = O.layer_agent.analyze, O.chief_mod.decide
    O.layer_agent.analyze, O.chief_mod.decide = fake_analyze, fake_chief
    try:
        out = asyncio.run(O.process(
            {"headline": "h", "text": "t"}, "BTC", 1, "LAGGING", 100.0,
            "Fear 28 | ETF-потоки 2026-06-10: BTC -85.5M$ (контекст, не сигнал)"))
    finally:
        O.layer_agent.analyze, O.chief_mod.decide = orig_a, orig_c
    assert calls["chief"] == 0 and out["verdict"] == "NO_GO"


# ── token unlocks ─────────────────────────────────────────────────────────────
def test_unlocks_disabled_without_key(monkeypatch):
    monkeypatch.delenv("TOKENOMIST_API_KEY", raising=False)
    assert TU.fetch_upcoming_unlocks() == []
    st = TU.unlocks_status()
    assert st["configured"] is False and "not_configured" in st["reason"]


def test_unlock_item_normalized_fields():
    token = {"tokenSymbol": "PEPE", "tokenName": "Pepe", "tokenId": "pepe",
             "marketCap": 4_000_000_000, "releasedPercentage": None,
             "upcomingEvent": {"unlockDate": "2026-06-15T00:00:00Z",
                               "cliffUnlocks": {"cliffValue": 50_000_000,
                                                "cliffAmount": 1e12,
                                                "valueToMarketCap": 1.25,
                                                "allocationBreakdown": []}}}
    item = TU._build_item(token, {"okx_inst": "PEPE-USDT-SWAP", "baseline": "BTC-USDT-SWAP"})
    assert item["event_type"] == "unlock" and item["phase"] == "EXPECTED"
    assert item["unlock_value_usd"] == 50_000_000
    assert item["unlock_value_to_mcap_pct"] == 1.25
    assert item["released_supply_pct"] is None        # пропуск помечен, не выдуман
    assert item["source_quality"] == "primary"


# ── dexscreener flow_metrics ──────────────────────────────────────────────────
def _pair_row(**kw):
    row = {"baseToken": {"symbol": "PEPE", "name": "Pepe", "address": "0xabc"},
           "quoteToken": {"symbol": "WETH"}, "chainId": "ethereum", "dexId": "uniswap",
           "pairAddress": "0xpair", "url": "https://dexscreener.com/x",
           "liquidity": {"usd": 300_000}, "volume": {"h24": 600_000},
           "priceChange": {"h24": 5.5}, "priceUsd": "0.0000012",
           "pairCreatedAt": None}
    row.update(kw)
    return row


def test_dexscreener_flow_metrics_marks_missing_age():
    asset = AssetRef(sym="PEPE", okx_inst="PEPE-USDT-SWAP",
                     baseline="BTC-USDT-SWAP", aliases=("pepe",))
    now = dt.datetime(2026, 6, 11, tzinfo=dt.timezone.utc)
    sig = _signal_from_pair(asset, _pair_row(), now)
    fm = sig["flow_metrics"]
    assert fm["liquidity_usd"] == 300_000 and fm["volume_24h_usd"] == 600_000
    assert fm["pair_age_hours"] is None              # API не дал — None, не 0
    assert fm["turnover_to_liquidity"] == 2.0
    assert "Pair age unknown." in sig["text"]


def test_dexscreener_flow_metrics_with_age():
    asset = AssetRef(sym="PEPE", okx_inst=None, baseline=None, aliases=("pepe",))
    now = dt.datetime(2026, 6, 11, tzinfo=dt.timezone.utc)
    created = int((now - dt.timedelta(hours=10)).timestamp() * 1000)
    sig = _signal_from_pair(asset, _pair_row(pairCreatedAt=created), now)
    assert sig["event_type"] == "launch"
    assert sig["flow_metrics"]["pair_age_hours"] == 10.0


# ── EIA surprise ──────────────────────────────────────────────────────────────
def test_eia_surprise_full_record():
    rec = EIA.build_surprise_record(report_ts="2026-06-05", actual_change_mbbl=-7.2,
                                    consensus_change_mbbl=-2.0, previous_change_mbbl=1.4)
    assert rec["surprise_mbbl"] == -5.2 and rec["surprise_available"] is True
    assert rec["direction_hint"] == "bullish_oil"


def test_eia_surprise_partial_without_consensus():
    rec = EIA.build_surprise_record(report_ts="2026-06-05", actual_change_mbbl=-7.2,
                                    previous_change_mbbl=1.4)
    assert rec["surprise_mbbl"] is None
    assert rec["surprise_available"] is False
    assert rec["direction_hint"] == "unavailable"    # не выдумываем интерпретацию


def test_eia_fetch_disabled_without_key(monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    assert EIA.fetch_actual_crude_stocks() is None
    assert EIA.surprise_status()["configured"] is False


def test_eia_fetch_actual_with_fixture(monkeypatch):
    class _Resp:
        def json(self):
            return {"response": {"data": [
                {"period": "2026-06-05", "value": 440_000},   # тыс. барр.
                {"period": "2026-05-29", "value": 447_200},
                {"period": "2026-05-22", "value": 445_800},
            ]}}

    class _Http:
        def get(self, url, **kw):
            return _Resp()

    rec = EIA.fetch_actual_crude_stocks(api_key="test-key", http=_Http())
    assert rec["actual_change_mbbl"] == -7.2         # (440000-447200)/1000
    assert rec["previous_change_mbbl"] == 1.4
    assert rec["surprise_available"] is False        # консенсуса в API нет — partial
    assert rec["report_ts"] == "2026-06-05"


def test_eia_fetch_never_raises(monkeypatch):
    class _Boom:
        def get(self, *a, **kw):
            raise RuntimeError("down")
    assert EIA.fetch_actual_crude_stocks(api_key="k", http=_Boom()) is None
