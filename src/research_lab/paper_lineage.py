"""Unified derived lineage index for the paper/research lifecycle.

The index joins existing IDs; it does not replace source artifacts or invent
missing validator evidence. Raw market data remains outside the index.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import stable_id

SCHEMA = "PaperLineageEnvelope.v1"
SUMMARY_SCHEMA = "paper_lineage_index.v1"

ID_FIELDS = (
    "scanner_event_id",
    "data_packet_id",
    "feature_packet_id",
    "setup_candidate_id",
    "sweep_run_id",
    "validation_id",
    "setup_id",
    "candidate_id",
    "ready_strategy_id",
    "instruction_id",
    "consumer_id",
    "runtime_id",
    "paper_trade_id",
    "paper_product_trade_id",
    "paper_account_scenario_id",
    "thesis_id",
    "telegram_card_id",
    "outcome_id",
    "outcome_review_id",
    "training_row_id",
)
MULTI_ID_FIELDS = {"telegram_card_id"}


def _derived(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived"


def _snapshot_path(private_root: Path) -> Path:
    return _derived(private_root) / "paper_lineage.json"


def _jsonl_path(private_root: Path) -> Path:
    return _derived(private_root) / "paper_lineage.jsonl"


def _load_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("items") if isinstance(payload, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _source_id(row: dict[str, Any]) -> str:
    return str(row.get("source_signal_id") or row.get("paper_signal_id") or row.get("signal_id") or "")


def _put(bucket: dict[str, set[str]], field: str, value: Any) -> None:
    if field not in ID_FIELDS or value in (None, ""):
        return
    bucket.setdefault(field, set()).add(str(value))


def _merge(bucket: dict[str, set[str]], row: dict[str, Any], *, aliases: dict[str, str] | None = None) -> None:
    aliases = aliases or {}
    for field in ID_FIELDS:
        _put(bucket, field, row.get(aliases.get(field, field)))


def build_paper_lineage(private_root: Path) -> dict[str, Any]:
    private_root = Path(private_root)
    derived = _derived(private_root)
    preview_rows = _load_items(derived / "paper_telegram_card_ledger.json")
    if not preview_rows:
        preview_rows = _load_items(derived / "paper_telegram_preview.json")
    training_rows = _load_jsonl(derived / "paper_signal_training.jsonl")
    if not training_rows:
        training_rows = _load_items(derived / "paper_signal_training.json")
    surfaces = {
        "product": _load_items(derived / "paper_product_trades.json"),
        "queue": _load_items(derived / "main_paper_runtime_queue.json"),
        "trades": _load_items(derived / "main_paper_trades.json"),
        "preview": preview_rows,
        "training": training_rows,
    }
    thesis_payload = {}
    thesis_path = derived / "trade_thesis_supervisor.json"
    if thesis_path.exists():
        try:
            thesis_payload = json.loads(thesis_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            thesis_payload = {}
    thesis_events = _load_jsonl(derived / "trade_thesis_events.jsonl")
    if not thesis_events:
        thesis_events = [row for row in thesis_payload.get("event_items") or [] if isinstance(row, dict)]
    thesis_items = _load_jsonl(derived / "trade_theses.jsonl")
    if not thesis_items:
        thesis_items = [row for row in thesis_payload.get("items") or [] if isinstance(row, dict)]
    account_events = _load_jsonl(derived / "paper_account_events.jsonl")

    all_source_ids = {
        source_id
        for rows in surfaces.values()
        for row in rows
        if (source_id := _source_id(row)) and not source_id.startswith("scenario_")
    }
    all_source_ids.update(_source_id(row) for row in thesis_events if _source_id(row))
    all_source_ids.update(
        str(row.get("primary_signal_id") or row.get("source_signal_id") or "")
        for row in thesis_items
        if str(row.get("primary_signal_id") or row.get("source_signal_id") or "")
    )
    all_source_ids.update(_source_id(row) for row in account_events if _source_id(row))
    buckets: dict[str, dict[str, set[str]]] = {source_id: {} for source_id in all_source_ids}
    seen_surfaces: dict[str, set[str]] = {source_id: set() for source_id in all_source_ids}
    metadata: dict[str, dict[str, str]] = {source_id: {} for source_id in all_source_ids}

    for surface, rows in surfaces.items():
        for row in rows:
            source_id = _source_id(row)
            if source_id not in buckets:
                continue
            seen_surfaces[source_id].add(surface)
            lineage_row = dict(row)
            if surface == "product":
                # The broad product ledger keeps a backwards-compatible
                # paper_trade_id alias; it is not the strict main trade ID.
                lineage_row.pop("paper_trade_id", None)
            _merge(
                buckets[source_id],
                lineage_row,
                aliases={
                    "runtime_id": "main_paper_runtime_id" if surface == "training" else "runtime_id",
                    "paper_signal_id": "paper_signal_id",
                },
            )
            _put(buckets[source_id], "paper_signal_id", source_id)
            for target, source in (
                ("instrument", "okx_inst_id"),
                ("timeframe", "timeframe"),
                ("setup_family", "setup_family"),
                ("source", "source"),
            ):
                value = str(row.get(source) or row.get("family") or "") if target == "setup_family" else str(row.get(source) or "")
                if value and not metadata[source_id].get(target):
                    metadata[source_id][target] = value

    for row in thesis_events:
        source_id = _source_id(row)
        if source_id in buckets:
            seen_surfaces[source_id].add("thesis")
            _put(buckets[source_id], "thesis_id", row.get("thesis_id"))
    for row in thesis_items:
        source_id = str(row.get("primary_signal_id") or row.get("source_signal_id") or "")
        if source_id in buckets:
            seen_surfaces[source_id].add("thesis")
            _put(buckets[source_id], "thesis_id", row.get("thesis_id"))
    for row in account_events:
        source_id = _source_id(row)
        if source_id in buckets:
            seen_surfaces[source_id].add("account")
            _put(buckets[source_id], "paper_account_scenario_id", row.get("scenario_id"))

    envelopes: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for source_id in sorted(buckets):
        values = buckets[source_id]
        ids: dict[str, str] = {}
        for field in ID_FIELDS:
            candidates = sorted(values.get(field) or [])
            ids[field] = candidates[0] if candidates else ""
            if field in MULTI_ID_FIELDS:
                ids[f"{field}s"] = candidates
            elif len(candidates) > 1:
                conflicts.append({"source_signal_id": source_id, "field": field, "values": candidates})
        envelope = {
            "schema": SCHEMA,
            "lineage_id": stable_id("paperlineage", {"source_signal_id": source_id}, length=20),
            "source_signal_id": source_id,
            **ids,
            **metadata[source_id],
            "surfaces": sorted(seen_surfaces[source_id]),
            "paper_only": True,
            "execution_allowed": False,
        }
        envelopes.append(envelope)

    queue_ids = {_source_id(row) for row in surfaces["queue"] if _source_id(row)}
    trade_ids = {_source_id(row) for row in surfaces["trades"] if _source_id(row)}
    terminal_ids = {
        _source_id(row)
        for row in surfaces["product"]
        if _source_id(row) and str(row.get("status") or "") not in {"armed", "opened_paper"}
    }
    training_ids = {_source_id(row) for row in surfaces["training"] if _source_id(row)}
    main_without_trade = sorted(queue_ids - trade_ids)
    terminal_without_training = sorted(terminal_ids - training_ids)
    summary = {
        "schema": SUMMARY_SCHEMA,
        "row_schema": SCHEMA,
        "envelopes": len(envelopes),
        "conflicts": len(conflicts),
        "conflict_samples": conflicts[:20],
        "main_queue_rows": len(queue_ids),
        "main_without_trade": len(main_without_trade),
        "main_without_trade_samples": main_without_trade[:20],
        "terminal_product_rows": len(terminal_ids),
        "terminal_without_training": len(terminal_without_training),
        "terminal_without_training_samples": terminal_without_training[:20],
        "complete_main_lineage": len(queue_ids & trade_ids),
        "valid": not conflicts and not main_without_trade,
        "paper_only": True,
        "execution_allowed": False,
        "jsonl_path": str(_jsonl_path(private_root)),
        "snapshot_path": str(_snapshot_path(private_root)),
        "items": envelopes,
    }
    _jsonl_path(private_root).parent.mkdir(parents=True, exist_ok=True)
    with _jsonl_path(private_root).open("w", encoding="utf-8") as fh:
        for row in envelopes:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    _snapshot_path(private_root).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
