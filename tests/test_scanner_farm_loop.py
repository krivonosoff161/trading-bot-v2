# -*- coding: utf-8 -*-
"""End-to-end tests for the scanner -> market-data -> farm coordinator cycle.

A fake market-data provider drives the whole prepare -> queue path with NO network:
its fetch_ohlcv returns candles spaced to the requested timeframe so prepare writes a
usable local file, choose_symbol_file finds it, and the sweep compiles + queues into
the real (tmp) state_db.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.pipeline_state import PipelineState  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.scanner_farm_pipeline import run_cycle  # noqa: E402
from src.research_lab.state_db import dashboard_snapshot, default_db_path  # noqa: E402
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402

_NOW_MS = 1_780_000_000_000
_TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


class _FakeProvider:
    name = "fake-okx-public"
    configured = True

    def __init__(self, n=120, *, fail=False, empty=False):
        self._n, self._fail, self._empty = n, fail, empty

    def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
        if self._fail:
            raise RuntimeError("simulated provider failure")
        if self._empty:
            return []
        interval = _TF_MS[timeframe]
        return [{"ts": end_ts - (self._n - 1 - i) * interval, "date": "x",
                 "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "vol": 5.0}
                for i in range(self._n)]


def _watch(symbol, *, tf="1h", eligible=True, resolved=True, readiness="usable",
           source="cointelegraph", event_type="etf_flow", watch_id=None, pending_reason=None,
           farm=True):
    w = {
        "watch_id": watch_id or f"watch_{symbol}_{tf}",
        "created_at": "2026-06-17T00:00:00Z",
        "scanner": {"verdict": "WATCH", "event_type": event_type},
        "trigger": {"source": source, "headline": f"{symbol} event"},
        "asset": {"symbol": symbol, "okx_inst": f"{symbol}-USDT-SWAP", "okx_resolved": resolved},
    }
    if farm:
        w["farm"] = {"eligible": eligible, "selected_timeframe": tf,
                     "data_readiness_status": readiness, "pending_reason": pending_reason}
    return w


def _cycle(state, watches, *, provider=None, apply=True, **kw):
    return run_cycle(
        state, private_root=kw.pop("private_root"), provider=provider or _FakeProvider(),
        watches=watches, profiles=load_timeframe_profiles(), policy=load_resource_policy(),
        apply=apply, backend="cpu", now_ms=_NOW_MS, max_jobs=kw.pop("max_jobs", 6),
        max_prepares=kw.pop("max_prepares", 6), **kw)


def test_eligible_watch_auto_prepares_and_queues(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    res = _cycle(st, [_watch("RE", tf="1h")], private_root=tmp_path)
    assert res["counters"]["data_prepared"] == 1
    assert res["counters"]["jobs_queued"] == 1
    assert st.is_watch_processed("watch_RE_1h")
    assert st.status()["queued_jobs"] == 1
    # the job really landed in the farm queue db
    assert dashboard_snapshot(default_db_path(tmp_path))["queue_counts"].get("queued") == 1
    st.close()


def test_selected_timeframe_used_not_1d(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    res = _cycle(st, [_watch("RE", tf="15m")], private_root=tmp_path)
    assert res["decisions"][0]["timeframe"] == "15m"
    # the queued spec file is at 15m, not a blind 1d
    specs = list((tmp_path / "plans" / "event_specs").glob("*.json"))
    assert specs and '"timeframe": "15m"' in specs[0].read_text(encoding="utf-8")
    st.close()


def test_legacy_watch_without_farm_block_is_skipped(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    res = _cycle(st, [_watch("RE", farm=False)], private_root=tmp_path)
    assert res["counters"]["jobs_queued"] == 0
    assert any(s["reason"] == "readiness_not_assessed" for s in res["skipped"])
    st.close()


def test_ineligible_watch_is_skipped(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    res = _cycle(st, [_watch("GEOD", eligible=False, resolved=False, readiness="",
                             pending_reason="no_okx_instrument")], private_root=tmp_path)
    assert res["counters"]["jobs_queued"] == 0
    assert res["counters"]["skipped_missing_instrument"] == 1
    st.close()


def test_duplicate_watch_no_duplicate_jobs(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    watches = [_watch("RE", tf="1h", watch_id="w1"), _watch("RE", tf="1h", watch_id="w2")]
    res = _cycle(st, watches, private_root=tmp_path)
    assert res["counters"]["jobs_queued"] == 1   # same inst+tf collapses to one job
    st.close()


def test_checkpoint_prevents_reprocessing(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    r1 = _cycle(st, [_watch("RE", tf="1h")], private_root=tmp_path)
    assert r1["counters"]["jobs_queued"] == 1
    # second cycle, same watch: already processed + job already queued -> nothing new
    r2 = _cycle(st, [_watch("RE", tf="1h")], private_root=tmp_path)
    assert r2["counters"]["watches_read"] == 0       # checkpoint filtered it out
    assert r2["counters"]["jobs_queued"] == 0
    st.close()


def test_dry_run_writes_no_state_or_queue(tmp_path):
    st = PipelineState(":memory:")
    res = run_cycle(st, private_root=tmp_path, provider=None, watches=[_watch("RE", tf="1h")],
                    profiles=load_timeframe_profiles(), policy=load_resource_policy(),
                    apply=False, now_ms=_NOW_MS)
    assert res["counters"]["would_queue"] == 1
    assert res["counters"]["jobs_queued"] == 0
    # nothing written: no farm db, no prepared candles, no spec files
    assert not default_db_path(tmp_path).exists()
    assert not (tmp_path / "market_data").exists()
    assert not (tmp_path / "plans").exists()
    # ...and the in-memory state stayed empty (no record_* slipped past an apply guard)
    s = st.status()
    assert s["total_cycles"] == 0 and s["processed_watches"] == 0
    assert s["queued_jobs"] == 0 and s["prepared_data"] == 0
    assert s["top_skip_reasons"] == {}
    st.close()


def test_usable_path_reuses_data_and_farm_dedups(tmp_path):
    # Cycle 1 prepares + queues RE. Cycle 2 uses a FRESH coordinator state (same root + same
    # watch_id) so the state checkpoint can't filter it: the local file is already usable
    # (no re-prepare) and the farm queue already holds that spec -> created=False ->
    # already_queued, not a second job.
    st1 = PipelineState(tmp_path / "s1.sqlite")
    _cycle(st1, [_watch("RE", tf="1h", watch_id="w1")], private_root=tmp_path)
    st1.close()
    st2 = PipelineState(tmp_path / "s2.sqlite")
    res = _cycle(st2, [_watch("RE", tf="1h", watch_id="w1")], private_root=tmp_path)
    assert res["counters"]["data_prepared"] == 0       # reused existing candles, no fetch
    assert res["counters"]["jobs_queued"] == 0
    assert res["counters"]["already_queued"] == 1      # farm queue dedup (created=False)
    st2.close()


def test_max_prepares_cap_bounds_load(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    watches = [_watch("AAA", tf="1h", watch_id="w1"), _watch("BBB", tf="1h", watch_id="w2")]
    res = _cycle(st, watches, private_root=tmp_path, max_prepares=1)
    assert res["counters"]["data_prepared"] == 1        # only one real prepare this cycle
    assert res["counters"]["jobs_queued"] == 1
    assert res["counters"]["prepare_deferred"] == 1     # second deferred, NOT prepare_failed
    assert res["counters"]["prepare_failed"] == 0
    assert any(s["reason"] == "prepare_cap_reached" for s in res["skipped"])
    assert st.status()["top_skip_reasons"].get("prepare_cap_reached") == 1
    st.close()


def test_prepare_cap_deferred_watch_retries_next_cycle(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    watches = [_watch("AAA", tf="1h", watch_id="w1"), _watch("BBB", tf="1h", watch_id="w2")]
    r1 = _cycle(st, watches, private_root=tmp_path, max_prepares=1)
    assert r1["counters"]["jobs_queued"] == 1
    assert st.is_watch_processed("w1")
    assert not st.is_watch_processed("w2")
    r2 = _cycle(st, watches, private_root=tmp_path, max_prepares=6)
    assert r2["counters"]["watches_read"] == 1
    assert r2["counters"]["jobs_queued"] == 1
    assert st.is_watch_processed("w2")
    st.close()


def test_queue_cap_deferred_watch_retries_next_cycle(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    watches = [_watch("AAA", tf="1h", watch_id="w1"), _watch("BBB", tf="1h", watch_id="w2")]
    r1 = _cycle(st, watches, private_root=tmp_path, max_jobs=1)
    assert r1["counters"]["jobs_queued"] == 1
    assert any(s["reason"] == "queue_cap_reached" for s in r1["skipped"])
    assert st.is_watch_processed("w1")
    assert not st.is_watch_processed("w2")
    r2 = _cycle(st, watches, private_root=tmp_path, max_jobs=6)
    assert r2["counters"]["watches_read"] == 1
    assert r2["counters"]["jobs_queued"] == 1
    assert st.is_watch_processed("w2")
    st.close()


def test_provider_not_configured_is_distinct_reason(tmp_path):
    from src.research_lab.market_data_provider import get_provider
    st = PipelineState(tmp_path / "state.sqlite")
    res = _cycle(st, [_watch("RE", tf="1h")], private_root=tmp_path, provider=get_provider("null"))
    assert res["counters"]["jobs_queued"] == 0
    assert any(s["reason"] == "provider_not_configured" for s in res["skipped"])
    st.close()


def test_failed_prepare_records_structured_reason(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    res = _cycle(st, [_watch("RE", tf="1h")], private_root=tmp_path, provider=_FakeProvider(fail=True))
    assert res["counters"]["jobs_queued"] == 0
    assert res["counters"]["prepare_failed"] == 1
    assert any(s["reason"] == "provider_error" for s in res["skipped"])
    assert st.status()["top_skip_reasons"].get("provider_error") == 1
    st.close()


def test_empty_provider_is_missing_prepared_data(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    res = _cycle(st, [_watch("RE", tf="1h")], private_root=tmp_path, provider=_FakeProvider(empty=True))
    assert res["counters"]["jobs_queued"] == 0
    assert any(s["reason"] == "missing_prepared_data" for s in res["skipped"])
    st.close()


def test_too_short_when_provider_returns_few_bars(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    res = _cycle(st, [_watch("RE", tf="1h")], private_root=tmp_path, provider=_FakeProvider(n=10))
    assert res["counters"]["jobs_queued"] == 0
    assert any(s["reason"] == "too_short" for s in res["skipped"])
    st.close()


def test_refill_universe_keeps_gpu_busy(tmp_path):
    st = PipelineState(tmp_path / "state.sqlite")
    res = _cycle(st, [], private_root=tmp_path, backlog_symbols=["BTC-USDT-SWAP"],
                 refill_timeframe="1h")
    # no scanner watches, but the backlog symbol is prepared + queued
    assert res["counters"]["jobs_queued"] == 1
    assert res["decisions"][0]["symbol"] == "BTC-USDT-SWAP"
    st.close()
