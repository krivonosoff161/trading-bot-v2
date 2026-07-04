# -*- coding: utf-8 -*-
"""Tactical track: parallel verdict lane, NO_EVENT != bad, leads are forward-watch only, never paper-ready."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import tactical_track as TT  # noqa: E402


def _r(n, net, mfe=0.0, cap=0.0, shadow=None):
    return {"n_trades": n, "baseline_net": net, "avg_mfe_pct": mfe, "avg_capture_ratio": cap,
            "shadow_status": shadow}


class TestVerdict:
    def test_no_event_is_not_bad(self):
        assert TT.tactical_verdict(_r(2, 0.0)) == "NO_EVENT"
        assert TT.tactical_verdict(_r(0, -5.0)) == "NO_EVENT"   # n<3 -> NO_EVENT even if net would be neg

    def test_known_bad_needs_power(self):
        assert TT.tactical_verdict(_r(20, -0.3)) == "KNOWN_BAD"
        assert TT.tactical_verdict(_r(5, -0.3)) != "KNOWN_BAD"  # under power floor -> not known_bad

    def test_exit_problem(self):
        assert TT.tactical_verdict(_r(15, 0.0, mfe=2.0, cap=0.1)) == "EXIT_PROBLEM"

    def test_tactical_lead_vs_underpowered(self):
        # thin positive with good capture -> lead; with poor capture -> underpowered positive
        assert TT.tactical_verdict(_r(4, 3.0, mfe=4.0, cap=0.7)) == "TACTICAL_LEAD"
        assert TT.tactical_verdict(_r(4, 1.0, mfe=4.0, cap=0.2)) == "UNDERPOWERED_POSITIVE"

    def test_shadow_forward_takes_priority(self):
        assert TT.tactical_verdict(_r(4, 3.0, cap=0.9, shadow="shadow_forward_candidate")) == "SHADOW_FORWARD"


class TestSummary:
    def test_counts_and_no_paper_ready(self, monkeypatch):
        recs = [_r(2, 0.0), _r(20, -0.5), _r(4, 3.0, mfe=4.0, cap=0.7), _r(4, 1.0, cap=0.2)]
        # build_track reads build_memory_index -> monkeypatch it
        for r in recs:
            r.update({"uc_key": "u", "symbol": "S", "timeframe": "1h", "family": "f"})
        monkeypatch.setattr(TT, "build_memory_index", lambda _root: recs)
        rows = TT.build_track(Path("."))
        s = TT.summarize(rows)
        assert s["no_event"] == 1 and s["known_bad"] == 1 and s["tactical_leads"] == 1
        assert s["paper_ready_leak"] == 0                       # invariant
        assert all(r["paper_forward_ready"] is False for r in rows)
        assert s["forward_watch"] >= 1                          # the lead is forward-watch


class TestNoExecutionPath:
    def test_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "tactical_track.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        assert 'paper_forward_ready": True' not in src and "paper_forward_ready=True" not in src
