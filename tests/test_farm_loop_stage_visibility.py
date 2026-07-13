# -*- coding: utf-8 -*-
"""Phase 0.2 — off-by-default stage visibility.

A bare apply/loop run with --run-worker/--run-validation/--run-paper off only QUEUES
work. That must be visible: a loud warning on stdout and a `stages` block in cycle_log,
so an operator never mistakes a partial loop for a working one.
"""
from __future__ import annotations

import json
import asyncio
import threading
from argparse import Namespace
from pathlib import Path

from scripts.strategy_lab import farm_loop
from src.research_lab import farm_journal


def test_priority_worker_uses_independent_db_and_stops_cleanly(monkeypatch, tmp_path) -> None:
    seen = {"closed": False, "slots": 0, "statuses": []}

    class FakeTasks:
        on_transition = None

        def eligible_count(self):
            return 0

        def close(self):
            seen["closed"] = True

    stop = threading.Event()

    def fake_slot(*args, **kwargs):
        seen["slots"] += 1
        stop.set()
        return {"pivot": "idle", "active_tasks": 0, "counters": {}, "status": {}, "errors": []}

    monkeypatch.setattr(farm_loop, "FarmTasksDB", lambda path: FakeTasks())
    monkeypatch.setattr(farm_loop, "_run_priority_slot", fake_slot)
    monkeypatch.setattr(farm_loop, "_write_priority_checkpoint", lambda *args, **kwargs: tmp_path / "cp")
    monkeypatch.setattr(
        farm_loop, "_write_priority_worker_status",
        lambda root, **kwargs: seen["statuses"].append(kwargs["stage"]),
    )
    monkeypatch.setattr(farm_journal, "make_transition_sink", lambda root: None)

    farm_loop._priority_worker_loop(
        Namespace(stop_file="", busy_slot_seconds=0.1, idle_poll_seconds=0.1),
        {}, {}, tmp_path, stop,
    )

    assert seen["slots"] == 1
    assert seen["closed"] is True
    assert seen["statuses"] == ["running_slot", "idle", "stopped"]


def _args(**over) -> Namespace:
    base = dict(run_worker=False, run_validation=False, run_paper=False,
                enrich_funding=False, enrich_oi=False, run_journal_export=False)
    base.update(over)
    return Namespace(**base)


class TestStageStatus:
    def test_priority_checkpoint_is_resumable_and_paper_only(self, tmp_path: Path) -> None:
        target = farm_loop._write_priority_checkpoint(
            tmp_path,
            {
                "pivot": "advanced_lifecycle",
                "active_tasks": 3,
                "status": {"by_state": {"queued": 2, "running": 1}},
                "counters": {"runs_completed": 1},
                "errors": [],
            },
            sequence=7,
        )
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["sequence"] == 7
        assert payload["resume_mode"] == "requeue_atomic_slot_from_durable_ledgers"
        assert payload["paper_only"] is True
        assert payload["execution_allowed"] is False

    def test_slot_did_work_uses_real_transition_counters(self) -> None:
        assert farm_loop._slot_did_work({"counters": {"runs_completed": 1}}) is True
        assert farm_loop._slot_did_work({"counters": {"runs_completed": 0}}) is False

    def test_pid_probe_treats_windows_system_error_as_dead(self, monkeypatch) -> None:
        def bad_kill(_pid: int, _sig: int) -> None:
            raise SystemError("<built-in function kill> returned a result with an exception set")

        monkeypatch.setattr(farm_loop.os, "kill", bad_kill)

        assert farm_loop._pid_is_alive(123456789) is False

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

    def test_journal_export_is_non_critical(self) -> None:
        s = farm_loop._stage_status(_args(run_journal_export=False), apply=True)

        assert s["journal_export"]["enabled"] is False
        assert s["journal_export"]["critical"] is False
        assert "--run-journal-export" in s["journal_export"]["skipped_reason"]


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

    def test_cycle_print_separates_delivery_cards_from_messages(self, capsys) -> None:
        farm_loop._print_cycle({
            "pivot": "work_available",
            "active_tasks": 1,
            "counters": {},
            "status": {},
            "paper_telegram_delivery": {
                "eligible_cards": 3,
                "target_recipients": 2,
                "sent_messages": 4,
                "sent_cards": 2,
                "duplicate_messages": 2,
                "duplicate_cards": 1,
                "skipped_messages": 0,
                "error_messages": 0,
                "dry_run": False,
                "sends_network": True,
            },
        })

        out = capsys.readouterr().out

        assert "eligible_cards=3" in out
        assert "targets=2" in out
        assert "sent_messages=4" in out
        assert "sent_cards=2" in out
        assert "duplicate_messages=2" in out
        assert "duplicate_cards=1" in out

    def test_run_paper_refreshes_main_paper_chain_without_signal_lane(self, tmp_path, monkeypatch) -> None:
        seen: dict[str, object] = {}

        monkeypatch.setattr(farm_loop, "_providers", lambda args, apply: ("provider", None, None))
        monkeypatch.setattr(farm_loop, "_discovery", lambda args, root, apply: (None, {"status": "test"}))
        monkeypatch.setattr(
            farm_loop,
            "run_coordinator_cycle",
            lambda *args, **kwargs: {"pivot": "idle", "active_tasks": 0, "counters": {}, "status": {}},
        )

        from src.research_lab import paper_runtime

        monkeypatch.setattr(
            paper_runtime,
            "run_paper_cycle",
            lambda root, apply, limit: {"counters": {"cards": 1}, "readiness": {}, "results": []},
        )

        def fake_refresh(args, private_root, *, apply, loop, cycle_started_at, out, provider=None):
            seen["called"] = True
            seen["provider"] = provider
            out["main_paper_bridge"] = {"instructions": 1}

        monkeypatch.setattr(farm_loop, "_run_main_paper_derived_chain", fake_refresh)

        args = Namespace(
            max_plan_events=0,
            max_prepares=0,
            max_enrich=0,
            max_sweeps=0,
            run_worker=False,
            max_worker_jobs=0,
            max_validations=0,
            night_mode=False,
            allow_public_output=False,
            run_validation=False,
            no_followups=True,
            max_followups=0,
            sweep_tier="smoke",
            run_paper=True,
            max_paper_cards=1,
            true_forward_max_candidates=0,
            run_paper_signals=False,
            provider="synthetic",
            backend="cpu",
            data_days=None,
            enrich_funding=False,
            enrich_oi=False,
            run_journal_export=False,
            discovery_ttl_seconds=3600,
            no_discovery_refresh=True,
            loop=False,
        )

        out = farm_loop._run_once(args, object(), {}, {}, tmp_path, apply=True)

        assert seen == {"called": True, "provider": "provider"}
        assert out["paper"]["counters"]["cards"] == 1
        assert out["main_paper_bridge"]["instructions"] == 1

    def test_run_paper_refreshes_main_chain_once_when_signal_lane_runs(self, tmp_path, monkeypatch) -> None:
        seen: dict[str, int] = {"refresh_calls": 0}

        monkeypatch.setattr(farm_loop, "_providers", lambda args, apply: ("provider", None, None))
        monkeypatch.setattr(farm_loop, "_discovery", lambda args, root, apply: (None, {"status": "test"}))
        monkeypatch.setattr(
            farm_loop,
            "run_coordinator_cycle",
            lambda *args, **kwargs: {"pivot": "idle", "active_tasks": 0, "counters": {}, "status": {}},
        )

        from src.research_lab import paper_runtime

        monkeypatch.setattr(
            paper_runtime,
            "run_paper_cycle",
            lambda root, apply, limit: {"counters": {"cards": 1}, "readiness": {}, "results": []},
        )

        def fake_refresh(*args, **kwargs):
            seen["refresh_calls"] += 1

        monkeypatch.setattr(farm_loop, "_run_main_paper_derived_chain", fake_refresh)

        args = Namespace(
            max_plan_events=0,
            max_prepares=0,
            max_enrich=0,
            max_sweeps=0,
            run_worker=False,
            max_worker_jobs=0,
            max_validations=0,
            night_mode=False,
            allow_public_output=False,
            run_validation=False,
            no_followups=True,
            max_followups=0,
            sweep_tier="smoke",
            run_paper=True,
            max_paper_cards=1,
            true_forward_max_candidates=0,
            run_paper_signals=True,
            pfr_db_path="",
            paper_signals_fetch_timeout=1.0,
            paper_signals_timeframes="15m",
            paper_signals_max_new=0,
            paper_signals_max_pfr_scan=0,
            paper_signals_max_pfr_fetches=0,
            paper_signals_pfr_reserved=0,
            paper_signals_max_observe=0,
            paper_signals_max_live_fetches=0,
            paper_signals_max_network_fetches=0,
            main_paper_runtime_limit=0,
            provider="synthetic",
            backend="cpu",
            data_days=None,
            enrich_funding=False,
            enrich_oi=False,
            run_journal_export=False,
            discovery_ttl_seconds=3600,
            no_discovery_refresh=True,
            loop=False,
            no_live_universe_refresh=True,
            live_universe_ttl_seconds=3600,
            live_universe_top_n=1,
            send_paper_telegram=False,
            paper_telegram_limit=0,
            paper_telegram_status_digest=False,
            paper_telegram_status_digest_hours=12,
            run_calculator_advisor=False,
            run_agent_role_reviews=False,
        )

        farm_loop._run_once(args, object(), {}, {}, tmp_path, apply=True)

        assert seen["refresh_calls"] == 1


class TestCycleLogStages:
    def test_live_universe_refresh_skips_fresh_snapshot(self, tmp_path, monkeypatch) -> None:
        from src.research_lab import live_universe_selector

        now = 1000.0
        discovery = tmp_path / "discovery"
        discovery.mkdir()
        (discovery / "live_universe.json").write_text(
            json.dumps({
                "schema": "live_universe.v1",
                "generated_at": now - 60,
                "detail": {"fresh_movers": [{"symbol": "AAA_USDT_SWAP"}]},
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            live_universe_selector,
            "run",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("fresh snapshot must not refresh")),
        )

        out = farm_loop._refresh_live_universe(
            Namespace(live_universe_ttl_seconds=900, live_universe_top_n=12, no_live_universe_refresh=False),
            tmp_path,
            apply=True,
            now=now,
        )

        assert out["status"] == "fresh"
        assert out["refreshed"] is False
        assert out["count"] == 1

    def test_live_universe_refresh_updates_stale_snapshot(self, tmp_path, monkeypatch) -> None:
        from src.research_lab import live_universe_selector

        now = 10_000.0
        discovery = tmp_path / "discovery"
        discovery.mkdir()
        (discovery / "live_universe.json").write_text(
            json.dumps({
                "schema": "live_universe.v1",
                "generated_at": now - 10_000,
                "detail": {"fresh_movers": [{"symbol": "OLD_USDT_SWAP"}]},
            }),
            encoding="utf-8",
        )

        def fake_run(_root, *, top_n_per_group, now):
            assert top_n_per_group == 12
            return {
                "selected": {"fresh_movers": [{"symbol": "NEW_USDT_SWAP"}]},
                "intake_events": [{"event_id": "e1"}],
                "tickers_seen": 321,
            }

        def fake_write_snapshot(root, result, *, generated_at):
            (Path(root) / "discovery" / "live_universe.json").write_text(
                json.dumps({
                    "schema": "live_universe.v1",
                    "generated_at": generated_at,
                    "detail": result["selected"],
                }),
                encoding="utf-8",
            )

        monkeypatch.setattr(live_universe_selector, "run", fake_run)
        monkeypatch.setattr(live_universe_selector, "write_snapshot", fake_write_snapshot)
        monkeypatch.setattr(
            live_universe_selector,
            "apply_intake",
            lambda *_a, **_k: {"registered": 1, "duplicate": 0},
        )

        out = farm_loop._refresh_live_universe(
            Namespace(live_universe_ttl_seconds=900, live_universe_top_n=12, no_live_universe_refresh=False),
            tmp_path,
            apply=True,
            now=now,
        )

        assert out["status"] == "refreshed"
        assert out["refreshed"] is True
        assert out["count"] == 1
        assert out["tickers_seen"] == 321
        assert out["registered"] == 1

    def test_paper_telegram_config_default_is_dry_run(self) -> None:
        cfg = farm_loop._paper_telegram_delivery_config(
            Namespace(send_paper_telegram=False),
            apply=True,
        )

        assert cfg["apply"] is False
        assert cfg["configured"] is False
        assert cfg["ids"] == []
        assert cfg["send_text"] is None

    def test_paper_telegram_config_opt_in_uses_active_subscribers(self, monkeypatch) -> None:
        from scripts.strategy_lab import paper_telegram_transport
        from scripts import subscriptions
        from src.utils import telegram

        class FakeResponse:
            status = 200

            async def text(self) -> str:
                return '{"ok": true, "result": {"message_id": 42}}'

            async def json(self) -> dict:
                return {"ok": True, "result": {"message_id": 42}}

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, url: str, *, json: dict, timeout):
                assert url.startswith("https://api.telegram.org/bottoken/")
                assert json["chat_id"] == "111"
                assert json["text"] == "card"
                return FakeResponse()

        monkeypatch.setattr(
            subscriptions,
            "list_delivery_users",
            lambda: [
                {"chat_id": "111", "status": "active"},
                {"chat_id": "222", "status": "expired"},
                {"chat_id": "333", "status": "superadmin"},
            ],
        )

        async def fake_send_photo(chat_id: str, path: str) -> int:
            assert chat_id == "111"
            assert path == "chart.png"
            return 43

        monkeypatch.setattr(telegram, "bot_token", lambda: "token")
        monkeypatch.setattr(telegram, "send_photo_to", fake_send_photo)
        monkeypatch.setattr(paper_telegram_transport.aiohttp, "ClientSession", FakeSession)

        cfg = farm_loop._paper_telegram_delivery_config(
            Namespace(send_paper_telegram=True),
            apply=True,
        )

        assert cfg["apply"] is True
        assert cfg["configured"] is True
        assert cfg["ids"] == ["111", "333"]
        assert asyncio.run(cfg["send_text"]("111", "card")) == 42
        assert asyncio.run(cfg["send_photo"]("111", "chart.png")) == 43

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

    def test_sleep_until_next_cycle_stops_immediately_when_stop_file_exists(self, tmp_path) -> None:
        stop_file = tmp_path / "STOP_FARM_FULL_CYCLE.txt"
        stop_file.write_text("stop", encoding="utf-8")

        assert farm_loop._sleep_until_next_cycle(600, str(stop_file)) is False

    def test_smoke_caps_skip_forward_and_new_paper_generation(self, tmp_path, monkeypatch) -> None:
        from src.research_lab.paper_signals import cycle as paper_cycle
        from src.research_lab.providers import okx_public

        seen: dict[str, object] = {}

        def fake_cycle(*_args, **kwargs):
            seen["max_new"] = kwargs["max_new"]
            seen["max_pfr_scan"] = kwargs["max_pfr_scan"]
            seen["max_pfr_fetches"] = kwargs["max_pfr_fetches"]
            seen["pfr_reserved_new"] = kwargs["pfr_reserved_new"]
            seen["max_observe"] = kwargs["max_observe"]
            seen["max_live_fetches"] = kwargs["max_live_fetches"]
            seen["max_network_fetches"] = kwargs["max_network_fetches"]
            seen["timeframes"] = kwargs["timeframes"]
            seen["paper_provider_direct_http"] = (
                getattr(getattr(kwargs["provider"], "fallback", None), "http_get", None)
                is okx_public._httpx_get_direct
            )
            return {"generated": 0, "pfr_counts": {}, "state": {}, "gate_counts": {}}

        coordinator_seen: dict[str, int] = {}

        def fake_coordinator(*_args, **kwargs):
            coordinator_seen["max_plan_events"] = kwargs["max_plan_events"]
            coordinator_seen["max_discovery"] = kwargs["max_discovery"]
            coordinator_seen["max_validations"] = kwargs["max_validations"]
            return {
                "pivot": "smoke",
                "active_tasks": 0,
                "counters": {},
                "status": {},
                "errors": [],
            }

        monkeypatch.setattr(farm_loop, "_providers", lambda *_a, **_k: (None, None, None))
        monkeypatch.setattr(farm_loop, "_read_intake", lambda *_a, **_k: [])
        monkeypatch.setattr(farm_loop, "_discovery", lambda *_a, **_k: (None, {"status": "smoke"}))
        monkeypatch.setattr(farm_loop, "_refresh_live_universe", lambda *_a, **_k: {"status": "smoke"})
        monkeypatch.setattr(farm_loop, "_maybe_storage_maintain", lambda *_a, **_k: None)
        monkeypatch.setattr(farm_loop, "run_coordinator_cycle", fake_coordinator)
        monkeypatch.setattr(paper_cycle, "run_cycle", fake_cycle)

        class FakeOkxProvider:
            name = "fake-okx"
            configured = True

            def __init__(self, *, timeout, http_get=None) -> None:
                self.timeout = timeout
                self.http_get = http_get

        monkeypatch.setattr(okx_public, "OkxPublicMarketDataProvider", FakeOkxProvider)

        args = Namespace(
            max_plan_events=0,
            max_prepares=0,
            max_enrich=0,
            max_sweeps=0,
            run_worker=False,
            max_worker_jobs=0,
            max_validations=0,
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
            paper_signals_timeframes="15m,1h,4h",
            paper_signals_max_new=0,
            paper_signals_max_pfr_scan=0,
            paper_signals_max_pfr_fetches=0,
            paper_signals_pfr_reserved=0,
            paper_signals_max_observe=0,
            paper_signals_max_live_fetches=0,
            paper_signals_max_network_fetches=0,
            main_paper_runtime_limit=0,
            provider="synthetic",
            backend="cpu",
            data_days=None,
            enrich_funding=False,
            enrich_oi=False,
            run_journal_export=False,
            discovery_ttl_seconds=3600,
            no_discovery_refresh=True,
        )
        derived = tmp_path / "state" / "derived"
        derived.mkdir(parents=True)
        existing_observation = derived / "main_paper_runtime_observation.json"
        existing_observation.write_text(
            json.dumps({
                "schema": "main_paper_runtime_observation.v1",
                "rows_read": 5,
                "observed": 5,
                "reviewed": 5,
                "invalid": 0,
                "provider_error": 0,
                "execution_allowed": False,
            }),
            encoding="utf-8",
        )
        before = existing_observation.read_text(encoding="utf-8")

        out = farm_loop._run_once(args, object(), {}, {}, tmp_path, apply=True)

        assert coordinator_seen == {"max_plan_events": 0, "max_discovery": 0, "max_validations": 0}
        assert out["true_forward"]["skipped"] == "true_forward_max_candidates=0"
        assert seen == {
            "max_new": 0,
            "max_pfr_scan": 0,
            "max_pfr_fetches": 0,
            "pfr_reserved_new": 0,
            "max_observe": 0,
            "max_live_fetches": 0,
            "max_network_fetches": 0,
            "timeframes": ("15m", "1h", "4h"),
            "paper_provider_direct_http": True,
        }
        assert out["main_paper_runtime_queue"]["queued"] == 0
        assert out["main_paper_runtime_queue"]["execution_allowed"] is False
        assert out["main_paper_runtime_observation"]["rows_read"] == 0
        assert out["main_paper_runtime_observation"]["execution_allowed"] is False
        assert out["trade_thesis_supervisor"]["paper_only"] is True
        assert out["trade_thesis_supervisor"]["execution_allowed"] is False
        assert out["paper_telegram_delivery"]["dry_run"] is True
        assert out["paper_telegram_delivery"]["sends_network"] is False
        assert out["paper_telegram_delivery"]["execution_allowed"] is False
        assert out["paper_telegram_preview"]["skipped_quality_gate"] == 0
        assert out["paper_signal_training_export"]["rows"] == 0
        assert out["paper_signal_training_export"]["terminal_only"] is True
        assert out["paper_signal_training_export"]["paper_only"] is True
        assert out["product_signal_training_export"]["paper_only"] is True
        assert out["product_signal_training_export"]["execution_allowed"] is False
        assert out["paper_product_quality_report"]["paper_only"] is True
        assert out["paper_product_quality_report"]["execution_allowed"] is False
        assert existing_observation.read_text(encoding="utf-8") == before

    def test_visible_full_cycle_bat_bounds_paper_signal_observation(self) -> None:
        bat = Path("bat/strategy_lab_farm_full_cycle_loop.bat").read_text(encoding="utf-8")

        assert "STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE=20" in bat
        assert "STRATEGY_LAB_PAPER_SIGNALS_MAX_LIVE_FETCHES=12" in bat
        assert "STRATEGY_LAB_PAPER_SIGNALS_MAX_NETWORK_FETCHES=44" in bat
        assert "STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES=12" in bat
        assert "STRATEGY_LAB_LIVE_UNIVERSE_TTL_SECONDS=900" in bat
        assert "STRATEGY_LAB_LIVE_UNIVERSE_TOP_N=12" in bat
        assert "STRATEGY_LAB_PAPER_SIGNALS_FETCH_TIMEOUT=3" in bat
        assert "STRATEGY_LAB_FARM_MAX_VALIDATIONS=10" in bat
        assert "live_fetches=%STRATEGY_LAB_PAPER_SIGNALS_MAX_LIVE_FETCHES%" in bat
        assert "network_fetches=%STRATEGY_LAB_PAPER_SIGNALS_MAX_NETWORK_FETCHES%" in bat
        assert "pfr_fetches=%STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES%" in bat
        assert "live universe: ttl=%STRATEGY_LAB_LIVE_UNIVERSE_TTL_SECONDS%s" in bat
        assert "'--paper-signals-max-observe','%STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE%'" in bat
        assert "'--paper-signals-max-live-fetches','%STRATEGY_LAB_PAPER_SIGNALS_MAX_LIVE_FETCHES%'" in bat
        assert "'--paper-signals-max-network-fetches','%STRATEGY_LAB_PAPER_SIGNALS_MAX_NETWORK_FETCHES%'" in bat
        assert "'--paper-signals-max-pfr-fetches','%STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES%'" in bat
        assert "'--paper-signals-max-seconds','%STRATEGY_LAB_PAPER_SIGNALS_MAX_SECONDS%'" in bat
        assert "'--live-universe-ttl-seconds','%STRATEGY_LAB_LIVE_UNIVERSE_TTL_SECONDS%'" in bat
        assert "'--live-universe-top-n','%STRATEGY_LAB_LIVE_UNIVERSE_TOP_N%'" in bat
        assert "'--max-validations','%STRATEGY_LAB_FARM_MAX_VALIDATIONS%'" in bat
        assert "STRATEGY_LAB_PAPER_SIGNALS_PFR_RESERVED=2" in bat
        assert "'--paper-signals-pfr-reserved','%STRATEGY_LAB_PAPER_SIGNALS_PFR_RESERVED%'" in bat
        assert "STRATEGY_LAB_RUN_CALCULATOR_ADVISOR=1" in bat
        assert "STRATEGY_LAB_CALCULATOR_ADVISOR_MAX_CALLS=1" in bat
        assert "'--calculator-advisor-max-calls','%STRATEGY_LAB_CALCULATOR_ADVISOR_MAX_CALLS%'" in bat
        assert "STRATEGY_LAB_RUN_AGENT_ROLE_REVIEWS=0" in bat
        assert "STRATEGY_LAB_RUN_JOURNAL_EXPORT=1" in bat
        assert "'%STRATEGY_LAB_JOURNAL_EXPORT_ARG%'" in bat
        assert "private_fills=forced_off" in bat
        assert "'%STRATEGY_LAB_CALCULATOR_ADVISOR_ARG%'" in bat
        assert "'%STRATEGY_LAB_AGENT_ROLE_REVIEWS_ARG%'" in bat
        assert "'--agent-role-provider','%STRATEGY_LAB_AGENT_ROLE_PROVIDER%'" in bat
        assert "Tee-Object" not in bat
        assert "Add-Content -Path '%LOG_FILE%' -Value $line -Encoding UTF8" in bat

    def test_farm_loop_cli_default_matches_visible_pfr_budget(self) -> None:
        source = Path("scripts/strategy_lab/farm_loop.py").read_text(encoding="utf-8")

        assert 'STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES", "12"' in source
        assert 'paper_signals_max_pfr_fetches", 12' in source
        assert 'paper_signals_max_pfr_fetches", 8' not in source

    def test_visible_full_cycle_network_cap_covers_paper_signal_lanes(self) -> None:
        bat = Path("bat/strategy_lab_farm_full_cycle_loop.bat").read_text(encoding="utf-8")

        def default_int(name: str) -> int:
            marker = f"set \"{name}="
            line = next(item for item in bat.splitlines() if marker in item)
            return int(line.split(marker, 1)[1].split("\"", 1)[0])

        observe = default_int("STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE")
        live = default_int("STRATEGY_LAB_PAPER_SIGNALS_MAX_LIVE_FETCHES")
        pfr = default_int("STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_FETCHES")
        network = default_int("STRATEGY_LAB_PAPER_SIGNALS_MAX_NETWORK_FETCHES")

        assert network >= observe + live + pfr

    def test_journal_export_forces_private_fills_off_and_restores_env(self, monkeypatch, tmp_path) -> None:
        import scripts.build_journal as journal

        journal_path = tmp_path / "journal.xlsx"
        seen: dict[str, str | None] = {}

        def fake_build() -> None:
            seen["root"] = farm_loop.os.environ.get("TRADING_BOT_RESEARCH_ROOT")
            seen["private_fills"] = farm_loop.os.environ.get("JOURNAL_ENABLE_PRIVATE_FILLS")
            journal_path.write_bytes(b"xlsx")

        monkeypatch.setenv("TRADING_BOT_RESEARCH_ROOT", "old-root")
        monkeypatch.setenv("JOURNAL_ENABLE_PRIVATE_FILLS", "1")
        monkeypatch.setattr(journal, "build", fake_build)
        monkeypatch.setattr(journal, "JOURNAL_PATH", journal_path)

        out = farm_loop._run_journal_export_stage(tmp_path, apply=True)

        assert seen == {"root": str(tmp_path), "private_fills": "0"}
        assert out["status"] == "rebuilt"
        assert out["private_fills"] is False
        assert out["paper_only"] is True
        assert out["execution_allowed"] is False
        assert farm_loop.os.environ["TRADING_BOT_RESEARCH_ROOT"] == "old-root"
        assert farm_loop.os.environ["JOURNAL_ENABLE_PRIVATE_FILLS"] == "1"
