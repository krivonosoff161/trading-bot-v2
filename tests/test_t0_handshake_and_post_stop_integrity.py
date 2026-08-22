from __future__ import annotations

import threading
from argparse import Namespace

import pytest

from scripts.strategy_lab import farm_loop


def test_generation_wake_latch_preserves_second_publication_during_reentry() -> None:
    wake = farm_loop._CurrentGenerationWakeup()
    wake.set()
    receipt: list[str] = []

    assert farm_loop._sleep_until_next_cycle(
        1, wake_event=wake, wake_receipt=receipt
    ) is True
    assert receipt == ["current_generation_published"]
    assert wake.is_set() is True

    # A new validation publication that races the first bounded re-entry must
    # survive acknowledgement of the first one.
    wake.set()
    assert wake.acknowledge() is True
    assert wake.is_set() is True
    assert wake.acknowledge() is True
    assert wake.is_set() is False


def test_generic_event_keeps_legacy_clear_on_wake() -> None:
    wake = threading.Event()
    wake.set()
    receipt: list[str] = []

    assert farm_loop._sleep_until_next_cycle(
        1, wake_event=wake, wake_receipt=receipt
    ) is True
    assert receipt == ["current_generation_published"]
    assert wake.is_set() is False


def test_current_generation_reentry_bypasses_generic_prefix_and_requires_delivery(
    monkeypatch, tmp_path
) -> None:
    seen: list[str] = []

    class Runtime:
        def raise_if_failed(self) -> None:
            return None

    class Tasks:
        def eligible_count(self) -> int:
            return 0

        def status_counts(self) -> dict[str, int]:
            return {}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("generic full-cycle work must not run in re-entry")

    monkeypatch.setattr(farm_loop, "_providers", forbidden)
    monkeypatch.setattr(farm_loop, "_read_intake", forbidden)
    monkeypatch.setattr(farm_loop, "_discovery", forbidden)
    monkeypatch.setattr(farm_loop, "run_coordinator_cycle", forbidden)
    monkeypatch.setattr(farm_loop, "_refresh_live_universe", forbidden)
    monkeypatch.setattr(farm_loop, "_write_loop_status", lambda *_a, **_k: None)

    from src.research_lab.paper_signals import cycle as paper_cycle
    from src.research_lab import paper_telegram_sender

    def paper_cycle_run(*_args, **kwargs):
        seen.append("pfr")
        assert kwargs["max_observe"] == 0
        assert kwargs["max_live_fetches"] == 0
        assert kwargs["pfr_reserved_new"] == kwargs["max_new"]
        assert kwargs["require_known_bad_authority"] is True
        return {"paper_only": True}

    monkeypatch.setattr(paper_cycle, "run_cycle", paper_cycle_run)

    def current_chain(*_args, out, **_kwargs):
        seen.append("current_v2")
        out["paper_generation_v2"] = {"state": "ready", "run_id": "run-current"}

    monkeypatch.setattr(farm_loop, "_run_main_paper_derived_chain", current_chain)
    monkeypatch.setattr(
        farm_loop,
        "_paper_telegram_delivery_config",
        lambda *_a, **_k: {
            "apply": False,
            "configured": False,
            "ids": [],
            "send_text": None,
            "send_photo": None,
        },
    )
    monkeypatch.setattr(
        paper_telegram_sender,
        "send_paper_telegram_previews",
        lambda *_a, **_k: seen.append("delivery")
        or {
            "paper_generation_run_id": "run-current",
            "current_generation_compatible": True,
        },
    )

    def checkpoint(*_args, out, **_kwargs):
        seen.append("checkpoint")
        out["mandatory_product_cycle_complete"] = True

    monkeypatch.setattr(farm_loop, "_run_v2_post_delivery_maintenance_chain", checkpoint)
    args = Namespace(
        paper_evidence_v2_required=True,
        paper_generation_runtime=Runtime(),
        run_paper_signals=True,
        loop=True,
        stop_file="",
        pfr_db_path="",
        paper_signals_fetch_timeout=1.0,
        paper_signals_timeframes="15m",
        paper_signals_max_new=2,
        paper_signals_max_pfr_scan=2,
        paper_signals_max_pfr_fetches=2,
        paper_signals_max_network_fetches=2,
        paper_signals_max_seconds=1.0,
        paper_telegram_limit=2,
        paper_telegram_status_digest=False,
        paper_telegram_status_digest_hours=12,
    )

    out = farm_loop._run_once(
        args,
        Tasks(),
        {},
        {},
        tmp_path,
        apply=True,
        current_generation_reentry=True,
    )

    assert seen == ["pfr", "current_v2", "delivery", "checkpoint"]
    assert out["mandatory_product_cycle_complete"] is True
    assert out["startup_product_reentry"] == {
        "state": "completed",
        "paper_generation_run_id": "run-current",
        "paper_only": True,
        "execution_allowed": False,
    }


def test_current_generation_reentry_waits_fail_closed_without_delivery(
    monkeypatch, tmp_path
) -> None:
    class Runtime:
        def raise_if_failed(self) -> None:
            return None

    class Tasks:
        def eligible_count(self) -> int:
            return 0

        def status_counts(self) -> dict[str, int]:
            return {}

    monkeypatch.setattr(farm_loop, "_write_loop_status", lambda *_a, **_k: None)
    from src.research_lab.paper_signals import cycle as paper_cycle

    monkeypatch.setattr(paper_cycle, "run_cycle", lambda *_a, **_k: {})

    def waiting(*_args, out, **_kwargs):
        out["paper_generation_v2"] = {"state": "waiting_validation_generation"}
        raise farm_loop._ValidationGenerationWaiting("pending")

    monkeypatch.setattr(farm_loop, "_run_main_paper_derived_chain", waiting)
    args = Namespace(
        paper_evidence_v2_required=True,
        paper_generation_runtime=Runtime(),
        run_paper_signals=True,
        loop=True,
        stop_file="",
        pfr_db_path="",
        paper_signals_fetch_timeout=1.0,
        paper_signals_timeframes="15m",
        paper_signals_max_new=0,
        paper_signals_max_pfr_scan=0,
        paper_signals_max_pfr_fetches=0,
        paper_signals_max_network_fetches=0,
        paper_signals_max_seconds=1.0,
    )

    out = farm_loop._run_once(
        args,
        Tasks(),
        {},
        {},
        tmp_path,
        apply=True,
        current_generation_reentry=True,
    )

    assert out["startup_product_reentry"]["state"] == "current_generation_not_ready"
    assert out["paper_telegram_delivery"]["skipped"] == "validation_generation_waiting"
    assert "mandatory_product_cycle_complete" not in out


def test_current_generation_reentry_rejects_lost_claim_before_pfr_side_effect(
    monkeypatch, tmp_path
) -> None:
    class Runtime:
        def raise_if_failed(self) -> None:
            return None

    class FailedClaim:
        def raise_if_failed(self) -> None:
            raise RuntimeError("synthetic claim loss")

    class Tasks:
        def eligible_count(self) -> int:
            return 0

        def status_counts(self) -> dict[str, int]:
            return {}

    from src.research_lab.paper_signals import cycle as paper_cycle

    monkeypatch.setattr(
        paper_cycle,
        "run_cycle",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("PFR must not run after claim loss")
        ),
    )
    args = Namespace(
        paper_evidence_v2_required=True,
        paper_generation_runtime=Runtime(),
        task_claim_failure_signal=FailedClaim(),
        run_paper_signals=True,
    )

    with pytest.raises(RuntimeError, match="claim loss"):
        farm_loop._run_once(
            args,
            Tasks(),
            {},
            {},
            tmp_path,
            apply=True,
            current_generation_reentry=True,
        )
