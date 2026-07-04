# -*- coding: utf-8 -*-
"""Microstructure feature math: pure, no-look-ahead, no live/order imports."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import micro_features as MF  # noqa: E402

_BOOK = {"bids": [[100.0, 3.0], [99.9, 10.0], [99.8, 1.0]],
         "asks": [[100.1, 1.0], [100.2, 2.0], [100.3, 1.0]]}


class TestOrderbook:
    def test_imbalance_and_spread(self):
        # bid_sum(3)=14, ask_sum(3)=4 -> (14-4)/18
        assert MF.orderbook_imbalance(_BOOK, depth=3) == round((14 - 4) / 18, 4)
        assert MF.spread_bps(_BOOK) > 0
        assert MF.mid_price(_BOOK) == 100.05

    def test_empty_book_safe(self):
        assert MF.orderbook_imbalance({}) == 0.0 and MF.spread_bps(None) == 0.0

    def test_liquidity_wall_picks_largest(self):
        w = MF.liquidity_wall(_BOOK, "bid")
        assert w["present"] and w["price"] == 99.9 and w["size"] == 10.0
        assert w["distance_bps"] > 0 and w["notional"] == round(99.9 * 10.0, 2)


class TestWallSequence:
    def test_persistence_and_spoof(self):
        w = lambda px, present=True: {"present": present, "price": px}  # noqa: E731
        seq = [w(99.9), w(99.9), w(99.9), w(99.9, present=False)]
        out = MF.wall_sequence_features(seq)
        assert out["spoof_cancel"] is True            # present then vanished
        seq2 = [w(99.9), w(99.91), w(99.92)]
        assert MF.wall_sequence_features(seq2)["movement"] == "up"


class TestTape:
    def _t(self, side, sz, ts=0):
        return {"ts_ms": ts, "side": side, "size": sz, "price": 100.0}

    def test_delta_and_pressure(self):
        trades = [self._t("buy", 3), self._t("sell", 1), self._t("buy", 2)]
        d = MF.tape_delta(trades)
        assert d["buy_vol"] == 5.0 and d["sell_vol"] == 1.0
        assert d["cvd_ratio"] == round(4 / 6, 4) and d["n_trades"] == 3
        assert MF.aggressive_pressure(trades) == round(4 / 6, 4)

    def test_speed(self):
        trades = [self._t("buy", 1, i) for i in range(10)]
        assert MF.tape_speed(trades, window_ms=1000) == 10.0  # 10 trades / 1s

    def test_gap_marker_ignored(self):
        trades = [self._t("buy", 1), {"ts_ms": 1, "side": "GAP", "size": 0}]
        assert MF.tape_delta(trades)["n_trades"] == 1  # GAP not counted as buy/sell


class TestNoExecutionPath:
    def test_pure_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "micro_features.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order", "live_engine", "auto_trade",
                     "credential", "dotenv", "aiohttp", "requests", "signal_engine")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
