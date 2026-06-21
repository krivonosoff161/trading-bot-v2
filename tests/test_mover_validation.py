# -*- coding: utf-8 -*-
"""Held-out OOS validation on movers: no-look-ahead IS/OOS split, honest verdicts, research-only."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import mover_validation as MV  # noqa: E402


class TestSplit:
    def test_split_is_by_entry_index_no_lookahead(self, monkeypatch):
        candles = [{"ts": i, "open": 100, "high": 101, "low": 99, "close": 100} for i in range(100)]
        # signals at idx 10 (IS) and idx 90 (OOS) with cut at 65
        monkeypatch.setattr(MV, "generate_signals",
                            lambda c, f, p: [{"idx": 10, "side": "long"}, {"idx": 90, "side": "long"}])
        monkeypatch.setattr(MV, "simulate_trades", lambda c, sigs, p, **k: [{"net_pct": 1.0} for _ in sigs])
        is_nets, oos_nets = MV._split_nets(candles, "f", {}, oos_frac=0.35)
        assert len(is_nets) == 1 and len(oos_nets) == 1   # one each side of cut=65


class TestVerdict:
    def test_holds_oos_candidate(self):
        cell = {"symbols": 6, "is_medians": [0.5] * 6, "oos_medians": [0.3] * 6, "oos_positive": 5}
        assert MV._verdict(cell) == "holds_oos_candidate"

    def test_in_sample_only_overfit(self):
        cell = {"symbols": 6, "is_medians": [0.5] * 6, "oos_medians": [-0.3] * 6, "oos_positive": 1}
        assert MV._verdict(cell) == "in_sample_only"

    def test_underpowered_few_symbols(self):
        cell = {"symbols": 2, "is_medians": [1.0, 1.0], "oos_medians": [1.0, 1.0], "oos_positive": 2}
        assert MV._verdict(cell) == "underpowered_few_symbols"


class TestNoExecutionPath:
    def test_no_forbidden_imports_and_never_paper_ready(self):
        src = (_ROOT / "src" / "research_lab" / "mover_validation.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        assert "paper_forward_ready" not in src or "nothing paper-ready" in src.lower()
