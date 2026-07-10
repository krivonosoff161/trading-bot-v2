from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.trade_thesis_supervisor import write_trade_thesis_supervisor  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build private paper trade-thesis supervisor artifacts")
    parser.add_argument("--private-root", default=str(DEFAULT_PRIVATE_ROOT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = write_trade_thesis_supervisor(resolve_private_root(args.private_root))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "trade_thesis_supervisor: "
            f"theses={summary['theses']} active={summary['active_trades']} "
            f"events={summary['events']} by_action={summary['by_action']} "
            f"execution_allowed={summary['execution_allowed']}"
        )


if __name__ == "__main__":
    main()
