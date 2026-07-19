"""Explicit synthetic adapters for the fixed Package 08B segment streams.

No function discovers a root, activates a store, or reads/writes a legacy path.
"""

from __future__ import annotations

from typing import Any

from src.research_lab.storage_segment_store import FIXED_STREAMS, SegmentStore


def append_record(
    store: SegmentStore,
    stream_id: str,
    record: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    if stream_id not in FIXED_STREAMS:
        raise ValueError("unknown fixed segment stream")
    return store.append(stream_id, record, request_id=request_id)


def read_stream_records(store: SegmentStore, stream_id: str) -> list[dict[str, Any]]:
    if stream_id not in FIXED_STREAMS:
        raise ValueError("unknown fixed segment stream")
    return store.read_records(stream_id)


def append_farm_cycle(store: SegmentStore, record: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    return append_record(store, "farm.cycle", record, request_id=request_id)


def append_farm_task_transition(store: SegmentStore, record: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    return append_record(store, "farm.task_transition", record, request_id=request_id)


def append_farm_error(store: SegmentStore, record: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    return append_record(store, "farm.error", record, request_id=request_id)


def append_scout_card(store: SegmentStore, record: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    return append_record(store, "scout.card", record, request_id=request_id)


def append_scout_drop(store: SegmentStore, record: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    return append_record(store, "scout.drop", record, request_id=request_id)


def append_scout_llm_budget(store: SegmentStore, record: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    return append_record(store, "scout.llm_budget", record, request_id=request_id)


def append_scout_ingest(store: SegmentStore, record: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    return append_record(store, "scout.ingest", record, request_id=request_id)


def append_scout_event_audit(store: SegmentStore, record: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    return append_record(store, "scout.event_audit", record, request_id=request_id)


def append_scout_routing_audit(store: SegmentStore, record: dict[str, Any], *, request_id: str) -> dict[str, Any]:
    return append_record(store, "scout.routing_audit", record, request_id=request_id)


def read_farm_cycles(store: SegmentStore) -> list[dict[str, Any]]:
    return read_stream_records(store, "farm.cycle")


def read_scout_cards(store: SegmentStore) -> list[dict[str, Any]]:
    return read_stream_records(store, "scout.card")
