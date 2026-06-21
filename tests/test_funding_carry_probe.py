# -*- coding: utf-8 -*-
"""Funding-carry probe: delta-neutral harvest episodes, net-of-cost verdict, honest caveats, research-only."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import funding_carry_probe as FC  # noqa: E402


def _series(rates):
    return [(i * 8 * 3600000, r) for i, r in enumerate(rates)]


class TestEpisodes:
    def test_harvests_persistent_high_funding_until_flip(self):
        # high positive funding for several periods then a flip -> one episode that accrues then exits
        rates = [0.0004, 0.0004, 0.0004, 0.0004, 0.0004, -0.0004]
        eps = FC._episodes(_series(rates))
        assert len(eps) == 1
        assert eps[0]["periods"] >= 3                  # accrued the same-sign run
        assert eps[0]["gross"] > eps[0]["net"]          # net subtracts the round-trip cost

    def test_no_episode_when_below_threshold(self):
        rates = [0.0001] * 6                            # below ENTER_THRESH -> no entry
        assert FC._episodes(_series(rates)) == []

    def test_no_episode_without_persistence(self):
        # alternating sign -> never PERSIST same-sign periods before entry
        rates = [0.0004, -0.0004, 0.0004, -0.0004, 0.0004, -0.0004]
        assert FC._episodes(_series(rates)) == []

    def test_net_subtracts_round_trip(self):
        rates = [0.0005, 0.0005, 0.0005, 0.0005, -0.0005]   # 0.0005*4 = 0.0020 gross collected
        eps = FC._episodes(_series(rates))
        assert eps and abs(eps[0]["net"] - (eps[0]["gross"] - FC.ROUND_TRIP_COST)) < 1e-9


class TestSummary:
    def test_underpowered_below_min(self):
        s = FC._summarize({}, [{"net": 0.01}] * 5)
        assert s["verdict"] == "underpowered"

    def test_beats_cost_candidate(self):
        eps = [{"net": 0.003} for _ in range(FC.MIN_EPISODES + 5)]
        per = {"X": {"annualized_carry_pct": 50.0, "episode_net_positive_share": 0.9}}
        s = FC._summarize(per, eps)
        assert s["verdict"] == "carry_beats_cost_candidate" and s["episode_net_positive_share"] >= 0.55

    def test_below_cost(self):
        eps = [{"net": -0.001} for _ in range(FC.MIN_EPISODES + 5)]
        assert FC._summarize({"X": {"annualized_carry_pct": 1.0}}, eps)["verdict"] == "carry_below_cost"


class TestNoExecutionPath:
    def test_keyless_no_forbidden_imports(self):
        src = (_ROOT / "src" / "research_lab" / "funding_carry_probe.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv", "hmac")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        assert "paper_forward_ready" not in src or "nothing paper-ready" in src.lower()
