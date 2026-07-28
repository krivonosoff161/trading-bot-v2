"""Verify and release redundant acknowledged materialization payloads.

Dry-run is read-only and is the default. Apply requires the exact plan digest
printed by a prior dry-run. The operator separately owns quiescence, backup,
restore-proof, and authority gates; this command never infers them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
from src.research_lab.paths import resolve_private_root


def run(
    *,
    private_root: Path,
    apply: bool,
    expected_plan_digest: str = "",
    compact: bool = False,
) -> dict[str, object]:
    root = resolve_private_root(private_root)
    tasks = FarmTasksDB(
        tasks_db_path(root),
        owner_id="offline-materialization-payload-release",
        read_only=not apply,
    )
    try:
        return tasks.release_acknowledged_materialization_payloads(
            apply=apply,
            expected_plan_digest=expected_plan_digest,
            compact=compact,
        )
    finally:
        tasks.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify and release acknowledged outbox replay payload copies "
            "(dry-run by default)."
        )
    )
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-digest", default="")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="checkpoint and VACUUM after a successful bound apply",
    )
    args = parser.parse_args()
    if not args.apply and (args.expected_plan_digest or args.compact):
        parser.error("--expected-plan-digest/--compact require --apply")
    result = run(
        private_root=args.private_root,
        apply=bool(args.apply),
        expected_plan_digest=str(args.expected_plan_digest),
        compact=bool(args.compact),
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
