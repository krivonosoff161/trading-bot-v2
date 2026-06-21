# -*- coding: utf-8 -*-
"""Micro-scalp search: entry triggers (fades, no look-ahead) x exit speeds, mirage-guarded, research-only."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import microscalp_search as MS  # noqa: E402


def _c(o, h, low, cl):
    return {"ts": 0, "open": o, "high": h, "low": low, "close": cl, "vol": 10.0}


class TestEntryTriggers:
    def test_range_fade_fades_up_spike(self):
        candles = [_c(100, 100.5, 99.5, 100) for _ in range(20)]
        candles.append(_c(100, 110, 100, 109))           # oversized up bar -> fade short
        assert MS._range_fade(candles, 20, 20, 3.0) == "short"

    def test_body_fade_fades_down_thrust(self):
        candles = [_c(100, 100.5, 99.5, 100) for _ in range(20)]
        candles.append(_c(100, 100, 90, 91))             # big down body -> fade long
        assert MS._body_fade(candles, 20, 20, 3.0) == "long"

    def test_wick_fade_on_rejection(self):
        # long UPPER wick -> rejection of higher prices -> fade short
        assert MS._wick_fade([_c(100, 110, 99.5, 100.5)], 0, 0, 2.0) == "short"
        # long LOWER wick -> fade long
        assert MS._wick_fade([_c(100, 100.5, 90, 99.5)], 0, 0, 2.0) == "long"

    def test_nbar_fade_on_exhaustion(self):
        up = [_c(100 + i, 100.5 + i, 99.5 + i, 100.5 + i) for i in range(5)]   # 5 up bars
        assert MS._nbar_fade(up, 4, 4, 0) == "short"


class TestExitSpeeds:
    def _candles(self):
        # entry at idx1 open=100; price drifts down then up
        return [_c(100, 100, 100, 100), _c(100, 101, 99, 99.5), _c(99.5, 100.5, 99, 100.4),
                _c(100.4, 101, 100, 100.8)]

    def test_close_1_is_fastest(self):
        g = MS._exit_gross(self._candles(), 1, "short", {"kind": "nclose", "n": 1})
        assert g is not None   # exits at close of the entry bar

    def test_first_green_exits_on_first_profit(self):
        g = MS._exit_gross(self._candles(), 1, "short", {"kind": "first_green", "max_hold": 5})
        assert g is not None and g > 0   # short profits as price dipped below entry

    def test_tp_sl_barrier(self):
        g = MS._exit_gross(self._candles(), 1, "short", {"kind": "tp_sl", "tp": 0.5, "sl": 0.5, "max_hold": 3})
        assert g is not None


class TestSummary:
    def test_mirage_guard(self):
        small = {"a::5m": {"label": "a", "tf": "5m", "syms": {"X", "Y"}, "oos": [0.2] * 100}}
        assert MS._summarize(small)["ranked"] == []        # < MIN_SYMBOLS
        ok = {"a::5m": {"label": "a", "tf": "5m", "syms": {f"S{i}" for i in range(6)},
                        "oos": [0.2] * (MS.MIN_OOS_TRADES + 5)}}
        r = MS._summarize(ok)["ranked"]
        assert r and r[0]["net_taker"] == round(0.2 - MS.TAKER_RT, 4)


class TestNoExecutionPath:
    def test_keyless_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "microscalp_search.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
