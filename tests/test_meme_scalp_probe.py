# -*- coding: utf-8 -*-
"""Meme 1m/5m scalp probe: scalp signals (no look-ahead), two-cost verdict, mirage guard, research-only."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import meme_scalp_probe as MS  # noqa: E402


def _c(o, h, low, cl, v=10.0):
    return {"ts": 0, "open": o, "high": h, "low": low, "close": cl, "vol": v}


class TestSignals:
    def test_vol_expansion_fade_fades_an_up_spike(self):
        candles = [_c(100, 100.5, 99.5, 100) for _ in range(20)]
        candles.append(_c(100, 110, 100, 109))   # oversized UP bar (range ~10% >> avg ~1%) -> fade short
        candles.append(_c(109, 110, 108, 109))
        sigs = MS.vol_expansion_fade(candles, 3.0)
        assert any(s["side"] == "short" and s["idx"] == 21 for s in sigs)   # entry next bar, fade up-spike

    def test_burst_momentum_rides_up_burst(self):
        candles = [_c(100, 101, 99, 100, v=10.0) for _ in range(20)]
        for p in (101, 102, 103):                 # 3 up bars
            candles.append(_c(p - 1, p + 0.5, p - 1.5, p, v=30.0))   # + volume surge
        candles.append(_c(103, 104, 102, 103))
        sigs = MS.burst_momentum(candles, 3, 1.8)
        assert any(s["side"] == "long" for s in sigs)

    def test_micro_breakout_long_on_new_high(self):
        candles = [_c(100, 101, 99, 100) for _ in range(20)]
        candles.append(_c(101, 103, 101, 102.5))  # closes above the prior 20-bar high (~101)
        candles.append(_c(102.5, 103, 102, 102.5))
        sigs = MS.micro_breakout(candles, 20)
        assert any(s["side"] == "long" for s in sigs)

    def test_no_lookahead_entry_is_next_bar(self):
        candles = [_c(100, 100.5, 99.5, 100) for _ in range(20)]
        candles.append(_c(100, 110, 100, 109))
        candles.append(_c(109, 110, 108, 109))
        assert all(s["idx"] == 21 for s in MS.vol_expansion_fade(candles, 3.0))


class TestVerdictAndSummary:
    def test_cost_levels_verdict(self):
        assert MS._verdict(0.04, 0.10) == "beats_taker_candidate"
        assert MS._verdict(-0.05, 0.03) == "needs_tight_execution"
        assert MS._verdict(-0.1, -0.05) == "cost_bound_dead"

    def test_summary_mirage_guard(self):
        # below MIN_OOS_TRADES or MIN_SYMBOLS -> not surfaced
        acc = {"a::5m": {"label": "a", "timeframe": "5m", "symbols": 2, "oos_gross": [0.2] * 100}}
        assert MS._summarize(acc)["by_cell"] == []           # only 2 symbols < MIN_SYMBOLS
        acc2 = {"a::5m": {"label": "a", "timeframe": "5m", "symbols": 6,
                          "oos_gross": [0.2] * (MS.MIN_OOS_TRADES + 5)}}
        cells = MS._summarize(acc2)["by_cell"]
        assert cells and cells[0]["net_taker_pct"] == round(0.2 - MS.TAKER_RT, 4)


class TestNoExecutionPath:
    def test_keyless_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "meme_scalp_probe.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
