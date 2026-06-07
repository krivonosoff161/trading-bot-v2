# -*- coding: utf-8 -*-
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.sources.goplus_rugcheck import fetch_token_risk_signals  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fetch_token_risk_signals_emits_evm_rug_flag(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert "token_security/1" in url
        return _Resp(
            {
                "result": {
                    "0xabc": {
                        "is_honeypot": "1",
                        "cannot_sell_all": "1",
                        "buy_tax": "0",
                        "sell_tax": "0",
                    }
                }
            }
        )

    monkeypatch.setattr("src.scout.sources.goplus_rugcheck.requests.get", fake_get)

    rows = fetch_token_risk_signals(
        [
            {
                "asset": "PEPE",
                "url": "https://dexscreener.com/ethereum/pair-pepe",
                "time": "2026-06-07T12:00:00Z",
                "chain": "ethereum",
                "contract_address": "0xabc",
                "pair_address": "pair-pepe",
                "okx_inst": "PEPE-USDT-SWAP",
                "baseline": "BTC-USDT-SWAP",
            }
        ]
    )

    assert len(rows) == 1
    assert rows[0]["source"] == "goplus_rugcheck"
    assert rows[0]["lead_class"] == "LEADING"
    assert rows[0]["event_type"] == "rug_flag"
    assert "honeypot" in rows[0]["risk_reasons"]


def test_fetch_token_risk_signals_ignores_single_noisy_flag(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _Resp(
            {
                "result": {
                    "0xondo": {
                        "is_honeypot": "0",
                        "cannot_sell_all": "0",
                        "cannot_buy": "0",
                        "is_blacklisted": "0",
                        "transfer_pausable": "1",
                        "buy_tax": "0",
                        "sell_tax": "0",
                    }
                }
            }
        )

    monkeypatch.setattr("src.scout.sources.goplus_rugcheck.requests.get", fake_get)

    rows = fetch_token_risk_signals(
        [
            {
                "asset": "ONDO",
                "url": "https://dexscreener.com/ethereum/pair-ondo",
                "time": "2026-06-07T12:00:00Z",
                "chain": "ethereum",
                "contract_address": "0xondo",
                "pair_address": "pair-ondo",
                "okx_inst": "ONDO-USDT-SWAP",
                "baseline": "BTC-USDT-SWAP",
            }
        ]
    )

    assert rows == []


def test_fetch_token_risk_signals_emits_solana_when_multiple_authorities(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert "solana/token_security" in url
        return _Resp(
            {
                "result": {
                    "pumpaddr": {
                        "non_transferable": "0",
                        "mintable": {"status": "1"},
                        "freezable": {"status": "1"},
                        "closable": {"status": "0"},
                        "transfer_fee_upgradable": {"status": "0"},
                        "balance_mutable_authority": {"status": "0"},
                        "default_account_state_upgradable": {"status": "0"},
                    }
                }
            }
        )

    monkeypatch.setattr("src.scout.sources.goplus_rugcheck.requests.get", fake_get)

    rows = fetch_token_risk_signals(
        [
            {
                "asset": "PUMP",
                "url": "https://dexscreener.com/solana/pair-pump",
                "time": "2026-06-07T12:00:00Z",
                "chain": "solana",
                "contract_address": "pumpaddr",
                "pair_address": "pair-pump",
                "okx_inst": "PUMP-USDT-SWAP",
                "baseline": "BTC-USDT-SWAP",
            }
        ]
    )

    assert len(rows) == 1
    assert sorted(rows[0]["risk_reasons"]) == ["freezable", "mintable"]
