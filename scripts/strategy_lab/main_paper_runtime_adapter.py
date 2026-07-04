"""Build a paper-only main runtime queue from accepted paper instructions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.main_paper_runtime_adapter import build_main_paper_runtime_queue  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    summary = build_main_paper_runtime_queue(args.private_root, limit=args.limit)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "main_paper_runtime_queue "
            f"read={summary['rows_read']} queued={summary['queued']} "
            f"invalid={summary['invalid']} execution_allowed={summary['execution_allowed']} "
            f"paper_only={summary['paper_only']}"
        )
        print(summary["snapshot_path"])


if __name__ == "__main__":
    main()
