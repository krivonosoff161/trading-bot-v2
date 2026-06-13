# -*- coding: utf-8 -*-
"""Private storage for typed proposals: one JSONL under the private research root.

Public code defines the store mechanics; proposal payloads stay private. Upserts
are keyed by proposal_id and preserve created_at so regeneration is idempotent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.research_lab.proposal_schema import Proposal, coerce_proposal


def proposals_path(private_root: Path) -> Path:
    return private_root / "proposals" / "proposals.jsonl"


def queued_spec_dir(private_root: Path) -> Path:
    return private_root / "proposals" / "queued_specs"


def load_proposals(path: Path) -> list[Proposal]:
    if not path.exists():
        return []
    out: list[Proposal] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(coerce_proposal(json.loads(line)))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def upsert_proposals(path: Path, proposals: list[Proposal]) -> dict[str, int]:
    """Insert/update by proposal_id; preserve existing created_at; deterministic order."""
    index = {p.proposal_id: p for p in load_proposals(path)}
    stats = {"added": 0, "updated": 0}
    for proposal in proposals:
        old = index.get(proposal.proposal_id)
        if old is not None:
            from dataclasses import replace
            proposal = replace(proposal, created_at=old.created_at or proposal.created_at)
            stats["updated"] += 1
        else:
            stats["added"] += 1
        index[proposal.proposal_id] = proposal
    _write_all(path, [index[k] for k in sorted(index)])
    stats["total"] = len(index)
    return stats


def set_status(path: Path, proposal_id: str, status: str, *, rejection_reason: str = "") -> bool:
    from dataclasses import replace
    proposals = load_proposals(path)
    found = False
    for i, p in enumerate(proposals):
        if p.proposal_id == proposal_id:
            proposals[i] = replace(p, status=status, rejection_reason=rejection_reason)
            found = True
    if found:
        _write_all(path, proposals)
    return found


def status_counts(proposals: list[Proposal]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in proposals:
        counts[p.status] = counts.get(p.status, 0) + 1
    return counts


def _write_all(path: Path, proposals: list[Proposal]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for proposal in proposals:
            f.write(json.dumps(proposal.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
