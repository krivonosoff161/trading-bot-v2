# -*- coding: utf-8 -*-
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.sources.dexscreener import fetch_alt_flow_signals  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_alt_flow_signals_emits_launch_and_filters_noise(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        q = (params or {}).get("q")
        if q == "PEPE":
            return _Resp(
                {
                    "pairs": [
                        {
                            "pairAddress": "pair-pepe-good",
                            "url": "https://dexscreener.com/ethereum/pair-pepe-good",
                            "chainId": "ethereum",
                            "dexId": "uniswap",
                            "baseToken": {"symbol": "PEPE", "name": "Pepe"},
                            "quoteToken": {"symbol": "WETH", "name": "Wrapped Ether"},
                            "liquidity": {"usd": 240000},
                            "volume": {"h24": 910000},
                            "priceChange": {"h24": 18.4},
                            "pairCreatedAt": 1780780000000,
                            "priceUsd": "0.000012",
                        },
                        {
                            "pairAddress": "pair-pepe-noise",
                            "url": "https://dexscreener.com/ethereum/pair-pepe-noise",
                            "chainId": "ethereum",
                            "dexId": "uniswap",
                            "baseToken": {"symbol": "PEPE", "name": "Pepe"},
                            "quoteToken": {"symbol": "WETH", "name": "Wrapped Ether"},
                            "liquidity": {"usd": 10000},
                            "volume": {"h24": 12000},
                            "priceChange": {"h24": 2.1},
                            "pairCreatedAt": 1780780000000,
                            "priceUsd": "0.000012",
                        },
                    ]
                }
            )
        return _Resp({"pairs": []})

    monkeypatch.setattr("src.scout.sources.dexscreener.requests.get", fake_get)

    rows = fetch_alt_flow_signals(limit=8)

    pepe = next((r for r in rows if r["asset"] == "PEPE"), None)
    assert pepe is not None
    assert pepe["source"] == "dexscreener"
    assert pepe["layer"] == 2
    assert pepe["lead_class"] == "COINCIDENT"
    assert pepe["event_type"] in {"launch", "dex_momentum"}
    assert "Liquidity" in pepe["text"]


def test_fetch_alt_flow_signals_keeps_best_signal_per_asset(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        q = (params or {}).get("q")
        if q == "SUI":
            return _Resp(
                {
                    "pairs": [
                        {
                            "pairAddress": "pair-sui-small",
                            "url": "https://dexscreener.com/solana/pair-sui-small",
                            "chainId": "solana",
                            "dexId": "raydium",
                            "baseToken": {"symbol": "SUI", "name": "Sui"},
                            "quoteToken": {"symbol": "USDC", "name": "USD Coin"},
                            "liquidity": {"usd": 200000},
                            "volume": {"h24": 600000},
                            "priceChange": {"h24": 13.0},
                            "pairCreatedAt": 1700000000000,
                            "priceUsd": "1.11",
                        },
                        {
                            "pairAddress": "pair-sui-big",
                            "url": "https://dexscreener.com/solana/pair-sui-big",
                            "chainId": "solana",
                            "dexId": "raydium",
                            "baseToken": {"symbol": "SUI", "name": "Sui"},
                            "quoteToken": {"symbol": "USDC", "name": "USD Coin"},
                            "liquidity": {"usd": 350000},
                            "volume": {"h24": 2400000},
                            "priceChange": {"h24": 21.0},
                            "pairCreatedAt": 1700000000000,
                            "priceUsd": "1.12",
                        },
                    ]
                }
            )
        return _Resp({"pairs": []})

    monkeypatch.setattr("src.scout.sources.dexscreener.requests.get", fake_get)

    rows = fetch_alt_flow_signals(limit=8)

    sui = [r for r in rows if r["asset"] == "SUI"]
    assert len(sui) == 1
    assert sui[0]["pair_address"] == "pair-sui-big"
