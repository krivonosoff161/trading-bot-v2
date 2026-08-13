from __future__ import annotations

import datetime as dt
from typing import Any

from scripts.strategy_lab import farm_loop
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
from src.research_lab.intake_adapter import watch_to_intake, watches_to_intake


def _watch(
    index: int,
    *,
    created_at: dt.datetime,
    verdict: str = "WATCH",
    event_type: str = "market_move",
) -> dict[str, Any]:
    symbol = f"ASSET{index}-USDT-SWAP"
    return {
        "watch_id": f"watch-{index}",
        "card_id": f"card-{index}",
        "created_at": created_at.isoformat(),
        "scanner": {
            "verdict": verdict,
            "event_type": event_type,
            "escalation_gate": verdict,
        },
        "asset": {
            "symbol": symbol,
            "okx_inst": symbol,
            "okx_asset_class": "crypto_alt",
        },
        "farm": {"eligible": True, "selected_timeframe": "1h"},
        "trigger": {"source": "scanner", "catalyst": f"move-{index}"},
    }


def test_known_open_watch_prefix_cannot_hide_fresh_events_after_limit() -> None:
    base = dt.datetime(2026, 8, 13, 8, 0, tzinfo=dt.UTC)
    old = [_watch(index, created_at=base + dt.timedelta(minutes=index)) for index in range(20)]
    fresh = [
        _watch(100, created_at=base + dt.timedelta(hours=2)),
        _watch(101, created_at=base + dt.timedelta(hours=3)),
    ]
    known_ids = {str(watch_to_intake(watch)["event_id"]) for watch in old}

    selected = watches_to_intake(
        old + fresh,
        known_event_ids=known_ids,
        limit=2,
    )

    assert [event["symbol"] for event in selected] == [
        "ASSET101-USDT-SWAP",
        "ASSET100-USDT-SWAP",
    ]


def test_selection_orders_priority_then_newest_with_stable_ties() -> None:
    base = dt.datetime(2026, 8, 13, 8, 0, tzinfo=dt.UTC)
    older_watch = _watch(1, created_at=base)
    newer_watch = _watch(2, created_at=base + dt.timedelta(minutes=5))
    go = _watch(3, created_at=base - dt.timedelta(hours=1), verdict="GO")

    selected = watches_to_intake([older_watch, newer_watch, go])

    assert [event["symbol"] for event in selected] == [
        "ASSET3-USDT-SWAP",
        "ASSET2-USDT-SWAP",
        "ASSET1-USDT-SWAP",
    ]


def test_newest_duplicate_representation_wins_before_bounded_selection() -> None:
    base = dt.datetime(2026, 8, 13, 8, 0, tzinfo=dt.UTC)
    older = _watch(7, created_at=base)
    newer = _watch(7, created_at=base + dt.timedelta(minutes=30))
    selected = watches_to_intake([older, newer], limit=1)

    assert len(selected) == 1
    assert selected[0]["observed_at"] == base.timestamp() + 30 * 60


def test_non_positive_limit_returns_no_events() -> None:
    base = dt.datetime(2026, 8, 13, 8, 0, tzinfo=dt.UTC)

    assert watches_to_intake([_watch(1, created_at=base)], limit=0) == []


def test_continuous_fresh_arrivals_cannot_starve_oldest_unseen_event() -> None:
    base = dt.datetime(2026, 8, 13, 8, 0, tzinfo=dt.UTC)
    watches = [
        _watch(index, created_at=base + dt.timedelta(minutes=index))
        for index in range(1, 6)
    ]
    known: set[str] = set()

    for cycle in range(3):
        watches.extend([
            _watch(10 + cycle * 2, created_at=base + dt.timedelta(hours=cycle + 1)),
            _watch(11 + cycle * 2, created_at=base + dt.timedelta(hours=cycle + 1, minutes=1)),
        ])
        selected = watches_to_intake(watches, known_event_ids=known, limit=2)
        known.update(str(event["event_id"]) for event in selected)

    oldest = watch_to_intake(watches[0])
    assert oldest is not None
    assert oldest["event_id"] in known


def test_canonical_farm_reader_queries_known_ids_before_bounding(
    tmp_path, monkeypatch
) -> None:
    base = dt.datetime(2026, 8, 13, 8, 0, tzinfo=dt.UTC)
    old = [_watch(index, created_at=base) for index in range(20)]
    fresh = [
        _watch(100, created_at=base + dt.timedelta(hours=2)),
        _watch(101, created_at=base + dt.timedelta(hours=3)),
    ]
    store = FarmTasksDB(tasks_db_path(tmp_path))
    for watch in old:
        event = watch_to_intake(watch)
        assert event is not None
        store.upsert_intake_event(event, now=base.timestamp())
    monkeypatch.setattr("src.scout.watch_queue.open_watches", lambda: old + fresh)
    metrics: dict[str, Any] = {}

    selected = farm_loop._read_intake(2, tasks=store, metrics=metrics)

    assert [event["symbol"] for event in selected] == [
        "ASSET101-USDT-SWAP",
        "ASSET100-USDT-SWAP",
    ]
    assert metrics["already_ingested"] == 20
    assert metrics["uningested_events"] == 2
    assert metrics["remaining_after_selection"] == 0
    store.close()
