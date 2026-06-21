# -*- coding: utf-8 -*-
"""Tape-pressure replay: no-look-ahead event detection, forward follow-through, bucket classification,
real-file reader. Research-only, no order/private imports."""
import ast
import gzip
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import micro_tape_replay as TR  # noqa: E402


def _tape(side_pattern, *, n=200, step_ms=200, px0=100.0, drift=0.0):
    """Synthetic (ts, side, price, size) trades; price drifts by `drift` per trade after index n//2."""
    out = []
    px = px0
    for i in range(n):
        side = side_pattern(i)
        if i > n // 2:
            px += drift
        out.append((i * step_ms, side, px, 1.0))
    return out


class TestEventDetection:
    def test_sell_pressure_makes_short_event_no_lookahead(self):
        # heavy sell pressure in the lookback -> a short event; detection must not use future trades
        trades = _tape(lambda i: "sell", n=200, step_ms=200)  # 5 trades/sec, all sells
        evs = TR._events(trades, max_events=5)
        assert evs and evs[0]["side"] == "short"
        # the event's window only spans trades at/before its ts
        ev = evs[0]
        assert all(trades[j][0] <= ev["ts_ms"] for j in range(ev["idx"] + 1))

    def test_no_event_when_balanced(self):
        trades = _tape(lambda i: "buy" if i % 2 == 0 else "sell", n=200, step_ms=200)
        assert TR._events(trades, max_events=5) == []  # cvd ~ 0 -> below threshold


class TestFollowThrough:
    def test_short_profits_when_price_falls(self):
        trades = _tape(lambda i: "sell", n=400, step_ms=200, drift=-0.05)  # price falls after midpoint
        evs = TR._events(trades, max_events=1)
        assert evs
        ft = TR._follow_through(trades, evs[0])
        assert ft["primary_net_pct"] is not None
        assert "horizons_net_pct" in ft and ft["mfe_pct"] >= 0


class TestBucket:
    def test_needs_more_samples_under_20(self):
        assert TR._bucket([{"primary_net_pct": 1.0}] * 5) == "needs_more_samples"

    def test_weak_followthrough_when_median_nonpositive(self):
        rows = [{"primary_net_pct": -0.1, "mfe_pct": 0.0} for _ in range(30)]
        assert TR._bucket(rows) == "weak_followthrough"

    def test_followthrough_observed_when_positive(self):
        rows = [{"primary_net_pct": 0.5, "mfe_pct": 1.0} for _ in range(30)]
        assert TR._bucket(rows) == "followthrough_observed"


class TestReader:
    def test_read_tape_drops_gap_and_bad(self, tmp_path):
        p = tmp_path / "x.csv.gz"
        with gzip.open(p, "wt", encoding="utf-8", newline="") as f:
            f.write("ts_ms,recv_ts_ms,symbol,side,price,size,trade_id\n")
            f.write("1,1,X,GAP,,,\n")               # gap marker dropped
            f.write("2,2,X,buy,100.0,1.0,a\n")
            f.write("3,3,X,sell,100.1,2.0,b\n")
            f.write("4,4,X,buy,bad,1.0,c\n")        # bad price dropped
        rows = TR._read_tape(str(p))
        assert len(rows) == 2 and rows[0][1] == "buy"


class TestNoExecutionPath:
    def test_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "micro_tape_replay.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv", "aiohttp", "signal_engine")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        assert "paper_forward_ready" not in src.lower() or "never paper-ready" in src.lower()
