# -*- coding: utf-8 -*-
"""Typed proposal schema for the closed research loop.

A proposal is the structured request for the *next* experiment. It is a research
label, never a profitability claim and never a live-trading instruction. Proposals
flow PROPOSED -> VALIDATED/REJECTED -> QUEUED and live only in the private root.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

# status lifecycle
PROPOSED = "PROPOSED"
VALIDATED = "VALIDATED"
REJECTED = "REJECTED"
QUEUED = "QUEUED"
STATUSES = {PROPOSED, VALIDATED, REJECTED, QUEUED}

CREATED_BY = {"llm_review", "rule_based", "manual"}
SCHEMA = "strategy_lab_proposal.v2"
_REQUIRED = ("created_by", "hypothesis", "requested_timeframe", "setup_family")


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    created_by: str
    hypothesis: str
    requested_universe: str = ""
    requested_timeframe: str = ""
    setup_family: str = ""
    symbols: list[str] = field(default_factory=list)
    filters: dict[str, list[str]] = field(default_factory=dict)
    parameter_grid: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    max_variants: int = 12
    reason_codes: list[str] = field(default_factory=list)
    expected_validation: str = ""
    risk_flags: list[str] = field(default_factory=list)
    private_notes: str = ""
    source_run_id: str = ""
    source_pack_id: str = ""
    status: str = PROPOSED
    rejection_reason: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, **asdict(self)}


def proposal_id_for(payload: dict[str, Any]) -> str:
    """Deterministic id from the research-defining fields (idempotent regeneration)."""
    key = {
        "created_by": payload.get("created_by"),
        "hypothesis": payload.get("hypothesis") or "",
        "setup_family": payload.get("setup_family"),
        "requested_timeframe": payload.get("requested_timeframe"),
        "symbols": sorted(payload.get("symbols") or []),
        "filters": payload.get("filters") or {},
        "parameter_grid": payload.get("parameter_grid") or {},
        "source_run_id": payload.get("source_run_id") or "",
    }
    raw = json.dumps(key, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def coerce_proposal(payload: dict[str, Any]) -> Proposal:
    """Build a Proposal from a dict, filling defaults and validating required keys."""
    if not isinstance(payload, dict):
        raise ValueError("proposal must be an object")
    status = str(payload.get("status") or PROPOSED)
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    # REJECTED records are an audit trail and may be incomplete; runnable statuses
    # (PROPOSED / VALIDATED / QUEUED) must carry the full required fields.
    if status != REJECTED:
        for key in _REQUIRED:
            if not str(payload.get(key) or "").strip():
                raise ValueError(f"proposal missing required field: {key}")
    created_by = str(payload.get("created_by") or "")
    if created_by not in CREATED_BY:
        raise ValueError(f"invalid created_by: {created_by}")
    pid = str(payload.get("proposal_id") or "").strip() or proposal_id_for(payload)
    return Proposal(
        proposal_id=pid,
        created_by=created_by,
        hypothesis=str(payload.get("hypothesis") or ""),
        requested_universe=str(payload.get("requested_universe") or ""),
        requested_timeframe=str(payload.get("requested_timeframe") or ""),
        setup_family=str(payload.get("setup_family") or ""),
        symbols=[str(s) for s in (payload.get("symbols") or [])],
        filters={str(k): [str(x) for x in v] for k, v in (payload.get("filters") or {}).items()},
        parameter_grid=_coerce_grid(payload.get("parameter_grid")),
        max_variants=int(payload.get("max_variants") or 12),
        reason_codes=[str(r) for r in (payload.get("reason_codes") or [])],
        expected_validation=str(payload.get("expected_validation") or ""),
        risk_flags=[str(r) for r in (payload.get("risk_flags") or [])],
        private_notes=str(payload.get("private_notes") or ""),
        source_run_id=str(payload.get("source_run_id") or ""),
        source_pack_id=str(payload.get("source_pack_id") or ""),
        status=status,
        rejection_reason=str(payload.get("rejection_reason") or ""),
        created_at=str(payload.get("created_at") or ""),
    )


def _coerce_grid(raw: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for family, variants in raw.items():
        if isinstance(variants, list):
            out[str(family)] = [dict(v) for v in variants if isinstance(v, dict)]
    return out
