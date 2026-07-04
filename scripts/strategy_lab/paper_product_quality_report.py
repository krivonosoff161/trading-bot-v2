from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paper_product_quality_report import build_paper_product_quality_report  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build private aggregate paper-product quality report")
    parser.add_argument("--private-root", default=str(DEFAULT_PRIVATE_ROOT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = build_paper_product_quality_report(resolve_private_root(args.private_root))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "paper_product_quality_report: "
            f"trades={summary['product_trades']} active={summary['active_trades']} "
            f"active_live_ready={summary['active_live_ready']} "
            f"action={summary['operator_action']} "
            f"families={len(summary['families'])} "
            f"execution_allowed={summary['execution_allowed']}"
        )


if __name__ == "__main__":
    main()
