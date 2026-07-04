# -*- coding: utf-8 -*-
"""Fade validation gates: slippage sensitivity, walk-forward, loss tail, deflated significance."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import fade_validation as FV  # noqa: E402


class TestSlippage:
    def test_net_subtracts_fees_and_spike_proportional_slip(self):
        rows = [{"gross": 1.0, "spike_range": 5.0, "block": 0}]
        # net = gross - fees - slip_mult*spike_range
        assert FV._net(rows, 0.0)[0] == 1.0 - FV.FEES_RT
        assert abs(FV._net(rows, 0.02)[0] - (1.0 - FV.FEES_RT - 0.02 * 5.0)) < 1e-9


class TestVerdict:
    def test_dies_under_slippage(self):
        slip = {0.0: 0.05, 0.02: -0.01}     # negative at realistic slip
        assert FV._verdict(slip, 5, 5, 0.01, 0.02) == "dies_under_realistic_slippage"

    def test_not_time_robust(self):
        slip = {0.0: 0.1, 0.02: 0.05}
        assert FV._verdict(slip, 1, 5, 0.01, 0.05) == "not_time_robust"   # only 1/5 blocks positive

    def test_not_significant_after_deflation(self):
        slip = {0.0: 0.1, 0.02: 0.05}
        assert FV._verdict(slip, 4, 5, 0.9, 0.05) == "not_significant_after_deflation"

    def test_survives_all(self):
        slip = {0.0: 0.1, 0.02: 0.05}
        assert FV._verdict(slip, 4, 5, 0.01, 0.05) == "survives_all_gates_paper_forward_candidate"


class TestAnalyze:
    def test_underpowered(self):
        assert FV.analyze([{"gross": 1, "spike_range": 1, "block": 0}] * 5)["verdict"] == "underpowered"

    def test_full_shape(self):
        rows = [{"gross": (0.5 if i % 2 else -0.4), "spike_range": 2.0, "block": i % FV.N_BLOCKS}
                for i in range(120)]
        a = FV.analyze(rows)
        assert "slippage_curve_net" in a and "walk_forward_by_block" in a
        assert "max_drawdown_pct_sum" in a and "p_sidak_deflated" in a and a["trades"] == 120


class TestNoExecutionPath:
    def test_keyless_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "fade_validation.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
