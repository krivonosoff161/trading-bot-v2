# -*- coding: utf-8 -*-
"""Phase 0.2 — off-by-default stage visibility.

A bare apply/loop run with --run-worker/--run-validation/--run-paper off only QUEUES
work. That must be visible: a loud warning on stdout and a `stages` block in cycle_log,
so an operator never mistakes a partial loop for a working one.
"""
from __future__ import annotations

from argparse import Namespace

from scripts.strategy_lab import farm_loop
from src.research_lab import farm_journal


def _args(**over) -> Namespace:
    base = dict(run_worker=False, run_validation=False, run_paper=False,
                enrich_funding=False, enrich_oi=False)
    base.update(over)
    return Namespace(**base)


class TestStageStatus:
    def test_critical_flags_marked(self) -> None:
        s = farm_loop._stage_status(_args(), apply=True)
        for name in ("worker", "validation", "paper"):
            assert s[name]["critical"] is True
        for name in ("enrich_funding", "enrich_oi"):
            assert s[name]["critical"] is False

    def test_skipped_reason_present_when_off(self) -> None:
        s = farm_loop._stage_status(_args(run_worker=False), apply=True)
        assert s["worker"]["enabled"] is False
        assert "--run-worker" in s["worker"]["skipped_reason"]

    def test_no_reason_when_on(self) -> None:
        s = farm_loop._stage_status(_args(run_validation=True), apply=True)
        assert s["validation"]["enabled"] is True
        assert s["validation"]["skipped_reason"] is None


class TestPrintWarning:
    def test_warns_when_critical_off_in_apply(self, capsys) -> None:
        farm_loop._print_stages(farm_loop._stage_status(_args(), apply=True), apply=True)
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "worker" in out and "validation" in out and "paper" in out

    def test_no_warning_when_all_critical_on(self, capsys) -> None:
        s = farm_loop._stage_status(
            _args(run_worker=True, run_validation=True, run_paper=True), apply=True)
        farm_loop._print_stages(s, apply=True)
        out = capsys.readouterr().out
        assert "WARNING" not in out

    def test_no_warning_in_dry_run(self, capsys) -> None:
        farm_loop._print_stages(farm_loop._stage_status(_args(), apply=False), apply=False)
        out = capsys.readouterr().out
        assert "WARNING" not in out


class TestCycleLogStages:
    def test_log_cycle_records_stages_and_skipped(self, tmp_path) -> None:
        stages = farm_loop._stage_status(_args(run_worker=True), apply=True)
        result = {"pivot": "work_available", "active_tasks": 3, "counters": {"sweeps": 2},
                  "status": {"by_state": {"queued": 3}}}
        farm_journal.log_cycle(tmp_path, ts=1000.0, mode="apply", result=result, stages=stages)
        cycles = farm_journal.read_recent_cycles(tmp_path, limit=5)
        assert len(cycles) == 1
        assert cycles[-1]["stages"]["worker"]["enabled"] is True
        # validation + paper are off -> skipped_stages reports them
        skipped = farm_journal.skipped_stages(cycles[-1])
        assert set(skipped) == {"validation", "paper"}

    def test_skipped_stages_empty_when_no_stage_data(self) -> None:
        assert farm_journal.skipped_stages({"pivot": "x"}) == []

    def test_smoke_caps_skip_forward_and_new_paper_generation(self, tmp_path, monkeypatch) -> None:
        from src.research_lab.paper_signals import cycle as paper_cycle

        seen: dict[str, int] = {}

        def fake_cycle(*_args, **kwargs):
            seen["max_new"] = kwargs["max_new"]
            seen["max_pfr_scan"] = kwargs["max_pfr_scan"]
            seen["max_observe"] = kwargs["max_observe"]
            return {"generated": 0, "pfr_counts": {}, "state": {}, "gate_counts": {}}

        monkeypatch.setattr(farm_loop, "_providers", lambda *_a, **_k: (None, None, None))
        monkeypatch.setattr(farm_loop, "_read_intake", lambda *_a, **_k: [])
        monkeypatch.setattr(farm_loop, "_discovery", lambda *_a, **_k: (None, {"status": "smoke"}))
        monkeypatch.setattr(farm_loop, "_maybe_storage_maintain", lambda *_a, **_k: None)
        monkeypatch.setattr(farm_loop, "run_coordinator_cycle", lambda *_a, **_k: {
            "pivot": "smoke",
            "active_tasks": 0,
            "counters": {},
            "status": {},
            "errors": [],
        })
        monkeypatch.setattr(paper_cycle, "run_cycle", fake_cycle)

        args = Namespace(
            max_plan_events=0,
            max_prepares=0,
            max_enrich=0,
            max_sweeps=0,
            run_worker=False,
            max_worker_jobs=0,
            night_mode=False,
            allow_public_output=False,
            run_validation=False,
            no_followups=True,
            max_followups=0,
            sweep_tier="smoke",
            run_paper=False,
            max_paper_cards=0,
            true_forward_max_candidates=0,
            run_paper_signals=True,
            pfr_db_path="",
            paper_signals_fetch_timeout=1.0,
            paper_signals_max_new=0,
            paper_signals_max_pfr_scan=0,
            paper_signals_max_observe=0,
            provider="synthetic",
            backend="cpu",
            data_days=None,
            enrich_funding=False,
            enrich_oi=False,
            discovery_ttl_seconds=3600,
            no_discovery_refresh=True,
        )

        out = farm_loop._run_once(args, object(), {}, {}, tmp_path, apply=True)

        assert out["true_forward"]["skipped"] == "true_forward_max_candidates=0"
        assert seen == {"max_new": 0, "max_pfr_scan": 0, "max_observe": 0}
        assert out["main_paper_runtime_queue"]["queued"] == 0
        assert out["main_paper_runtime_queue"]["execution_allowed"] is False
        assert out["main_paper_runtime_observation"]["rows_read"] == 0
        assert out["main_paper_runtime_observation"]["execution_allowed"] is False
