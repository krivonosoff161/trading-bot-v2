# -*- coding: utf-8 -*-
"""Universe-refill orchestration: prepare -> multi-family plan -> queue.

Runs the pure work-list units from universe_refill: for each (group, timeframe,
families) unit it prepares any missing candles (bounded, with per-symbol failure
backoff so a permanently-unfetchable symbol is not retried forever), builds the
multi-family research plan on the private market_data glob, and idempotently
queues each spec. Injectable provider + conn make it testable with a fake provider
and an in-memory DB (no network, no real disk). Paper/research only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.data_inventory import build_inventory
from src.research_lab.data_prepare import prepare_market_data
from src.research_lab.experiment import choose_symbol_file
from src.research_lab.paths import market_data_glob, resolve_private_root
from src.research_lab.research_plan import build_research_plan
from src.research_lab.state_db import ensure_experiment_queued
from src.research_lab.universe_refill import RefillUnit, prepare_items

SMOKE_SYMBOLS = 3
COUNTER_KEYS = ("units", "jobs_queued", "already_queued", "prepared", "would_prepare",
                "would_queue", "prepare_failed", "prepare_skipped_backoff")


@dataclass
class RefillState:
    """Cursor + per-symbol prepare-failure backoff, persisted as one small json."""
    cursor: int = 0
    failures: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "RefillState":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(cursor=int(data.get("cursor", 0)), failures=dict(data.get("failures") or {}))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"cursor": self.cursor, "failures": self.failures},
                                   ensure_ascii=False), encoding="utf-8")

    def backed_off(self, key: str, max_failures: int) -> bool:
        return self.failures.get(key, 0) >= max_failures

    def record_failure(self, key: str) -> None:
        self.failures[key] = self.failures.get(key, 0) + 1

    def clear_failure(self, key: str) -> None:
        self.failures.pop(key, None)


def _symbol_cap(profile: Any, full: bool) -> int:
    return profile.max_symbols_per_cycle if full else min(profile.max_symbols_per_cycle, SMOKE_SYMBOLS)


def _prepare_missing(symbols, unit, *, glob, state, provider, private_root, apply, now_ms,
                     data_days, prepares_left, max_failures, allow_public_output, counters, eff):
    """Prepare any symbol without usable data; return remaining prepare budget."""
    for sym in symbols:
        if choose_symbol_file(glob, sym, timeframe=unit.timeframe):
            continue
        if not apply:
            counters["would_prepare"] += 1
            continue
        key = f"{sym}::{unit.timeframe}"
        if state.backed_off(key, max_failures):
            counters["prepare_skipped_backoff"] += 1
            eff["skipped"].append({"symbol": sym, "reason": "prepare_backoff"})
            continue
        if prepares_left <= 0:
            eff["skipped"].append({"symbol": sym, "reason": "prepare_cap_reached"})
            continue
        items = prepare_items([sym], unit.timeframe, now_ms, data_days)
        report = prepare_market_data(items, provider=provider, private_root=private_root,
                                     timeframe=unit.timeframe, apply=True,
                                     allow_public_output=allow_public_output)
        prepares_left -= 1
        if report.downloaded > 0 and choose_symbol_file(glob, sym, timeframe=unit.timeframe):
            counters["prepared"] += 1
            state.clear_failure(key)
        else:
            counters["prepare_failed"] += 1
            state.record_failure(key)
            reason = report.skipped[0]["reason"] if report.skipped else "too_short"
            eff["skipped"].append({"symbol": sym, "reason": reason})
    return prepares_left


def _queue_plan(plan, unit, *, private_root, conn, apply, priority, counters, eff) -> None:
    for job in plan.jobs:
        if not apply:
            counters["would_queue"] += 1
            eff["decisions"].append({"group": unit.group, "timeframe": unit.timeframe,
                                     "families": list(job.families), "symbols": len(job.symbols),
                                     "action": "would_queue"})
            continue
        out_dir = private_root / "plans" / "specs"
        out_dir.mkdir(parents=True, exist_ok=True)
        spec_path = out_dir / f"{job.experiment_id}.json"
        spec_path.write_text(json.dumps(job.spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _, created = ensure_experiment_queued(conn, spec_path.resolve(), priority=priority)
        counters["jobs_queued" if created else "already_queued"] += 1
        eff["decisions"].append({"group": unit.group, "timeframe": unit.timeframe,
                                 "experiment_id": job.experiment_id, "symbols": len(job.symbols),
                                 "action": "queued" if created else "already_queued"})
    for skip in plan.skipped:
        eff["skipped"].append({"symbol": skip.symbol, "reason": skip.reason})


def run_refill_cycle(
    units: list[RefillUnit],
    *,
    universe,
    profiles,
    policy,
    private_root: Path,
    provider: Any,
    state: RefillState,
    apply: bool,
    now_ms: int,
    conn=None,
    max_prepares: int = 8,
    data_days: int | None = None,
    full: bool = False,
    priority: int = 80,
    max_failures: int = 3,
    allow_public_output: bool = False,
) -> dict[str, Any]:
    """Prepare + plan + queue the given units. Returns counters/decisions/skipped."""
    private_root = resolve_private_root(Path(private_root), allow_public_output=allow_public_output) \
        if apply else Path(private_root)
    counters = {k: 0 for k in COUNTER_KEYS}
    eff: dict[str, list] = {"decisions": [], "skipped": []}
    prepares_left = max_prepares
    for unit in units:
        counters["units"] += 1
        profile = profiles.get(unit.timeframe)
        symbols = list(universe.symbols_in(unit.group))[: _symbol_cap(profile, full)]
        glob = market_data_glob(private_root, unit.timeframe)
        prepares_left = _prepare_missing(
            symbols, unit, glob=glob, state=state, provider=provider, private_root=private_root,
            apply=apply, now_ms=now_ms, data_days=data_days, prepares_left=prepares_left,
            max_failures=max_failures, allow_public_output=allow_public_output, counters=counters, eff=eff)
        inventory = build_inventory(glob, symbols)
        plan = build_research_plan(universe, profiles, policy, group=unit.group,
                                   timeframe=unit.timeframe, data_glob=glob,
                                   families=unit.families, inventory=inventory, smoke=not full)
        _queue_plan(plan, unit, private_root=private_root, conn=conn, apply=apply,
                    priority=priority, counters=counters, eff=eff)
    return {"counters": counters, "decisions": eff["decisions"], "skipped": eff["skipped"],
            "prepares_left": prepares_left}
