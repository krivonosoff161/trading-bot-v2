# -*- coding: utf-8 -*-
"""Farm queue scheduler/refill — decide what to research next, with visible reasons.

Pure planning: given open scanner watch records (and an optional backlog universe),
it returns a bounded, deduped, prioritized plan plus the operator-visible status
counters. It performs NO side effects — no fetch, no queueing, no order path. The
side effects live in the CLI (farm_queue_status) and the existing queueing path
(generate_event_sweeps --from-scanner), which queue the same bounded SweepSpecs.

Priority order (productization spec):
  1 fresh scanner WATCH/GO with usable data (incl. a fresh listing that BECAME
    usable — once usable its readiness is 'usable', so it rides this tier; and
    backlog/expired watches fed via --include-expired classify by their kind here)
  2 OKX official announcement with usable data
  3 OKX market mover with usable data
  4 curated universe/backlog to keep the GPU busy when fresh WATCH/GO is empty
"""
from __future__ import annotations

from dataclasses import dataclass

from src.research_lab.farm_priority import (
    PRIORITY_BACKGROUND,
    PRIORITY_OKX_ANNOUNCEMENT,
    PRIORITY_OKX_MOVER,
    PRIORITY_SCANNER_GO,
    PRIORITY_SCANNER_WATCH,
    watch_verdict,
)

PRIORITY_UNIVERSE = PRIORITY_BACKGROUND

DEFAULT_MAX_JOBS = 8

# Pending-reasons that mean "data, not instrument" — counted as skipped_missing_data.
_MISSING_DATA_REASONS = {"missing", "too_short", "provider_error", "readiness_not_assessed"}


@dataclass(frozen=True)
class FarmJob:
    okx_inst: str | None
    symbol: str | None
    timeframe: str | None
    priority: int
    source_kind: str
    watch_id: str | None
    reason: str


def _empty_counters() -> dict:
    return {
        "watches_read": 0,
        "resolved": 0,
        "unresolved": 0,
        "eligible": 0,
        "data_usable": 0,
        "data_too_short": 0,
        "pending_recheck": 0,
        "queued": 0,
        "already_pending": 0,
        "already_completed": 0,
        "skipped_missing_instrument": 0,
        "skipped_missing_data": 0,
    }


def classify_watch(watch: dict) -> tuple[int, str]:
    """Map a watch to (priority, source_kind) for the refill order."""
    scanner = watch.get("scanner") or {}
    source = str((watch.get("trigger") or {}).get("source") or "").lower()
    event_type = str(scanner.get("event_type") or "").lower()
    verdict = watch_verdict(watch)
    if verdict == "GO":
        return PRIORITY_SCANNER_GO, "scanner_go"
    if event_type == "market_mover" or "market_tape" in source:
        return PRIORITY_OKX_MOVER, "okx_market_mover"
    if "okx_announcement" in source or event_type == "listing":
        return PRIORITY_OKX_ANNOUNCEMENT, "okx_announcement"
    return PRIORITY_SCANNER_WATCH, "scanner_watch"


def plan_jobs(
    watches: list[dict],
    *,
    max_jobs: int = DEFAULT_MAX_JOBS,
    backlog_symbols: list[str] | None = None,
    completed_inst: set[str] | None = None,
    pending_inst: set[str] | None = None,
) -> dict:
    """Return {'jobs': [FarmJob], 'skipped': [{symbol, reason}], 'counters': {...}}."""
    counters = _empty_counters()
    jobs: list[FarmJob] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    completed = {str(s).upper() for s in (completed_inst or set())}
    pending = {str(s).upper() for s in (pending_inst or set())}

    ranked = sorted(watches, key=lambda w: (classify_watch(w)[0], str(w.get("created_at") or "")))
    for watch in ranked:
        counters["watches_read"] += 1
        asset = watch.get("asset") or {}
        farm = watch.get("farm") or {}
        symbol = asset.get("symbol")
        inst = asset.get("okx_inst")
        resolved = asset.get("okx_resolved")
        readiness = str(farm.get("data_readiness_status") or "")
        eligible = farm.get("eligible")

        if resolved is True:
            counters["resolved"] += 1
        elif resolved is False:
            counters["unresolved"] += 1
        if readiness == "usable":
            counters["data_usable"] += 1
        elif readiness == "too_short":
            counters["data_too_short"] += 1
        elif readiness == "fresh_listing_pending":
            counters["pending_recheck"] += 1

        if resolved is False:
            counters["skipped_missing_instrument"] += 1
            skipped.append({"symbol": symbol, "reason": farm.get("pending_reason") or "no_okx_instrument"})
            continue
        if eligible is not True:
            reason = str(farm.get("pending_reason") or readiness or "readiness_not_assessed")
            if reason in _MISSING_DATA_REASONS:
                counters["skipped_missing_data"] += 1
            skipped.append({"symbol": symbol, "reason": reason})
            continue
        counters["eligible"] += 1  # passed the eligibility gate (before dedup/cap drops)

        key = str(inst or symbol or "").upper()
        if not key:
            skipped.append({"symbol": symbol, "reason": "no_symbol"})
            continue
        if key in seen:
            continue
        if key in completed:
            counters["already_completed"] += 1
            skipped.append({"symbol": symbol, "reason": "already_completed"})
            continue
        if key in pending:
            counters["already_pending"] += 1
            skipped.append({"symbol": symbol, "reason": "already_pending"})
            continue
        if len(jobs) >= max_jobs:
            skipped.append({"symbol": symbol, "reason": "queue_cap_reached"})
            continue
        seen.add(key)
        priority, kind = classify_watch(watch)
        jobs.append(FarmJob(inst, symbol, farm.get("selected_timeframe"), priority, kind,
                            watch.get("watch_id"), "queued"))
        counters["queued"] += 1

    # Refill (priority 4): keep the GPU busy with backlog/universe when fresh is thin.
    for symbol in backlog_symbols or []:
        if len(jobs) >= max_jobs:
            break
        key = str(symbol or "").upper()
        if not key or key in seen or key in completed or key in pending:
            continue
        seen.add(key)
        jobs.append(FarmJob(symbol, symbol, None, PRIORITY_UNIVERSE, "universe_backlog",
                            None, "refill_keep_gpu_busy"))
        counters["queued"] += 1

    return {"jobs": jobs, "skipped": skipped, "counters": counters}
