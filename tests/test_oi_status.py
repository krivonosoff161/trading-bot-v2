# -*- coding: utf-8 -*-
"""Honest OI coverage status: keyless-public measurement, no fake-pass, no eternal pending, and
OI never grants paper-forward access (it only gates whether an OI family's sweep may run)."""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.oi_status import (  # noqa: E402
    OI_MAX_ATTEMPTS,
    OI_STATUSES,
    classify_oi_status,
    measure_oi,
    summarize_oi,
)
from src.research_lab.providers.okx_flow import FlowDataError  # noqa: E402


class _FakeOI:
    def __init__(self, points): self._points = points
    def fetch_open_interest(self, symbol, timeframe, start, end):  # noqa: ARG002
        if self._points == "raise":
            raise FlowDataError("boom")
        return list(self._points)


_TF_MS = {"15m": 900000, "1h": 3600000, "4h": 14400000}


def _candle_file(tmp_path, symbol, tf, n=10):
    d = tmp_path / "market_data" / tf
    d.mkdir(parents=True, exist_ok=True)
    step = _TF_MS[tf]
    rows = [{"ts": i * step, "date": "", "open": 100, "high": 101, "low": 99, "close": 100, "vol": 10.0}
            for i in range(n)]
    p = d / f"{symbol}_{rows[0]['ts']}_{rows[-1]['ts']}_{tf}.json"  # {sym}_{start}_{end}_{tf}.json
    p.write_text(json.dumps(rows), encoding="utf-8")
    return p


class TestClassify:
    def test_table(self):
        assert classify_oi_status(0.8, 50, fetch_ok=True) == "oi_available"
        assert classify_oi_status(0.3, 20, fetch_ok=True) == "oi_partial"
        assert classify_oi_status(0.0, 0, fetch_ok=True) == "oi_unmeasured"
        assert classify_oi_status(0.0, 5, fetch_ok=False) == "oi_fetch_failed"

    def test_thin_coverage_is_partial_not_available(self):
        assert classify_oi_status(0.49, 30, fetch_ok=True, min_coverage=0.5) == "oi_partial"


class TestMeasure:
    def test_full_coverage_available_and_writes_on_apply(self, tmp_path):
        p = _candle_file(tmp_path, "X", "1h")
        points = [(i * 3600000, 1000 + i) for i in range(10)]  # covers every candle
        r = measure_oi(p, "X", "1h", provider=_FakeOI(points), now_ms=1, apply=True)
        assert r["status"] == "oi_available" and r["merged_written"] is True
        assert json.loads(p.read_text())[0].get("oi") is not None  # oi field written

    def test_partial_coverage_does_not_write(self, tmp_path):
        p = _candle_file(tmp_path, "X", "1h")
        points = [(i * 3600000, 1000) for i in range(6, 10)]  # only last 4/10 covered = 40%
        r = measure_oi(p, "X", "1h", provider=_FakeOI(points), now_ms=1, apply=True)
        assert r["status"] == "oi_partial" and r["merged_written"] is False
        assert "oi" not in json.loads(p.read_text())[0]  # no fake-pass write

    def test_no_points_is_unmeasured(self, tmp_path):
        p = _candle_file(tmp_path, "X", "1h")
        r = measure_oi(p, "X", "1h", provider=_FakeOI([]), now_ms=1, apply=True)
        assert r["status"] == "oi_unmeasured" and r["merged_written"] is False

    def test_fetch_error_is_fetch_failed(self, tmp_path):
        p = _candle_file(tmp_path, "X", "1h")
        r = measure_oi(p, "X", "1h", provider=_FakeOI("raise"), now_ms=1, apply=True)
        assert r["status"] == "oi_fetch_failed"


class TestSummary:
    def test_aggregates_by_status_and_tf(self):
        rows = [{"timeframe": "1h", "status": "oi_available", "coverage_pct": 80.0, "merged_written": True},
                {"timeframe": "1h", "status": "oi_partial", "coverage_pct": 30.0, "merged_written": False},
                {"timeframe": "4h", "status": "oi_unmeasured", "coverage_pct": 0.0, "merged_written": False}]
        s = summarize_oi(rows)
        assert s["measured"] == 3 and s["available"] == 1 and s["merged_written"] == 1
        assert s["by_timeframe"]["1h"] == {"oi_available": 1, "oi_partial": 1}


class TestInvariant:
    def test_oi_status_never_paper_ready(self):
        # OI is a DATA state, disjoint from any trade verdict; it must not contain a paper status.
        assert "PAPER_FORWARD_READY" not in OI_STATUSES
        assert "oi_available" != "PAPER_FORWARD_READY"


class TestCoordinatorNoEternalPending:
    def test_structural_oi_failure_marks_unmeasured_and_frees_sweep(self, tmp_path):
        from src.research_lab.farm_coordinator import _drain_enrich_oi
        from src.research_lab.farm_tasks_db import FarmTasksDB
        _candle_file(tmp_path, "SYM", "1h", n=80)  # >=60 rows so choose_symbol_file deems it usable
        tasks = FarmTasksDB(":memory:")
        # an OI family sweep blocked on NEEDS_OI_DATA + its enrich_oi task
        tasks.enqueue_task(task_type="run_sweep", task_key="run_sweep::SYM::1h::oi_price_quadrant::gate",
                           symbol="SYM", timeframe="1h", family="oi_price_quadrant", state="blocked",
                           machine_reason="NEEDS_OI_DATA", now=0.0)
        tasks.enqueue_task(task_type="enrich_oi", task_key="enrich_oi::SYM::1h",
                           symbol="SYM", timeframe="1h", now=0.0)
        prov = _FakeOI([])  # structural no_points every attempt
        now = 0.0
        for _ in range(OI_MAX_ATTEMPTS):  # claim defers under the cap, then skips at the cap
            _drain_enrich_oi(tasks, private_root=tmp_path, oi_provider=prov, now_ms=int(now * 1000),
                             limit=1, counters={k: 0 for k in (
                                 "enriched_oi_ok", "enrich_oi_deferred", "oi_marked_unmeasured")}, now=now)
            now += 6 * 3600 + 1  # advance past the 6h defer so the task is claimable again
        # enrich task terminal-skipped, and the blocked sweep freed honestly (not eternal pending)
        assert not tasks.tasks_in_state("blocked", task_type="run_sweep")
        freed = tasks.tasks_in_state("skipped", task_type="run_sweep")
        assert freed and freed[0]["machine_reason"] == "oi_unmeasured"
        tasks.close()
