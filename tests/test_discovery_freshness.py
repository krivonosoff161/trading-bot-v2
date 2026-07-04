# -*- coding: utf-8 -*-
"""Phase 0.3 — discovery snapshot freshness.

A stale/missing universe snapshot must never silently degrade the farm to
blocked:no_eligible: it auto-refreshes in apply mode (TTL-throttled) or warns loudly.
"""
from __future__ import annotations

import datetime as dt
from argparse import Namespace

from scripts.strategy_lab import farm_loop
from src.research_lab import farm_journal
from src.research_lab import instrument_discovery as idisc


def _stamp(age_seconds: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age_seconds)).isoformat()


def _snapshot(age_seconds: float, count: int = 5) -> dict:
    return {
        "schema": idisc.SCHEMA, "generated_at": _stamp(age_seconds), "count": count,
        "groups": {"crypto_major": ["BTC_USDT_SWAP"]},
        "instruments": {"BTC_USDT_SWAP": {"group": "crypto_major", "inst_id": "BTC-USDT-SWAP"}},
    }


def _args(**over) -> Namespace:
    base = dict(discovery_ttl_seconds=6 * 3600, no_discovery_refresh=False)
    base.update(over)
    return Namespace(**base)


class TestSnapshotAge:
    def test_age_for_fresh(self) -> None:
        age = idisc.snapshot_age_seconds(_snapshot(10), int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000))
        assert age is not None and 0 <= age < 120

    def test_age_none_for_missing_stamp(self) -> None:
        assert idisc.snapshot_age_seconds({}, 0) is None


class TestDiscovery:
    def test_missing_snapshot_warns_and_returns_none(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(idisc, "load_snapshot", lambda root: {})
        snap, info = farm_loop._discovery(_args(), "x", False)
        out = capsys.readouterr().out
        assert snap is None
        assert info["status"] == "missing"
        assert "MISSING" in out

    def test_stale_snapshot_warns_no_silent_block(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(idisc, "load_snapshot", lambda root: _snapshot(100 * 3600))
        snap, info = farm_loop._discovery(_args(no_discovery_refresh=True), "x", True)
        out = capsys.readouterr().out
        # Still returns the snapshot (loop runs on what exists) but loudly flags stale.
        assert snap is not None
        assert info["status"] == "stale_no_refresh"
        assert "STALE" in out

    def test_fresh_snapshot_no_warning(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(idisc, "load_snapshot", lambda root: _snapshot(60))
        snap, info = farm_loop._discovery(_args(), "x", False)
        out = capsys.readouterr().out
        assert info["status"] == "fresh"
        assert "WARNING" not in out

    def test_apply_auto_refresh_marks_refreshed(self, monkeypatch) -> None:
        import scripts.strategy_lab.discover_okx_universe as disc_mod
        monkeypatch.setattr(
            disc_mod, "discover",
            lambda root, **kw: {"status": "discovered", "count": 9, "diff": {"new": 2}})
        # After refresh, load_snapshot returns a fresh snapshot.
        monkeypatch.setattr(idisc, "load_snapshot", lambda root: _snapshot(5, count=9))
        snap, info = farm_loop._discovery(_args(), "x", True)
        assert info["status"] == "refreshed"
        assert info["count"] == 9

    def test_no_refresh_flag_skips_network_in_apply(self, monkeypatch) -> None:
        import scripts.strategy_lab.discover_okx_universe as disc_mod

        def _boom(*a, **k):
            raise AssertionError("discover() must not be called with --no-discovery-refresh")

        monkeypatch.setattr(disc_mod, "discover", _boom)
        monkeypatch.setattr(idisc, "load_snapshot", lambda root: _snapshot(60))
        snap, info = farm_loop._discovery(_args(no_discovery_refresh=True), "x", True)
        assert info["status"] == "fresh"


class TestCycleLogDiscovery:
    def test_log_cycle_records_discovery(self, tmp_path) -> None:
        result = {"pivot": "discovery_refill", "active_tasks": 1, "counters": {}}
        farm_journal.log_cycle(tmp_path, ts=1.0, mode="apply", result=result,
                               discovery={"status": "stale_no_refresh", "age_seconds": 99, "count": 5})
        rows = farm_journal.read_recent_cycles(tmp_path, limit=1)
        assert rows[-1]["discovery"]["status"] == "stale_no_refresh"
