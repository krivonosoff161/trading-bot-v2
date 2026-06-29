"""Build derived lineage mappings for existing private paper/research artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.lineage_backfill import build_lineage_backfill  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = build_lineage_backfill(args.private_root, limit=args.limit)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "lineage_backfill: "
            f"rows={summary['rows']} mapping={summary['mapping_label']} "
            f"non_destructive={summary['non_destructive']}"
        )


if __name__ == "__main__":
    main()
