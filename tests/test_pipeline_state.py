# -*- coding: utf-8 -*-
"""Tests for the scanner->farm coordinator persistent state."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.pipeline_state import PipelineState  # noqa: E402


def test_cycle_lifecycle_and_status(tmp_path):
    st = PipelineState(tmp_path / "s.sqlite")
    c = st.start_cycle()
    st.finish_cycle(c, {"jobs_queued": 2})
    status = st.status()
    assert status["total_cycles"] == 1
    assert status["last_cycle"]["counters"]["jobs_queued"] == 2
    assert status["last_cycle"]["age_seconds"] >= 0
    st.close()


def test_processed_watch_dedup_persists(tmp_path):
    p = tmp_path / "s.sqlite"
    st = PipelineState(p)
    assert not st.is_watch_processed("watch_1")
    st.mark_watch_processed("watch_1", okx_inst="RE-USDT-SWAP", timeframe="1h", cycle=1)
    assert st.is_watch_processed("watch_1")
    st.close()
    # survives reopen (restart)
    st2 = PipelineState(p)
    assert st2.is_watch_processed("watch_1")
    st2.close()


def test_job_dedup_and_queued_insts(tmp_path):
    st = PipelineState(tmp_path / "s.sqlite")
    key = "RE-USDT-SWAP::1h::momentum_breakout"
    assert not st.is_job_queued(key)
    st.mark_job_queued(key, symbol="RE-USDT-SWAP", timeframe="1h", family="momentum_breakout",
                       experiment_id="sweep_x", cycle=1)
    assert st.is_job_queued(key)
    assert "RE-USDT-SWAP" in st.queued_insts()
    st.close()


def test_skip_reasons_aggregated(tmp_path):
    st = PipelineState(tmp_path / "s.sqlite")
    st.record_skip(1, symbol="A", timeframe="1h", reason="too_short")
    st.record_skip(1, symbol="B", timeframe="1h", reason="too_short")
    st.record_skip(1, symbol="C", timeframe="1h", reason="provider_error")
    reasons = st.status()["top_skip_reasons"]
    assert reasons["too_short"] == 2
    assert reasons["provider_error"] == 1
    st.close()


def test_record_prepared(tmp_path):
    st = PipelineState(tmp_path / "s.sqlite")
    st.record_prepared("RE-USDT-SWAP", "1h", rows=120, cycle=1)
    assert st.status()["prepared_data"] == 1
    st.close()
