# -*- coding: utf-8 -*-
"""
backfill_scanner_records.py - restore structured scanner records from historical journal/outcomes.

Purpose:
  - old cards already in scanner_journal/scanner_outcomes should not be invisible to
    the new training/memory storage model;
  - backfill is append-only and idempotent.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.scanner_journal import JOURNAL  # noqa: E402
from src.scout.resolve_outcomes import OUTCOMES  # noqa: E402
from src.scout import scanner_records as R  # noqa: E402
from src.scout import pending_store as PS  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def backfill(
    *,
    journal_path: Path = JOURNAL,
    outcomes_path: Path = OUTCOMES,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    journal_rows = _read_jsonl(journal_path)
    outcome_index = {row.get("card_id"): row for row in _read_jsonl(outcomes_path) if row.get("card_id")}
    compacted = {}
    if not dry_run:
        compacted = {
            "events": R.compact_unique_file(R.EVENTS),
            "reasoning": R.compact_unique_file(R.REASONING),
            "training": R.compact_unique_file(R.TRAINING),
            "memory": R.compact_unique_file(R.MEMORY),
        }
    event_index = R.read_index(R.EVENTS)
    reasoning_index = R.read_index(R.REASONING)
    training_index = R.read_index(R.TRAINING)
    memory_index = R.read_index(R.MEMORY)

    stats = {
        "journal_rows": len(journal_rows),
        "with_outcomes": 0,
        "events_written": 0,
        "reasoning_written": 0,
        "training_written": 0,
        "memory_written": 0,
        "already_present": 0,
        "skipped_no_outcome": 0,
        "skipped_unscored": 0,
        "pending_opened": 0,
        "processed": 0,
        "dry_run": dry_run,
        "compacted": compacted,
    }

    for row in journal_rows:
        if limit is not None and stats["processed"] >= limit:
            break
        cid = row.get("card_id")
        if not cid:
            continue
        stats["processed"] += 1

        event_block = event_index.get(cid)
        reasoning_block = reasoning_index.get(cid)

        if event_block is None:
            event_block = R.build_event_block_from_journal(row)
            if dry_run:
                stats["events_written"] += 1
            elif R.write_event_block(event_block):
                stats["events_written"] += 1
                event_index[cid] = event_block

        if reasoning_block is None:
            reasoning_block = R.build_reasoning_block_from_journal(row)
            if dry_run:
                stats["reasoning_written"] += 1
            elif R.write_reasoning_block(reasoning_block):
                stats["reasoning_written"] += 1
                reasoning_index[cid] = reasoning_block

        pending = PS.build_pending_from_journal(row)
        if pending is not None:
            if dry_run:
                stats["pending_opened"] += 1
            else:
                _, changed = PS.upsert_pending(pending)
                if changed:
                    stats["pending_opened"] += 1

        outcome = outcome_index.get(cid)
        if outcome is None:
            stats["skipped_no_outcome"] += 1
            continue
        stats["with_outcomes"] += 1
        if not outcome.get("scored"):
            stats["skipped_unscored"] += 1
            continue

        if cid in training_index and cid in memory_index:
            stats["already_present"] += 1
            continue

        training = R.build_training_record(
            row,
            outcome,
            event_block=event_block,
            reasoning_block=reasoning_block,
        )
        memory = R.build_memory_record(row, outcome, reasoning_block=reasoning_block)

        if training and cid not in training_index:
            if dry_run:
                stats["training_written"] += 1
            elif R.write_training_record(training):
                stats["training_written"] += 1
                training_index[cid] = training
        if memory and cid not in memory_index:
            if dry_run:
                stats["memory_written"] += 1
            elif R.write_memory_record(memory):
                stats["memory_written"] += 1
                memory_index[cid] = memory

    if not dry_run:
        stats["pending_expired"] = PS.expire_old()
    else:
        stats["pending_expired"] = 0

    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report what would be written, do not append files")
    ap.add_argument("--limit", type=int, default=None, help="process at most N journal rows")
    args = ap.parse_args()

    stats = backfill(dry_run=args.dry_run, limit=args.limit)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
