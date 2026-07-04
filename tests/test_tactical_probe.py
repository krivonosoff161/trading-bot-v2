# -*- coding: utf-8 -*-
"""Tactical-probe: characterizes sub-power net-positive setups WITHOUT calling them edge.
Verifies the family-skew verdicts and the meat-grinder answer, and that nothing is paper-ready."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import tactical_probe as TP  # noqa: E402


def _row(family, n, net, tf="4h", regime=""):
    return {"family": family, "n_trades": n, "avg_net_pct": net, "timeframe": tf, "regime_bucket": regime}


class TestThinFilter:
    def test_only_sub_power(self):
        rows = [_row("f", 1, 1.0), _row("f", 9, 1.0), _row("f", 10, 1.0), _row("f", 0, 1.0)]
        thin = TP._thin(rows)
        assert all(1 <= r["n_trades"] < TP.POWER_FLOOR for r in thin)
        assert len(thin) == 2  # n=1 and n=9 only


class TestFamilySkew:
    def test_three_verdicts(self):
        # coherent positive skew family
        pos_fam = [_row("mrf", 2, 1.0) for _ in range(13)] + [_row("mrf", 2, -1.0) for _ in range(3)]
        # coin-flip family
        coin = [_row("bb", 3, 1.0) for _ in range(10)] + [_row("bb", 3, -1.0) for _ in range(10)]
        # mostly-negative family (gate correct)
        neg = [_row("mb", 4, 1.0) for _ in range(2)] + [_row("mb", 4, -1.0) for _ in range(15)]
        skew = {s["family"]: s["verdict"] for s in TP._family_skew(pos_fam + coin + neg)}
        assert skew["mrf"] == "thin_positive_skew"
        assert skew["bb"] == "noise_consistent"
        assert skew["mb"] == "gate_correct_reject"

    def test_insufficient_when_small(self):
        small = [_row("x", 2, 1.0) for _ in range(4)]
        assert TP._family_skew(small)[0]["verdict"] == "insufficient"


class TestVerdict:
    def test_meat_grinder_answer_flags_probe_families(self):
        v = TP._verdict(0.51, ["mean_reversion_fade"])
        assert "tactical probe" in v and "NOT edge" in v
        v2 = TP._verdict(0.5, [])
        assert "structurally correct" in v2

    def test_build_is_research_only(self, monkeypatch):
        sample = ([_row("mrf", 2, 1.0) for _ in range(13)] + [_row("mrf", 2, -1.0) for _ in range(3)]
                  + [_row("mb", 4, -1.0) for _ in range(12)])
        monkeypatch.setattr(TP, "derive_setup_lifecycle", lambda _root: sample)
        probe = TP.build_tactical_probe(Path("."))
        assert probe["paper_forward_ready"] is False
        assert probe["probe_families"] == ["mrf"]
        assert "tactical probe" in probe["meat_grinder_verdict"]
