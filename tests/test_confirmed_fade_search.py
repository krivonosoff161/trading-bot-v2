# -*- coding: utf-8 -*-
"""Confirmed-fade: candlestick/TA confirmations, per-TF horizon stop, lift-vs-none, research-only."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import confirmed_fade_search as CF  # noqa: E402


def _c(o, h, low, cl):
    return {"ts": 0, "open": o, "high": h, "low": low, "close": cl, "vol": 10.0}


class TestCandlestick:
    def test_pin_bar_rejection(self):
        # up-spike with a long upper wick -> short rejection confirmed
        assert CF._pin_bar([_c(100, 110, 99.8, 100.5)], 0, "short") is True
        assert CF._pin_bar([_c(100, 100.6, 90, 99.5)], 0, "long") is True

    def test_engulfing(self):
        candles = [_c(100, 101, 99, 100.8), _c(101, 101, 98, 98.5)]   # bearish engulf of prior up body
        assert CF._engulfing(candles, 1, "short") is True

    def test_doji(self):
        assert CF._doji([_c(100, 102, 98, 100.05)], 0, "short") is True   # tiny body, big range
        assert CF._doji([_c(100, 101, 99, 100.9)], 0, "short") is False


class TestTechnical:
    def test_rsi_extreme(self):
        up = [{"open": 100, "high": 101, "low": 99, "close": 100 + i} for i in range(20)]  # rising -> RSI high
        assert CF._rsi_extreme(up, 19, "short") is True

    def test_bb_break(self):
        flat = [_c(100, 100.5, 99.5, 100) for _ in range(20)]
        flat.append(_c(100, 106, 100, 105))               # far above the band -> short break
        assert CF._bb_break(flat, 20, "short") is True


class TestHorizonAndSummary:
    def test_horizons_match_owner_gradation(self):
        assert CF.HORIZON_BARS == {"1m": 5, "5m": 24, "15m": 28, "1h": 36, "1d": 5}

    def test_lift_vs_none_computed(self):
        acc = {
            "none::5m": {"confirm": "none", "tf": "5m", "syms": {f"S{i}" for i in range(6)},
                         "oos": [0.10] * 40},
            "bb_break::5m": {"confirm": "bb_break", "tf": "5m", "syms": {f"S{i}" for i in range(6)},
                             "oos": [0.15] * 40},
        }
        ranked = CF._summarize(acc)["ranked"]
        bb = next(c for c in ranked if c["confirm"] == "bb_break")
        assert bb["lift_net_vs_none"] == round((0.15 - CF.TAKER_RT) - (0.10 - CF.TAKER_RT), 4)


class TestNoExecutionPath:
    def test_keyless_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "confirmed_fade_search.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
