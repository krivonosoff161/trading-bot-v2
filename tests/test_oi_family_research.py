# -*- coding: utf-8 -*-
"""OI-family bounded research: runs OI families on OI-enriched DENSE timeframes only (1h/4h; 15m
delta_coarse excluded), in a SEPARATE oi_* class, honest-validated, never paper-ready."""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.oi_family_research import (  # noqa: E402
    DENSE_TFS,
    DIAG_TF,
    OI_FAMILIES,
    _required_data_present,
    plan_oi_family_research,
    run_oi_family_one,
    summarize_oi_diagnostic_15m,
    summarize_oi_family,
)


class TestGating:
    def test_dense_timeframes_exclude_15m(self):
        assert "15m" not in DENSE_TFS and set(DENSE_TFS) == {"1h", "4h"}

    def test_required_data_present_needs_oi(self):
        no_oi = [{"ts": 0, "open": 1, "high": 1, "low": 1, "close": 1}]
        with_oi = [{"ts": 0, "open": 1, "high": 1, "low": 1, "close": 1, "oi": 1000.0}]
        assert _required_data_present(no_oi, "oi_price_quadrant") is False
        assert _required_data_present(with_oi, "oi_price_quadrant") is True


class TestPlan:
    def test_plan_structure(self, tmp_path):
        p = plan_oi_family_research(tmp_path, limit=12)
        assert p["dense_timeframes"] == list(DENSE_TFS)
        assert "15m" in p["excluded"] and "delta_coarse" in p["excluded"]["15m"]
        assert set(p["oi_families"]) == set(OI_FAMILIES)


class TestSummary:
    def test_separate_oi_class_and_skips(self):
        rows = [
            {"outcome_class": "oi_failed_oos", "hard_status": "FAILED_OOS"},
            {"outcome_class": "oi_needs_more_data", "hard_status": "NEEDS_MORE_DATA"},
            {"skipped": "oi_data_absent"},
            {"skipped": "no_signals"},
        ]
        s = summarize_oi_family(rows)
        assert s["evaluated"] == 2 and s["skipped_total"] == 2
        assert s["skipped"]["oi_data_absent"] == 1
        assert s["honest_passed"] == 0
        assert all(k.startswith("oi_") for k in s["by_class"])  # separate namespace


class TestDiagnostic15m:
    def test_diag_tf_is_15m_and_summary_labels_diagnostic(self):
        assert DIAG_TF == "15m"
        rows = [
            {"outcome_class": "oi_diag_failed_costs", "diagnostic": True, "paper_forward_ready": False},
            {"outcome_class": "oi_diag_regime_only", "diagnostic": True, "paper_forward_ready": False},
            {"skipped": "no_signals"},
        ]
        s = summarize_oi_diagnostic_15m(rows)
        assert s["timeframe"] == "15m" and s["evaluated"] == 2
        assert all(k.startswith("oi_diag_") for k in s["by_class"])  # diagnostic namespace
        assert "DIAGNOSTIC" in s["note"] and "never edge" in s["note"]


class TestRunOne:
    def _enriched(self, tmp_path, symbol="X", tf="1h", n=80):
        d = tmp_path / "market_data" / tf
        d.mkdir(parents=True, exist_ok=True)
        step = 3600000
        rows = []
        for i in range(n):
            px = 100 + (i % 7) - 3  # mild oscillation so a mean/quadrant signal can fire
            rows.append({"ts": i * step, "date": "", "open": px, "high": px + 1, "low": px - 1,
                         "close": px, "vol": 10.0, "oi": 1000.0 + (i % 5) * 10})
        p = d / f"{symbol}_{rows[0]['ts']}_{rows[-1]['ts']}_{tf}.json"
        p.write_text(json.dumps(rows), encoding="utf-8")
        return p

    def test_run_one_is_research_only(self, tmp_path):
        self._enriched(tmp_path, "X", "1h")
        res = run_oi_family_one(tmp_path, family="oi_price_quadrant", symbol="X", timeframe="1h")
        # either it produced a separate oi_* class result, or honestly skipped (no signals) — never paper
        assert res.get("paper_forward_ready", False) is False
        if "outcome_class" in res:
            assert res["outcome_class"].startswith("oi_")
        else:
            assert res["skipped"] in ("no_signals", "oi_data_absent", "no_file")

    def test_run_one_skips_when_oi_absent(self, tmp_path):
        # candle file without an oi field -> honestly skipped, not run
        d = tmp_path / "market_data" / "1h"
        d.mkdir(parents=True, exist_ok=True)
        rows = [{"ts": i * 3600000, "date": "", "open": 100, "high": 101, "low": 99, "close": 100, "vol": 1.0}
                for i in range(80)]
        (d / "Y_0_1_1h.json").write_text(json.dumps(rows), encoding="utf-8")
        res = run_oi_family_one(tmp_path, family="oi_price_quadrant", symbol="Y", timeframe="1h")
        assert res["skipped"] == "oi_data_absent"
