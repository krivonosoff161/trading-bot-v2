# -*- coding: utf-8 -*-
"""Durable store for paper-watch signals — append-only JSONL + a current-state view (research-only).

The JSONL (`state/derived/paper_signals.jsonl`) is the audit log (one row per signal version); the latest
row per signal_id is the current state. Dashboard/graph read the rebuilt current-state view. No DB locks,
no execution path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.paper_signals.contract import PaperActionSignal, validate_signal


def _jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_signals.jsonl"


def append_signal(private_root: Path, sig: PaperActionSignal) -> Path:
    """Validate then append. A signal that fails the deterministic gate is rejected (never stored)."""
    ok, problems = validate_signal(sig)
    if not ok:
        raise ValueError(f"invalid paper signal {sig.signal_id}: {problems}")
    path = _jsonl_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sig.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return path


def update_signal(private_root: Path, sig: PaperActionSignal) -> Path:
    """Append a new version (status/outcome/review changed). Latest row per signal_id wins."""
    return append_signal(private_root, sig)


def load_signals(private_root: Path) -> list[PaperActionSignal]:
    """Current state: the latest version of each signal_id, in first-seen order."""
    path = _jsonl_path(private_root)
    if not path.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = row.get("signal_id")
        if not sid:
            continue
        if sid not in latest:
            order.append(sid)
        latest[sid] = row
    return [PaperActionSignal.from_dict(latest[sid]) for sid in order]


def current_state_view(private_root: Path) -> dict[str, Any]:
    """Compact summary for the dashboard/graph: counts by status + the active cards."""
    sigs = load_signals(private_root)
    by_status: dict[str, int] = {}
    for s in sigs:
        by_status[s.status] = by_status.get(s.status, 0) + 1
    active = [s.to_dict() for s in sigs if s.status in ("candidate", "armed", "opened_paper")]
    return {"schema": "paper_signals.v1", "total": len(sigs), "by_status": by_status,
            "active": active, "all_research_only": True,
            "disclaimer": "Paper-watch signals — NOT orders, NOT live trades; outcome != edge."}


def write_state_snapshot(private_root: Path) -> Path:
    """Persist the current-state view so dashboard/graph can read it without parsing the JSONL."""
    out = Path(private_root) / "state" / "derived" / "paper_signals.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(current_state_view(private_root), ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n", encoding="utf-8")
    return out
