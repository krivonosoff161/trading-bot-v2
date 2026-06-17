# -*- coding: utf-8 -*-
"""CLI wiring + status-reader tests for the scanner->farm coordinator (no network)."""
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.strategy_lab import farm_queue_status as FQS  # noqa: E402
from scripts.strategy_lab import scanner_farm_loop as LOOP  # noqa: E402
from src.research_lab.pipeline_state import PipelineState, state_db_path  # noqa: E402
from src.research_lab.state_db import default_db_path  # noqa: E402

_TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


class _FakeProvider:
    name, configured = "fake", True

    def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
        interval = _TF_MS[timeframe]
        return [{"ts": end_ts - (119 - i) * interval, "open": 1.0, "high": 1.1, "low": 0.9,
                 "close": 1.0, "vol": 5.0} for i in range(120)]


def _watch(symbol, tf="1h"):
    return {
        "watch_id": f"watch_{symbol}_{tf}", "created_at": "2026-06-17T00:00:00Z",
        "scanner": {"verdict": "WATCH", "event_type": "etf_flow"},
        "trigger": {"source": "cointelegraph", "headline": f"{symbol} event"},
        "asset": {"symbol": symbol, "okx_inst": f"{symbol}-USDT-SWAP", "okx_resolved": True},
        "farm": {"eligible": True, "selected_timeframe": tf, "data_readiness_status": "usable"},
    }


def _args(**over):
    base = dict(private_root=None, state_path="", run_scanner_pass=False, scanner_limit=5,
                include_expired=False, refill_universe="", max_jobs_per_cycle=6,
                max_data_prepares_per_cycle=6, max_worker_jobs_per_cycle=0, run_worker=False,
                backend="cpu", data_days=None, allow_public_output=False)
    base.update(over)
    return SimpleNamespace(**base)


def test_should_stop():
    assert LOOP._should_stop("") is False
    assert LOOP._should_stop("nonexistent_file_xyz") is False


def test_should_stop_true_when_file_present(tmp_path):
    f = tmp_path / "stop"
    f.write_text("x", encoding="utf-8")
    assert LOOP._should_stop(str(f)) is True


def test_drain_worker_zero_is_noop():
    assert LOOP._drain_worker(Path("."), 0) == []


def test_drain_worker_breaks_on_queue_empty(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_worker(private_root, *, ignore_cadence=False):
        calls["n"] += 1
        return {"status": "completed"} if calls["n"] == 1 else {"status": "queue_empty"}

    monkeypatch.setattr("scripts.strategy_lab.worker_once.run_worker_once", fake_worker)
    out = LOOP._drain_worker(tmp_path, 5)
    assert [o["status"] for o in out] == ["completed", "queue_empty"]  # stopped at empty, not 5x


def test_cli_dry_run_uses_memory_state_and_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(LOOP, "_load_watches", lambda inc: [_watch("RE", "1h")])
    out = LOOP._run_one_cycle(_args(private_root=str(tmp_path)), apply=False)
    assert out["result"]["counters"]["would_queue"] == 1
    assert out["status"]["db"] == ":memory:"            # dry-run never opens an on-disk state db
    assert not default_db_path(tmp_path).exists()
    assert not state_db_path(tmp_path).exists()
    assert not (tmp_path / "market_data").exists()


def test_cli_apply_opens_real_provider_and_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(LOOP, "_load_watches", lambda inc: [_watch("RE", "1h")])
    monkeypatch.setattr("src.research_lab.market_data_provider.get_provider",
                        lambda name: _FakeProvider())
    out = LOOP._run_one_cycle(_args(private_root=str(tmp_path)), apply=True)
    assert out["result"]["counters"]["jobs_queued"] == 1
    assert default_db_path(tmp_path).exists()           # farm queue written
    assert state_db_path(tmp_path).exists()             # coordinator state written
    assert out["status"]["queued_jobs"] == 1


def test_pipeline_status_reader_no_file_is_readonly(tmp_path):
    # Non-existent state db -> {} AND no file created (read-only invariant).
    assert FQS._pipeline_status(tmp_path, "") == {}
    assert not state_db_path(tmp_path).exists()


def test_pipeline_status_reader_returns_counts(tmp_path):
    p = tmp_path / "state" / "scanner_farm_loop.sqlite"
    st = PipelineState(p)
    c = st.start_cycle()
    st.record_skip(c, symbol="RE", timeframe="1h", reason="too_short")
    st.finish_cycle(c, {"jobs_queued": 0})
    st.close()
    status = FQS._pipeline_status(tmp_path, str(p))
    assert status["total_cycles"] == 1
    assert status["top_skip_reasons"].get("too_short") == 1
