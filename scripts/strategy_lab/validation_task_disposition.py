"""Plan/apply exact validation-orphan disposition while the farm is quiescent."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.paths import resolve_private_root
from src.research_lab.validation_task_disposition import apply_plan, build_plan


def _require_quiescent(private_root: Path) -> None:
    ownership = Path(private_root) / "state" / "ownership.sqlite"
    conn = sqlite3.connect(f"file:{ownership.resolve().as_posix()}?mode=ro", uri=True)
    try:
        active = int(
            conn.execute(
                """SELECT COUNT(*) FROM ownership_resources
                   WHERE owner_id IS NOT NULL AND lease_expires_at>?""",
                (time.time(),),
            ).fetchone()[0]
        )
    finally:
        conn.close()
    if active:
        raise RuntimeError("validation disposition requires zero active owners")
    tasks = tasks_db_path(private_root)
    conn = sqlite3.connect(f"file:{tasks.resolve().as_posix()}?mode=ro", uri=True)
    try:
        running = int(
            conn.execute("SELECT COUNT(*) FROM tasks WHERE state='running'").fetchone()[0]
        )
    finally:
        conn.close()
    if running:
        raise RuntimeError("validation disposition requires zero running tasks")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--private-root", type=Path, required=True)
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--missing-grace-seconds", type=float, default=600.0)
    plan_parser.add_argument("--json", action="store_true")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--private-root", type=Path, required=True)
    apply_parser.add_argument("--plan", type=Path, required=True)
    apply_parser.add_argument("--expected-plan-digest", required=True)
    apply_parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    private_root = resolve_private_root(args.private_root)
    _require_quiescent(private_root)
    if args.command == "plan":
        output = resolve_private_root(args.output.parent) / args.output.name
        result = build_plan(
            tasks_db_path(private_root),
            missing_grace_seconds=args.missing_grace_seconds,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        public_result = {
            "schema": result["schema"],
            "plan_digest": result["plan_digest"],
            "counts": result["counts"],
        }
    else:
        plan_path = resolve_private_root(args.plan.parent) / args.plan.name
        result = apply_plan(
            tasks_db_path(private_root),
            json.loads(plan_path.read_text(encoding="utf-8")),
            expected_plan_digest=args.expected_plan_digest,
        )
        public_result = result
    print(json.dumps(public_result, sort_keys=True) if args.json else public_result)


if __name__ == "__main__":
    main()
