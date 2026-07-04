# -*- coding: utf-8 -*-
"""Strategy library: readable join, check-before-compute lookup, research-only."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import strategy_library as SL  # noqa: E402


def _row(symbol, tf, family, ph, outcome="UNCHARACTERIZED", forward=""):
    return {"symbol": symbol, "timeframe": tf, "family": family, "params_hash": ph,
            "universe_source": "grind", "exit_mode": "baseline",
            "result": {"n_trades": 5, "net": 0.0, "outcome_class": outcome, "cost_class": "cost_ok",
                       "exit_recovered_delta": None},
            "failure_reason": "", "forward_status": forward, "paper_forward_ready": False}


class TestLookup:
    def test_fresh_when_unseen(self):
        assert SL.lookup([], symbol="X", timeframe="1h", family="f")["action"] == "fresh"

    def test_forward_watch_for_lead(self):
        lib = [_row("X", "1h", "f", "p", forward="TACTICAL_LEAD")]
        assert SL.lookup(lib, symbol="X", timeframe="1h", family="f")["action"] == "forward_watch"

    def test_skip_known_bad(self):
        lib = [_row("X", "1h", "f", "p", outcome="CONFIRMED_BAD")]
        assert SL.lookup(lib, symbol="X", timeframe="1h", family="f")["action"] == "skip_known_bad"

    def test_revisit_otherwise(self):
        lib = [_row("X", "1h", "f", "p", outcome="WRONG_EXIT")]
        assert SL.lookup(lib, symbol="X", timeframe="1h", family="f")["action"] == "revisit"

    def test_params_hash_narrows(self):
        lib = [_row("X", "1h", "f", "p1", forward="TACTICAL_LEAD")]
        assert SL.lookup(lib, symbol="X", timeframe="1h", family="f", params_hash="p2")["action"] == "fresh"


class TestSummary:
    def test_counts_sources_and_forward(self):
        rows = [_row("A", "1h", "f", "p", forward="TACTICAL_LEAD"),
                _row("B", "4h", "g", "q", outcome="CONFIRMED_BAD")]
        rows[0]["universe_source"] = "live_mover"
        s = SL.summarize(rows)
        assert s["total"] == 2 and s["by_universe_source"]["live_mover"] == 1
        assert s["by_forward_status"]["TACTICAL_LEAD"] == 1


class TestNoExecutionPath:
    def test_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "strategy_library.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
