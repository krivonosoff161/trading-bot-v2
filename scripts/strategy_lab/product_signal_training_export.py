"""Export manual/VIP product signal events into private training rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402
from src.research_lab.product_signal_training import (  # noqa: E402
    DEFAULT_SOURCE_LOG,
    export_product_signal_training,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    ap.add_argument("--source-log", type=Path, default=DEFAULT_SOURCE_LOG)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    summary = export_product_signal_training(args.private_root, source_log=args.source_log)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "product_signal_training: "
            f"rows={summary['rows']} source_rows={summary['source_rows']} "
            f"path={summary['jsonl_path']}"
        )


if __name__ == "__main__":
    main()
