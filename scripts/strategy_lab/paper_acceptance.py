from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.research_lab.paper_acceptance import evaluate_acceptance, start_acceptance
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="Start or inspect the private paper acceptance run")
    parser.add_argument("command", choices=("start", "status"))
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--hours", type=float, default=24.0)
    args = parser.parse_args()
    if args.command == "start":
        result = start_acceptance(args.private_root, hours=args.hours)
    else:
        result = evaluate_acceptance(args.private_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
