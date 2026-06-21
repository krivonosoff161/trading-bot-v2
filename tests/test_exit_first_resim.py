# -*- coding: utf-8 -*-
"""Exit-first re-sim across the whole wrong-exit pool: MFE/capture pool filter, outcome classification,
research-only (no paper-ready, no order/private imports)."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import exit_first_resim as EF  # noqa: E402


def _rec(family, sym, tf, mfe, cap, uc="uc"):
    return {"uc_key": uc, "symbol": sym, "timeframe": tf, "family": family,
            "avg_mfe_pct": mfe, "avg_capture_ratio": cap}


class TestPool:
    def test_wrong_exit_signature_filter(self, monkeypatch):
        recs = [
            _rec("momentum_breakout", "A", "1h", 2.0, 0.1, "u1"),   # MFE>1 & capture<0.3 -> in
            _rec("bb_volume_fade", "B", "4h", 0.5, 0.1, "u2"),       # MFE<1 -> out
            _rec("mean_reversion_fade", "C", "15m", 3.0, 0.5, "u3"),  # capture>=0.3 -> out
            _rec("momentum_breakout", "D", "1h", 1.5, 0.0, "u4"),    # capture==0 -> out (not >0)
        ]
        monkeypatch.setattr(EF, "build_memory_index", lambda _root: recs)
        pool = EF.wrong_exit_pool(Path("."))
        assert [p["uc_key"] for p in pool] == ["u1"]

    def test_families_filter(self, monkeypatch):
        recs = [_rec("momentum_breakout", "A", "1h", 2.0, 0.1, "u1"),
                _rec("bb_volume_fade", "B", "1h", 2.0, 0.1, "u2")]
        monkeypatch.setattr(EF, "build_memory_index", lambda _root: recs)
        pool = EF.wrong_exit_pool(Path("."), families=("bb_volume_fade",))
        assert [p["uc_key"] for p in pool] == ["u2"]


class TestSummarize:
    def test_classes_and_modes(self):
        rows = [
            {"family": "f", "outcome_class": "exit_recovered_candidate", "best_exit": "early_tp", "delta": 2.0},
            {"family": "f", "outcome_class": "exit_recovered_candidate", "best_exit": "hold_long", "delta": 1.0},
            {"family": "g", "outcome_class": "still_bad", "best_exit": "baseline", "delta": -0.1},
            {"family": "g", "outcome_class": "thin_noise", "best_exit": "baseline", "delta": 0.0},
        ]
        s = EF._summarize(rows)
        assert s["exit_recovered_candidate"] == 2 and s["exit_still_bad"] == 1 and s["thin_noise"] == 1
        assert s["best_exit_modes"] == {"early_tp": 1, "hold_long": 1}
        assert s["top_recovered"][0]["delta"] == 2.0  # sorted by delta desc


class TestNoExecutionPath:
    def test_no_forbidden_imports_and_never_paper_ready(self):
        src = (_ROOT / "src" / "research_lab" / "exit_first_resim.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        assert 'paper_forward_ready": True' not in src and "paper_forward_ready=True" not in src
