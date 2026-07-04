"""Export paper-signal outcomes into a training-friendly derived JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paper_signals.training_export import export_training_rows  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    ap.add_argument("--include-active", action="store_true",
                    help="include non-terminal active rows; default exports terminal outcomes only")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    summary = export_training_rows(args.private_root, terminal_only=not args.include_active)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "paper_signal_training: "
            f"rows={summary['rows']} terminal_only={summary['terminal_only']} "
            f"path={summary['jsonl_path']}"
        )


if __name__ == "__main__":
    main()
