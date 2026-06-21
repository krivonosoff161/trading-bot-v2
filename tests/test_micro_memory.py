# -*- coding: utf-8 -*-
"""Microstructure outcome memory + orderbook event detector: no-look-ahead events, typed schema,
bucket vocabulary, no paper-ready. Research-only."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import micro_memory as MM  # noqa: E402


def _snap(bids, asks, ts=1, recv=1):
    return {"recv_ms": recv, "book": {"bids": bids, "asks": asks, "ts": str(ts)}}


class TestDetect:
    def test_ask_heavy_makes_short_event_with_schema(self):
        # ask-heavy book (OBI <= -0.5) near a tight spread -> short event, reason ok
        snaps = [_snap([["100", "1"]], [["100.1", "9"], ["100.2", "9"]])]
        evs = MM.detect_orderbook_events(snaps, "BTC-USDT-SWAP")
        assert len(evs) == 1
        ev = evs[0]
        assert ev["side"] == "short" and ev["symbol"] == "BTC-USDT-SWAP"
        assert set(ev) >= {"symbol", "ts", "side", "features", "threshold_version", "source", "reason"}
        assert "obi_top5" in ev["features"] and "paper_forward_ready" not in ev

    def test_balanced_book_no_event(self):
        snaps = [_snap([["100", "5"]], [["100.1", "5"]])]
        assert MM.detect_orderbook_events(snaps, "X") == []

    def test_wide_spread_flagged(self):
        # ask-heavy but very wide spread -> reason spread_too_wide (rejected, not traded)
        snaps = [_snap([["90", "1"]], [["100", "9"], ["101", "9"]])]
        evs = MM.detect_orderbook_events(snaps, "X")
        assert evs and evs[0]["reason"] == "spread_too_wide"


class TestBuckets:
    def test_vocabulary_and_needs_more_samples(self):
        assert "weak_followthrough" in MM.MICRO_BUCKETS and "fake_wall_cancel" in MM.MICRO_BUCKETS
        b = MM._orderbook_bucket([{"reason": "ok", "features": {}}] * 3)
        assert b["bucket"] == "needs_more_samples" and b["events"] == 3


class TestSummarizeAndWrite:
    def test_scan_and_write_events(self, tmp_path):
        import gzip
        import json
        d = tmp_path / "microstructure" / "recordings" / "live"
        d.mkdir(parents=True)
        with gzip.open(d / "BTC-USDT-SWAP.jsonl.gz", "wt", encoding="utf-8") as f:
            f.write(json.dumps(_snap([["100", "1"]], [["100.1", "9"], ["100.2", "9"]])) + "\n")
        events = MM.scan_recordings(tmp_path)
        assert events and events[0]["side"] == "short"
        p = MM.write_events(tmp_path, events)
        assert p.exists() and p.read_text(encoding="utf-8").strip()


class TestNoExecutionPath:
    def test_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "micro_memory.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv", "signal_engine")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        assert 'paper_forward_ready": True' not in src and "paper_forward_ready=True" not in src
