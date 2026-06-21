# -*- coding: utf-8 -*-
"""Bounded discovery cycle: guarded steps (one failure doesn't kill the cycle), stop-file aware,
what-worked/failed synthesis. Research-only."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import discovery_cycle as DC  # noqa: E402


class TestGuardedStep:
    def test_step_records_ok_and_error_without_raising(self):
        steps = []
        DC._step("good", lambda: {"x": 1}, steps)
        DC._step("bad", lambda: (_ for _ in ()).throw(ValueError("boom")), steps)
        assert steps[0]["status"] == "ok" and steps[0]["result"] == {"x": 1}
        assert steps[1]["status"].startswith("error:ValueError") and "boom" in steps[1]["detail"]


class TestSynthesize:
    def test_routes_verdicts_to_worked_and_failed(self):
        steps = [{"step": "mover_validation", "status": "ok", "result": {"by_cell": [
            {"family": "momentum_breakout", "timeframe": "4h", "is_median_net": 0.4,
             "oos_median_net": 0.5, "verdict": "holds_oos_candidate"},
            {"family": "sfp_liquidity_sweep", "timeframe": "1h", "is_median_net": 0.3,
             "oos_median_net": -0.5, "verdict": "in_sample_only"},
        ]}}]
        out = DC._synthesize(steps)
        assert any("forward-watch" in w for w in out["worked"])
        assert any("overfit" in f["why"] for f in out["failed"])

    def test_step_errors_surface_in_failed(self):
        steps = [{"step": "live_universe", "status": "error:RuntimeError", "detail": "net"}]
        out = DC._synthesize(steps)
        assert any(f["what"] == "live_universe" for f in out["failed"])


class TestStopFile:
    def test_stop_file_skips_remaining_steps(self, tmp_path, monkeypatch):
        monkeypatch.setattr(DC, "is_stop_requested", lambda _r: True)
        rep = DC.run_cycle(tmp_path, limit_symbols=2, apply_intake=False, now=1000.0)
        assert all(s["status"] == "skipped_stop_file" for s in rep["steps"])
        assert rep["stopped"] is True


class TestNoExecutionPath:
    def test_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "discovery_cycle.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
