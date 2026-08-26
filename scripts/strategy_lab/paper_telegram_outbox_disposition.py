"""Plan/apply permanent no-replay disposition for Telegram outbox debt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.research_lab.paper_telegram_outbox_disposition import (
    apply_disposition_plan,
    build_disposition_plan,
)
from src.research_lab.paths import resolve_private_root


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--private-root", type=Path, required=True)
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--json", action="store_true")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--private-root", type=Path, required=True)
    apply_parser.add_argument("--plan", type=Path, required=True)
    apply_parser.add_argument("--expected-plan-digest", required=True)
    apply_parser.add_argument("--backup", type=Path, required=True)
    apply_parser.add_argument(
        "--confirm-permanent-no-replay", action="store_true", required=True
    )
    apply_parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = resolve_private_root(args.private_root)
    if args.command == "plan":
        result = build_disposition_plan(root)
        output = resolve_private_root(args.output.parent) / args.output.name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        public = {
            "schema": result["schema"],
            "action": result["action"],
            "plan_digest": result["plan_digest"],
            "target_count": result["target_count"],
        }
    else:
        plan_path = resolve_private_root(args.plan.parent) / args.plan.name
        backup_path = resolve_private_root(args.backup.parent) / args.backup.name
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        result = apply_disposition_plan(
            root,
            plan,
            expected_plan_digest=args.expected_plan_digest,
            backup_path=backup_path,
            confirm_permanent_no_replay=args.confirm_permanent_no_replay,
        )
        public = result
    print(json.dumps(public, sort_keys=True) if args.json else public)


if __name__ == "__main__":
    main()
