"""Rebuild the main-readable paper instruction view.

This is a paper/research-only utility. It exports active paper-watch signals into
a SignalContract-shaped artifact for a future main-paper consumer. It does not send
Telegram messages and does not enable orders.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.main_paper_bridge import export_main_paper_instructions  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    summary = export_main_paper_instructions(args.private_root)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(
        "main_paper_bridge "
        f"instructions={summary['instructions']} "
        f"paper_only={summary['paper_only']} "
        f"execution_allowed={summary['execution_allowed']}"
    )
    print(f"jsonl={summary['jsonl_path']}")
    print(f"snapshot={summary['snapshot_path']}")


if __name__ == "__main__":
    main()
