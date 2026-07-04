# -*- coding: utf-8 -*-
"""Three-mode (naive/realistic/strict) cost decomposition of the ledger: read-only, costs-as-killer,
maker unlock is a hypothesis not edge, nothing paper-ready."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import cost_mode_analysis as CMA  # noqa: E402


def _row(net, oos, n=20, hard="", family="f", tf="4h"):
    return {"family": family, "timeframe": tf, "symbol": "X", "n_trades": n,
            "avg_net_pct": net, "test_avg_net_pct": oos, "hard_status": hard}


class TestClassify:
    def test_naive_vs_realistic_vs_cost(self):
        # gross +0.05 (net -0.05 + taker 0.10) -> naive yes, realistic no, maker yes (-0.05+0.08=+0.03)
        c = CMA.classify_row(_row(-0.05, -0.05))
        assert c["naive"] is True and c["real"] is False and c["maker"] is True

    def test_strict_requires_hard_pass(self):
        assert CMA.classify_row(_row(1.0, 1.0, hard="PAPER_FORWARD_READY"))["strict"] is True
        assert CMA.classify_row(_row(1.0, 1.0, hard="FAILED_OOS"))["strict"] is False

    def test_oos_split_kills_real(self):
        # in-sample net+, OOS net- -> real_is True but real False
        c = CMA.classify_row(_row(0.5, -0.5))
        assert c["real_is"] is True and c["real"] is False


class TestVerdict:
    def test_weak_generator(self):
        a = {"n": 100, "naive": 20, "real": 5, "real_is": 8, "maker": 6, "strict": 0}
        assert CMA._verdict(a) == "weak_generator"

    def test_cost_bound_beats_stray_strict(self):
        # strong maker unlock + a couple strict passes -> still cost_bound (honest dominant story)
        a = {"n": 1000, "naive": 800, "real": 200, "real_is": 400, "maker": 700, "strict": 2}
        assert CMA._verdict(a) == "cost_bound"

    def test_underpowered_tactical(self):
        a = {"n": 50, "naive": 30, "real": 15, "real_is": 18, "maker": 16, "strict": 0}
        assert CMA._verdict(a) == "underpowered_tactical"


class TestAnalyzeShape:
    def test_aggregate_funnel_consistency(self):
        rows = [_row(-0.05, -0.05, family="a"), _row(0.5, 0.5, family="a", n=5),
                _row(-1.0, -1.0, family="b")]
        out = CMA._aggregate(rows, lambda r: r["family"])
        a = next(x for x in out if x["key"] == "a")
        assert a["n"] == 2 and a["maker_unlock"] >= 0
        assert "verdict" in a


class TestNoExecutionPath:
    def test_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "cost_mode_analysis.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        assert "paper_forward_ready" not in src.lower() or "never" in src.lower()
