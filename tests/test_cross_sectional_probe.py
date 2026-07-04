# -*- coding: utf-8 -*-
"""Cross-sectional RV: market-neutral spread, momentum vs reversal, significance verdict, research-only."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import cross_sectional_probe as XS  # noqa: E402


class TestStats:
    def test_positive_candidate_needs_significance(self):
        # mean high AND t>1.5 -> candidate
        strong = [0.4] * 40
        assert XS._stats(strong)["verdict"] == "positive_spread_candidate"

    def test_weak_or_zero(self):
        mixed = [0.3, -0.3] * 20      # mean ~0
        assert XS._stats(mixed)["verdict"] == "weak_or_zero"

    def test_negative(self):
        assert XS._stats([-0.5] * 30)["verdict"] == "negative"

    def test_underpowered(self):
        assert XS._stats([0.5] * 5)["verdict"] == "underpowered"


class TestSpread:
    def test_momentum_vs_reversal_are_opposite_sign(self):
        # 3 symbols: A trends up, C trends down, B flat -> momentum longs A/shorts C
        ts = list(range(0, 30))
        closes = {
            "A": {t: 100 * (1.02 ** t) for t in ts},   # strong up
            "B": {t: 100.0 for t in ts},                # flat
            "C": {t: 100 * (0.98 ** t) for t in ts},    # strong down
        }
        mom = XS._spread_returns(closes, lookback=3, hold=3, quantile=1, direction="momentum")
        rev = XS._spread_returns(closes, lookback=3, hold=3, quantile=1, direction="reversal")
        assert mom and rev
        # momentum (long winner A / short loser C) and reversal are mirror trades -> opposite-signed spreads
        import statistics
        assert statistics.mean(s for _, s in mom) * statistics.mean(s for _, s in rev) <= 0


class TestNoExecutionPath:
    def test_keyless_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "cross_sectional_probe.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
