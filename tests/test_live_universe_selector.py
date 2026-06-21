# -*- coding: utf-8 -*-
"""Live universe selector: USD-volume scoring, liquidity/spread filters, movement grouping, equity
separation, ranked live_mover intake events. Keyless public; no order/private imports."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import live_universe_selector as LU  # noqa: E402


def _tk(inst, last, o, hi, lo, volccy, ask=None, bid=None):
    ask = ask if ask is not None else last * 1.0005
    bid = bid if bid is not None else last * 0.9995
    return {"instId": inst, "last": str(last), "open24h": str(o), "high24h": str(hi),
            "low24h": str(lo), "volCcy24h": str(volccy), "askPx": str(ask), "bidPx": str(bid)}


class TestScore:
    def test_usd_volume_is_base_times_price(self):
        # volCcy24h is base units; vol_usd must be base*last
        m = LU.score_ticker(_tk("PEPE-USDT-SWAP", 0.00001, 0.00001, 0.000011, 0.0000099, 1e13))
        assert m is not None and abs(m["vol_usd"] - 1e13 * 0.00001) < 1.0  # ~ $100M not $1e13

    def test_untradeable_dropped(self):
        assert LU.score_ticker(_tk("X-USDT-SWAP", 0, 1, 1, 1, 1)) is None       # last<=0
        assert LU.score_ticker({"instId": "Y-USDT-SWAP"}) is None               # missing fields
        assert LU.score_ticker(_tk("Z-USDT-SPOT", 1, 1, 1, 1, 1)) is None       # not a swap


class TestGrouping:
    def test_fresh_mover_by_range(self):
        m = {"symbol": "WHATEVER_USDT_SWAP", "range_pct": 20.0}
        assert LU._group("WHATEVER_USDT_SWAP", m) == "fresh_movers"

    def test_equity_routed_to_own_lane(self):
        m = {"symbol": "AAPL_USDT_SWAP", "range_pct": 1.0}
        assert LU._group("AAPL_USDT_SWAP", m) == "equity_proxy"

    def test_btc_eth_tactical_and_core(self):
        assert LU._group("BTC_USDT_SWAP", {"symbol": "BTC_USDT_SWAP", "range_pct": 2.0}) == "btc_eth_tactical"
        # a major (SOL) with low range -> core
        assert LU._group("SOL_USDT_SWAP", {"symbol": "SOL_USDT_SWAP", "range_pct": 3.0}) == "core"


class TestSelectAndFilters:
    def _tickers(self):
        return [
            _tk("BICO-USDT-SWAP", 0.2, 0.17, 0.28, 0.10, 1_300_000_000),   # fresh mover, liquid
            _tk("SOL-USDT-SWAP", 150, 147, 155, 146, 4_400_000),           # core, liquid
            _tk("AAPL-USDT-SWAP", 200, 199, 201, 198, 50_000),             # equity -> separate lane
            _tk("DEAD-USDT-SWAP", 1.0, 1.0, 1.0, 1.0, 100),                # illiquid (vol_usd $100)
            _tk("WIDE-USDT-SWAP", 1.0, 0.9, 1.2, 0.8, 20_000_000, ask=1.1, bid=0.9),  # wide spread
        ]

    def test_filters_and_equity_separation(self):
        r = LU.select_universe(self._tickers(), top_n_per_group=10)
        all_syms = {m["symbol"] for rows in r["selected"].values() for m in rows}
        assert "BICO_USDT_SWAP" in all_syms              # fresh mover kept
        assert "AAPL_USDT_SWAP" not in all_syms          # equity excluded from crypto
        assert any(m["symbol"] == "AAPL_USDT_SWAP" for m in r["equity_lane"])  # in its own lane
        assert "DEAD_USDT_SWAP" not in all_syms          # illiquid dropped
        assert r["dropped"]["illiquid"] >= 1 and r["dropped"]["wide_spread"] >= 1


class TestIntake:
    def test_intake_events_ranked_and_live_mover(self):
        r = LU.select_universe([_tk("BICO-USDT-SWAP", 0.2, 0.17, 0.28, 0.10, 1_300_000_000)], top_n_per_group=5)
        evs = LU.to_intake_events(r["selected"], now=1000.0)
        assert evs and evs[0]["reason"] == "live_mover" and evs[0]["source"] == "live_universe"
        assert evs[0]["symbol"] == "BICO_USDT_SWAP" and "group" in evs[0]["evidence"]
        # dedup: same call twice within window -> same event_id
        evs2 = LU.to_intake_events(r["selected"], now=1000.0)
        assert evs[0]["event_id"] == evs2[0]["event_id"]


class TestApplyIntake:
    def test_apply_registers_and_dedups(self, tmp_path):
        r = LU.select_universe([_tk("BICO-USDT-SWAP", 0.2, 0.17, 0.28, 0.10, 1_300_000_000)], top_n_per_group=5)
        events = LU.to_intake_events(r["selected"], now=1000.0)
        first = LU.apply_intake(tmp_path, events, now=1000.0)
        assert first["registered"] == len(events) and first["total"] == len(events)
        # the live_mover events are priority 1-3 (movers) -> consumed before the grind (priority 4)
        assert all(1 <= ev["priority"] <= 5 for ev in events)
        second = LU.apply_intake(tmp_path, events, now=1000.0)
        assert second["duplicate"] == len(events) and second["registered"] == 0  # idempotent


class TestNoExecutionPath:
    def test_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "live_universe_selector.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv", "hmac")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        assert "/market/tickers" in src and "OK-ACCESS" not in src and "passphrase" not in src
