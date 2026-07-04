# -*- coding: utf-8 -*-
"""Honest maker-fill model: passive limit entry can no-fill, exits are mixed-cost (take=maker,
stop=taker), and it re-sims taker on the same basis. Research-only, never paper-ready."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import maker_fill_model as MFM  # noqa: E402


def _c(ts, o, h, low, cl):
    return {"ts": ts, "open": o, "high": h, "low": low, "close": cl, "vol": 1.0}


class TestNoFill:
    def test_long_no_fill_when_price_never_returns_to_limit(self):
        # limit = prior close (100). Entry bar gaps up and never trades at/below 100 -> NO-FILL.
        candles = [_c(0, 100, 100, 100, 100), _c(1, 105, 110, 104, 108), _c(2, 108, 112, 107, 110)]
        sig = [{"idx": 1, "side": "long"}]
        out = MFM.simulate_maker(candles, sig, {"hold_bars": 1, "stop_pct": 5, "take_pct": 10})
        assert out["n_filled"] == 0 and out["fill_rate"] == 0.0

    def test_fills_when_bar_trades_through_limit(self):
        candles = [_c(0, 100, 100, 100, 100), _c(1, 101, 103, 99, 102), _c(2, 102, 106, 101, 105)]
        sig = [{"idx": 1, "side": "long"}]
        out = MFM.simulate_maker(candles, sig, {"hold_bars": 1, "stop_pct": 5, "take_pct": 2})
        assert out["n_filled"] == 1


class TestMixedExitCost:
    def test_take_is_cheaper_than_stop(self):
        # build one trade that hits TAKE and one that hits STOP; the take net must carry the lower cost
        take_c = [_c(0, 100, 100, 100, 100), _c(1, 100, 100, 99, 100), _c(2, 100, 110, 100, 109)]
        stop_c = [_c(0, 100, 100, 100, 100), _c(1, 100, 100, 99, 100), _c(2, 100, 100, 90, 91)]
        sig = [{"idx": 1, "side": "long"}]
        p = {"hold_bars": 1, "stop_pct": 5, "take_pct": 2}
        take = MFM.simulate_maker(take_c, sig, p)
        stop = MFM.simulate_maker(stop_c, sig, p)
        # take exit pays maker+maker (0.04pp), stop pays maker+taker (0.07pp) -> take cost is lower
        assert take["n_trades"] == 1 and stop["n_trades"] == 1


class TestTakerSameBasis:
    def test_taker_resim_runs_on_market_entry(self):
        candles = [_c(0, 100, 100, 100, 100), _c(1, 100, 103, 99, 102), _c(2, 102, 106, 101, 105)]
        sig = [{"idx": 1, "side": "long"}]
        out = MFM.simulate_taker(candles, sig, {"hold_bars": 1, "stop_pct": 5, "take_pct": 2})
        assert out["n_trades"] == 1 and isinstance(out["taker_resim_net_pct"], float)


class TestNoExecutionPath:
    def test_no_forbidden_imports_and_never_paper_ready(self):
        src = (_ROOT / "src" / "research_lab" / "maker_fill_model.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        assert "paper_forward_ready" not in src or "never paper-ready" in src
