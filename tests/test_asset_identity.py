# -*- coding: utf-8 -*-
"""
test_asset_identity.py — tests for deterministic asset identity resolver.

Validates:
  - SPCXX → L5 tokenized_equity, requires_context=True, baseline QQQ
  - ARX (crypto listing) → L2 crypto_alt, not equity
  - BTC liquidation → L1 liquidation_flow/context_trigger
  - SpaceX/xStocks from tg_markettwits → L5 needs_context
  - Unknown ticker → not blindly L2
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.asset_identity import identify_asset  # noqa: E402


def test_spcxx_tokenized_equity_l5():
    """SPCXX listed on Bybit → L5, tokenized_equity, requires_context, baseline QQQ."""
    r = identify_asset("SPCXX", "$SPCXX listed on Bybit spot")
    assert r["symbol"] == "SPCXX"
    assert r["asset_class"] == "tokenized_equity"
    assert r["layer"] == 5
    assert r["baseline"] == "QQQ-USDT-SWAP"
    assert r["requires_context"] is True
    assert r["trigger_role"] == "needs_context"
    assert r["confidence"] >= 0.9


def test_spcx_tokenized_equity_l5():
    """SPCX → L5, tokenized_equity."""
    r = identify_asset("SPCX", "SpaceX whale opens $22M SPCX long")
    assert r["symbol"] == "SPCX"
    assert r["asset_class"] == "tokenized_equity"
    assert r["layer"] == 5
    assert r["baseline"] == "QQQ-USDT-SWAP"
    assert r["requires_context"] is True


def test_arx_crypto_listing_stays_l2():
    """ARX added to Coinbase roadmap from listing source → L2 crypto_alt, NOT equity."""
    meta = {"telegram_kind": "listing", "trust": "tg_alpha"}
    r = identify_asset("ARX", "$ARX added to Coinbase roadmap", meta)
    assert r["symbol"] == "ARX"
    assert r["asset_class"] == "crypto_alt"
    assert r["layer"] == 2
    assert r["baseline"] == "BTC-USDT-SWAP"
    assert r["requires_context"] is False
    assert r["trigger_role"] == "signal"


def test_arx_unknown_source_stays_unknown():
    """ARX from non-listing source (news) with no equity context → unknown."""
    meta = {"telegram_kind": "news", "trust": "tg_alpha"}
    r = identify_asset("ARX", "$ARX added to Coinbase roadmap", meta)
    assert r["symbol"] == "ARX"
    assert r["asset_class"] == "unknown"
    assert r["layer"] is None
    assert r["requires_context"] is True


def test_btc_liquidation_is_l1_context_trigger():
    """#BTC Liquidated Short → L1 liquidation_flow/context_trigger."""
    meta = {"telegram_kind": "liquidations", "trust": "tg_alpha"}
    r = identify_asset("BTC", "#BTC Liquidated Short $1.2M at $108,421", meta)
    assert r["symbol"] == "BTC"
    assert r["asset_class"] == "liquidation_flow"
    assert r["layer"] == 1
    assert r["trigger_role"] == "context_trigger"
    assert r["requires_context"] is True


def test_trump_liquidation_is_l2_meme_flow():
    """#TRUMP Liquidated Long → L2 meme/flow context."""
    meta = {"telegram_kind": "liquidations", "trust": "tg_alpha"}
    r = identify_asset("TRUMP", "#TRUMP Liquidated Long $50K at $12.50", meta)
    assert r["symbol"] == "TRUMP"
    assert r["asset_class"] == "liquidation_flow"
    assert r["layer"] == 2
    assert r["trigger_role"] == "context_trigger"
    assert r["requires_context"] is True


def test_spacex_from_text_is_l5_needs_context():
    """SpaceX mentioned in text → L5 pre_ipo_equity, needs_context."""
    r = identify_asset("SPACEX", "SpaceX IPO expected next quarter")
    assert r["symbol"] == "SPACEX"
    assert r["asset_class"] == "pre_ipo_equity"
    assert r["layer"] == 5
    assert r["requires_context"] is True
    assert r["trigger_role"] == "needs_context"


def test_openai_pre_ipo_equity():
    """OpenAI → L5 pre_ipo_equity."""
    r = identify_asset("OPENAI", "OpenAI reportedly in talks for IPO")
    assert r["symbol"] == "OPENAI"
    assert r["asset_class"] == "pre_ipo_equity"
    assert r["layer"] == 5
    assert r["requires_context"] is True


def test_equity_context_keyword_unknown_ticker():
    """Unknown ticker + 'tokenized' in text → L5 tokenized_equity."""
    r = identify_asset("XYZQ", "$XYZQ tokenized shares listed on Bybit")
    assert r["symbol"] == "XYZQ"
    assert r["asset_class"] == "tokenized_equity"
    assert r["layer"] == 5
    assert r["requires_context"] is True
    assert r["trigger_role"] == "needs_context"


def test_unknown_ticker_not_blindly_l2():
    """Unknown ticker with no context → unknown asset_class, no layer, low confidence."""
    r = identify_asset("ZZZZ", "ZZZZ listed on new exchange")
    assert r["symbol"] == "ZZZZ"
    assert r["asset_class"] == "unknown"
    assert r["layer"] is None
    assert r["confidence"] <= 0.35
    assert r["requires_context"] is True
    assert r["trigger_role"] == "needs_context"


def test_known_major_btc_is_l1():
    """BTC → L1 crypto_major, signal, no context needed."""
    r = identify_asset("BTC", "Bitcoin ETF inflow hits $500M")
    assert r["symbol"] == "BTC"
    assert r["asset_class"] == "crypto_major"
    assert r["layer"] == 1
    assert r["trigger_role"] == "signal"
    assert r["requires_context"] is False


def test_known_alt_sui_is_l2():
    """SUI → L2 crypto_alt, signal."""
    r = identify_asset("SUI", "Sui mainnet upgrade goes live")
    assert r["symbol"] == "SUI"
    assert r["asset_class"] == "crypto_alt"
    assert r["layer"] == 2
    assert r["trigger_role"] == "signal"


def test_known_major_eth_is_l1():
    """ETH → L1."""
    r = identify_asset("ETH", "Ethereum staking yield rises")
    assert r["symbol"] == "ETH"
    assert r["asset_class"] == "crypto_major"
    assert r["layer"] == 1


def test_nvda_equity_is_l5():
    """NVDA → L5 equity, signal."""
    r = identify_asset("NVDA", "Nvidia beats earnings Q1")
    assert r["symbol"] == "NVDA"
    assert r["asset_class"] == "equity"
    assert r["layer"] == 5
    assert r["trigger_role"] == "signal"


def test_xstocks_equity_keyword():
    """xStocks keyword in text → L5."""
    r = identify_asset("TSLA", "xStocks launches Tesla synthetic token")
    assert r["symbol"] == "TSLA"
    assert r["asset_class"] == "equity"
    assert r["layer"] == 5


def test_identity_returns_required_fields():
    """Every identity result has all required fields."""
    r = identify_asset("BTC", "Bitcoin rally")
    required = [
        "symbol", "asset_class", "layer", "baseline", "okx_inst",
        "confidence", "reason", "requires_context", "context_requirements",
        "trigger_role",
    ]
    for field in required:
        assert field in r, f"missing field: {field}"


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
    print(f"\n{'ALL PASSED' if not failed else f'{failed} failed'}")
    sys.exit(1 if failed else 0)
