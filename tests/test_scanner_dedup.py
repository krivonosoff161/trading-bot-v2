# -*- coding: utf-8 -*-
"""test_scanner_dedup.py — event-level дедуп (заголовки одного актива из N лент → 1)."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.dedup import normalize_title, event_signature, event_key, is_duplicate  # noqa: E402


def test_normalize_strips_prefix_and_stopwords():
    n = normalize_title("CoinDesk: The Bitcoin ETF approved by the SEC")
    assert "coindesk" not in n and "the" not in n and "bitcoin" in n and "etf" in n


def test_event_key_stable_for_near_dup():
    a = event_key("BTC", "Bitcoin ETF approved by SEC")
    b = event_key("BTC", "Bitcoin ETF approved by SEC")
    assert a == b and a.startswith("BTC::")


def test_event_signature_security_incident_groups_reworded_titles():
    assert event_signature("ZEC crashes 38% as Zcash discloses critical counterfeiting vulnerability") == "security_incident"
    recent = [("ZEC", "Winklevoss-Backed Zcash Treasury Plunges Nearly 40% on ZEC Privacy Bug Concerns")]
    assert is_duplicate("ZEC Crashes 38% as Zcash Discloses Critical Counterfeiting Vulnerability", "ZEC", recent, 88)


def test_is_duplicate_same_event_diff_wording():
    recent = [("BTC", "SEC approves spot Bitcoin ETF after long wait")]
    assert is_duplicate("Bitcoin spot ETF approved by SEC", "BTC", recent, 80)


def test_is_duplicate_rejects_other_asset():
    recent = [("ETH", "SEC approves spot Bitcoin ETF")]
    assert not is_duplicate("SEC approves spot Bitcoin ETF", "BTC", recent, 88)


def test_is_duplicate_rejects_different_event():
    recent = [("BTC", "Bitcoin ETF approved by SEC")]
    assert not is_duplicate("Bitcoin miner sells 300 BTC in Q1", "BTC", recent, 88)


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
