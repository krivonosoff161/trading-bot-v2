# -*- coding: utf-8 -*-
"""Direction-filter probe: pooled-entry tercile separation analysis, no-look-ahead features, research-only."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import direction_filter_probe as DP  # noqa: E402


def _rows(feat, pairs):
    # pairs: list of (feat_value, net); all OOS
    return [{"oos": True, "net": net, feat: fv, "overext": fv, "run5": fv, "volratio": fv}
            for fv, net in pairs]


class TestAnalyze:
    def test_no_separation_when_both_terciles_negative(self):
        rows = _rows("overext", [(i, -0.5) for i in range(DP.MIN_OOS_ENTRIES + 10)])
        out = DP.analyze(rows)
        assert out["overext"]["verdict"] == "no_separation"

    def test_tilts_when_terciles_split_sign(self):
        # low feature -> negative net, high feature -> positive net => tilts_long_high
        n = DP.MIN_OOS_ENTRIES + 30
        pairs = [(i, (-1.0 if i < n // 3 else (1.0 if i > 2 * n // 3 else 0.0))) for i in range(n)]
        out = DP.analyze(_rows("overext", pairs))
        assert out["overext"]["verdict"] == "tilts_long_high"
        assert out["overext"]["separation_top_minus_low"] > 0

    def test_underpowered_below_min_entries(self):
        out = DP.analyze(_rows("overext", [(1, 1.0)] * 10))
        assert out["overext"]["verdict"] == "underpowered"


class TestVerdict:
    def test_feat_verdict_thresholds(self):
        assert DP._feat_verdict(-0.5, 0.5) == "tilts_long_high"
        assert DP._feat_verdict(0.5, -0.5) == "tilts_long_low"
        assert DP._feat_verdict(-0.1, -0.1) == "no_separation"
        assert DP._feat_verdict(0.05, 0.1) == "no_separation"   # both ~flat, no clear split


class TestNoLookahead:
    def test_features_use_only_pre_entry_bars(self):
        # entry at idx -> features computed at j=idx-1; verify None when not enough history
        candles = [{"ts": i, "open": 100, "high": 101, "low": 99, "close": 100, "vol": 10.0} for i in range(40)]
        closes = [100.0] * 40
        vseries = [10.0] * 40
        assert DP._entry_features(candles, 30, closes, vseries) is None   # j=29 < MA_LONG(50)


class TestNoExecutionPath:
    def test_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "direction_filter_probe.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
