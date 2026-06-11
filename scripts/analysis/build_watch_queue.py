"""Build or refresh scout watch_queue.jsonl from scanner_journal.jsonl.

This is a read-only bridge builder for the scanner -> setup confirmation path.
It never sends Telegram messages and never touches order execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.scout import scanner_journal as J  # noqa: E402
from src.scout import watch_queue as WQ  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def build_queue(*, journal_path: Path, out_path: Path, dry_run: bool = False) -> dict[str, int]:
    rows = read_jsonl(journal_path)
    stats = {
        "journal_rows": len(rows),
        "eligible": 0,
        "written_or_updated": 0,
        "unchanged": 0,
        "skipped": 0,
    }
    for row in rows:
        if not WQ.should_queue(row):
            stats["skipped"] += 1
            continue
        stats["eligible"] += 1
        if dry_run:
            continue
        _watch_id, changed = WQ.upsert_watch(row, path=out_path)
        if changed:
            stats["written_or_updated"] += 1
        else:
            stats["unchanged"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build scanner watch queue from scanner journal")
    parser.add_argument("--journal", type=Path, default=J.JOURNAL)
    parser.add_argument("--out", type=Path, default=WQ.WATCH_QUEUE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    stats = build_queue(journal_path=args.journal, out_path=args.out, dry_run=args.dry_run)
    mode = "dry-run" if args.dry_run else "write"
    print(
        f"watch_queue {mode}: journal={stats['journal_rows']} eligible={stats['eligible']} "
        f"changed={stats['written_or_updated']} unchanged={stats['unchanged']} skipped={stats['skipped']}"
    )
    if not args.dry_run:
        print(f"out: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
