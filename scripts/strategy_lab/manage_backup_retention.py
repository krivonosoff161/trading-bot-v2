"""Plan, apply, and verify exact-root backup retention."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from src.research_lab.backup_retention import (
    BackupRetentionError,
    apply_retention_plan,
    build_retention_plan,
    load_authority,
    load_plan,
    storage_budget_status,
    verify_archive,
    write_plan,
)


def _gib(value: str) -> int:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("GiB value must be numeric") from exc
    if not 0 < parsed <= 1024 * 1024:
        raise argparse.ArgumentTypeError("GiB value must be positive and bounded")
    return int(parsed * 1024**3)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser(
        "status", help="Fail closed when the exact backup root exceeds budget."
    )
    status.add_argument("--backup-root", required=True, type=Path)
    status.add_argument("--max-backup-gib", type=_gib, default=_gib("20"))
    status.add_argument("--min-free-gib", type=_gib, default=_gib("32"))

    plan = commands.add_parser(
        "plan", help="Hash every generation and emit a non-mutating cleanup plan."
    )
    plan.add_argument("--backup-root", required=True, type=Path)
    plan.add_argument("--archive-root", required=True, type=Path)
    plan.add_argument("--output", required=True, type=Path)
    plan.add_argument("--retain-generation", required=True)
    plan.add_argument("--retain-evidence-sha256", required=True)
    plan.add_argument("--max-backup-gib", type=_gib, default=_gib("20"))
    plan.add_argument("--min-free-gib", type=_gib, default=_gib("32"))

    apply = commands.add_parser(
        "apply", help="Archive and remove only exact files from one verified plan."
    )
    apply.add_argument("--plan", required=True, type=Path)
    apply.add_argument("--authority", required=True, type=Path)
    apply.add_argument("--expected-plan-digest", required=True)

    verify = commands.add_parser(
        "verify", help="Restore-verify all content-addressed objects in a plan."
    )
    verify.add_argument("--plan", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "status":
            result = storage_budget_status(
                args.backup_root,
                max_backup_bytes=args.max_backup_gib,
                min_free_bytes=args.min_free_gib,
            )
            print(json.dumps(result, sort_keys=True))
            return 0 if result["within_budget"] else 2
        if args.command == "plan":
            plan = build_retention_plan(
                args.backup_root,
                args.archive_root,
                retain_generation=args.retain_generation,
                retained_generation_evidence_sha256=args.retain_evidence_sha256,
                max_backup_bytes=args.max_backup_gib,
                min_free_bytes=args.min_free_gib,
            )
            write_plan(plan, args.output)
            print(
                json.dumps(
                    {
                        "schema": plan.schema,
                        "plan_digest": plan.plan_digest,
                        "generation_count": len(plan.generations),
                        "file_count": len(plan.files),
                        "retain_unpacked": list(plan.retain_unpacked_generations),
                        "archive_remove_count": len(plan.archive_remove_generations),
                        "reclaim_candidate_bytes": plan.reclaim_candidate_bytes,
                        "unique_archive_logical_bytes": plan.unique_archive_logical_bytes,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "apply":
            plan = load_plan(args.plan)
            authority = load_authority(args.authority, plan)
            report = apply_retention_plan(
                plan,
                authority,
                expected_plan_digest=args.expected_plan_digest,
            )
            print(json.dumps(asdict(report), sort_keys=True))
            return 0
        if args.command == "verify":
            print(json.dumps(verify_archive(load_plan(args.plan)), sort_keys=True))
            return 0
    except BackupRetentionError as exc:
        print(f"backup retention: failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
