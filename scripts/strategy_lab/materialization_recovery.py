"""Plan/apply one exact expired materialization adoption while quiescent."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.materialization_recovery import apply_plan, build_plan
from src.research_lab.paths import resolve_private_root
from src.research_lab.state_db import default_db_path


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)


def _require_quiescent(private_root: Path, task_id: int) -> None:
    ownership = _read_only(Path(private_root) / "state" / "ownership.sqlite")
    try:
        active = int(
            ownership.execute(
                """SELECT COUNT(*) FROM ownership_resources
                   WHERE owner_id IS NOT NULL AND lease_expires_at>?""",
                (time.time(),),
            ).fetchone()[0]
        )
    finally:
        ownership.close()
    if active:
        raise RuntimeError("materialization recovery requires zero active owners")
    farm = _read_only(tasks_db_path(private_root))
    try:
        running = [
            int(row[0])
            for row in farm.execute(
                "SELECT task_id FROM tasks WHERE state='running' ORDER BY task_id"
            )
        ]
    finally:
        farm.close()
    # Before the first apply the exact expired target is the sole running task.
    # After a successful adoption there are no running tasks, and the same
    # hash-bound plan must remain callable to prove changed=0.  The domain
    # planner/apply API still validates the target's exact adopted state.
    if running not in ([], [int(task_id)]):
        raise RuntimeError(
            "materialization recovery requires no unrelated running tasks"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--private-root", type=Path, required=True)
    plan_parser.add_argument("--task-id", type=int, required=True)
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--json", action="store_true")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--private-root", type=Path, required=True)
    apply_parser.add_argument("--task-id", type=int, required=True)
    apply_parser.add_argument("--plan", type=Path, required=True)
    apply_parser.add_argument("--expected-plan-digest", required=True)
    apply_parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    private_root = resolve_private_root(args.private_root)
    task_id = int(args.task_id)
    _require_quiescent(private_root, task_id)
    farm_path = tasks_db_path(private_root)
    compute_path = default_db_path(private_root)
    if args.command == "plan":
        output = Path(args.output).resolve()
        result = build_plan(farm_path, compute_path, task_id=task_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        public_result = {
            "schema": result["schema"],
            "plan_digest": result["plan_digest"],
            "task_id": int(result["entry"]["task_id"]),
            "already_adopted": bool(result["entry"]["already_adopted"]),
        }
    else:
        plan_path = Path(args.plan).resolve()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if int((plan.get("entry") or {}).get("task_id") or 0) != task_id:
            raise ValueError("materialization recovery task capability mismatch")
        public_result = apply_plan(
            farm_path,
            compute_path,
            plan,
            expected_plan_digest=str(args.expected_plan_digest),
        )
    print(json.dumps(public_result, sort_keys=True) if args.json else public_result)


if __name__ == "__main__":
    main()
