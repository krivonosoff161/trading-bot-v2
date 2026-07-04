"""Validated setup catalog for the paper-only main runtime.

This module turns hard-validator/PFR rows into a private, deterministic catalog.
It is not a trading engine and it never grants execution authority. The catalog
is the bridge identity between farm validation, PFR paper signals, main-paper
watch, Telegram previews, and training rows.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import stable_id, utc_now
from src.research_lab.paper_signals.pfr_bridge import apply_quality_policy, load_pfr_records

SCHEMA = "ReadyStrategyCatalogRow.v1"
SUMMARY_SCHEMA = "ready_strategy_catalog.v1"


@dataclass(frozen=True)
class ReadyStrategyCatalogRow:
    ready_strategy_id: str
    setup_id: str
    run_id: str
    candidate_id: str
    symbol: str
    okx_inst_id: str
    timeframe: str
    family: str
    params_hash: str
    status: str
    reasons: list[str] = field(default_factory=list)
    avg_net_pct: float | None = None
    win_rate: float | None = None
    n_trades: int | None = None
    max_drawdown_pct: float | None = None
    hard_status: str = ""
    paper_status: str = ""
    params_ref: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ready_strategy_id(row: dict[str, Any]) -> str:
    """Stable identity for one validated setup/parameter set."""
    payload = {
        "run_id": row.get("run_id"),
        "candidate_id": row.get("candidate_id"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "family": row.get("family"),
        "params_hash": row.get("params_hash"),
    }
    return stable_id("ready", payload, length=20)


def catalog_jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "ready_strategy_catalog.jsonl"


def catalog_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "ready_strategy_catalog.json"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row(record: dict[str, Any], *, status: str, reasons: list[str] | None = None) -> ReadyStrategyCatalogRow:
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    return ReadyStrategyCatalogRow(
        ready_strategy_id=ready_strategy_id(record),
        setup_id=str(record.get("setup_id") or f"setup-{record.get('candidate_id', '')}"),
        run_id=str(record.get("run_id") or ""),
        candidate_id=str(record.get("candidate_id") or ""),
        symbol=str(record.get("symbol") or ""),
        okx_inst_id=str(record.get("symbol") or "").replace("_", "-"),
        timeframe=str(record.get("timeframe") or ""),
        family=str(record.get("family") or ""),
        params_hash=str(record.get("params_hash") or ""),
        status=status,
        reasons=list(reasons or []),
        avg_net_pct=_float_or_none(record.get("avg_net_pct")),
        win_rate=_float_or_none(record.get("win_rate")),
        n_trades=_int_or_none(record.get("n_trades")),
        max_drawdown_pct=_float_or_none(record.get("max_drawdown_pct")),
        hard_status=str(record.get("hard_status") or ""),
        paper_status=str(record.get("paper_status") or ""),
        params_ref={
            "params_hash": str(record.get("params_hash") or ""),
            "keys": sorted(str(k) for k in params.keys()),
        },
    )


def build_ready_strategy_catalog(
    private_root: Path,
    pfr_db_path: Path | str,
    *,
    policy: dict[str, Any] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Build private ready/rejected catalog rows from the hard-validator PFR DB."""
    records = load_pfr_records(pfr_db_path)
    passed, rejected = apply_quality_policy(records, policy=policy)

    rows: list[ReadyStrategyCatalogRow] = []
    seen_ready: set[str] = set()
    duplicate_ready = 0
    for record in passed:
        row = _row(record, status="ready_for_paper_runtime", reasons=["quality_policy_passed"])
        if row.ready_strategy_id in seen_ready:
            duplicate_ready += 1
            continue
        seen_ready.add(row.ready_strategy_id)
        rows.append(row)
    for record in rejected:
        reasons = [str(x) for x in record.get("_rejection_reasons") or ["quality_policy_rejected"]]
        rows.append(_row(record, status="rejected_quality", reasons=reasons))

    by_status: dict[str, int] = {}
    by_family: dict[str, int] = {}
    by_timeframe: dict[str, int] = {}
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        if row.status == "ready_for_paper_runtime":
            by_family[row.family] = by_family.get(row.family, 0) + 1
            by_timeframe[row.timeframe] = by_timeframe.get(row.timeframe, 0) + 1

    jsonl_path = catalog_jsonl_path(private_root)
    snapshot_path = catalog_snapshot_path(private_root)
    summary = {
        "schema": SUMMARY_SCHEMA,
        "source_schema": "farm_results/candidates",
        "source_path": str(pfr_db_path),
        "records_loaded": len(records),
        "ready": by_status.get("ready_for_paper_runtime", 0),
        "rejected_quality": by_status.get("rejected_quality", 0),
        "duplicates_skipped": duplicate_ready,
        "by_status": by_status,
        "ready_by_family": by_family,
        "ready_by_timeframe": by_timeframe,
        "paper_only": True,
        "execution_allowed": False,
        "jsonl_path": str(jsonl_path),
        "snapshot_path": str(snapshot_path),
    }
    if write:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        snapshot_path.write_text(
            json.dumps({**summary, "items": [row.to_dict() for row in rows]}, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    return summary
