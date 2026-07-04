# -*- coding: utf-8 -*-
"""Orderbook follow-through replay: no-look-ahead event->forward mid, bucketing, no order/private imports."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import micro_orderbook_replay as OR  # noqa: E402


def _snap(t, bids, asks):
    return {"recv_ms": t, "book": {"bids": bids, "asks": asks}}


class TestSeriesAndEvents:
    def test_series_drops_one_sided(self):
        snaps = [_snap(1, [["100", "1"]], [["100.1", "1"]]), _snap(2, [], [["100", "1"]])]
        s = OR._series(snaps)
        assert len(s) == 1 and s[0]["mid"] == 100.05

    def test_ask_heavy_event_short_followthrough(self):
        # ask-heavy imbalance (OBI<=-0.5) with a TIGHT spread (<5bps) at t0; price then falls.
        snaps = [_snap(0, [["100.00", "1"]], [["100.01", "9"]])]   # OBI=(1-9)/10=-0.8, spread ~1bps
        snaps += [_snap(30_000, [["99.50", "1"]], [["99.51", "1"]]),
                  _snap(60_000, [["99.00", "1"]], [["99.01", "1"]])]
        rows = OR._events_with_followthrough(OR._series(snaps))
        assert rows and rows[0]["side"] == "short"
        assert rows[0]["primary_net_pct"] is not None


class TestBucket:
    def test_needs_more_samples(self):
        assert OR._bucket([{"primary_net_pct": 1.0}] * 5) == "needs_more_samples"

    def test_weak_when_nonpositive(self):
        rows = [{"primary_net_pct": -0.1, "mfe_pct": 0.0} for _ in range(30)]
        assert OR._bucket(rows) == "weak_followthrough"

    def test_followthrough_observed_when_positive(self):
        rows = [{"primary_net_pct": 0.5, "mfe_pct": 1.0} for _ in range(30)]
        assert OR._bucket(rows) == "followthrough_observed"


class TestNoExecutionPath:
    def test_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "micro_orderbook_replay.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv", "signal_engine", "aiohttp")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        assert 'paper_forward_ready": True' not in src and "paper_forward_ready=True" not in src
