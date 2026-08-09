"""Operator CLI for Paper Evidence v2 shadow, cutover and rollback.

The command never starts farm/runtime processes and never reads environment files.
Operational callers must supply the exact private root and revision identity after
their independent quiescence, backup/restore and integrity gates.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from src.research_lab.ownership import current_process_identity
from src.research_lab.paper_evidence_store import PaperEvidenceStore
from src.research_lab.paper_generation_cutover import (
    activate_cutover,
    compare_shadow_parity,
    load_cutover_manifest,
    rollback_cutover,
    run_forward_shadow_replay,
)


def _load_items(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("parity input must be a JSON object")
    return [item for item in payload.get("items") or [] if isinstance(item, dict)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)

    activate = commands.add_parser("activate")
    activate.add_argument("--code-identity", required=True)
    activate.add_argument("--confirm-quiescent", action="store_true")

    commands.add_parser("status")
    commands.add_parser("rollback")

    parity = commands.add_parser("shadow-parity")
    parity.add_argument("--legacy-projection", type=Path, required=True)
    parity.add_argument("--v2-database", type=Path, required=True)

    replay = commands.add_parser("shadow-replay")
    replay.add_argument("--shadow-root", type=Path, required=True)
    replay.add_argument("--code-identity", required=True)
    replay.add_argument("--now-ms", type=int, required=True)
    replay.add_argument("--timeout-seconds", type=float, default=10.0)

    args = parser.parse_args()
    root = args.private_root.resolve()
    if args.command == "activate":
        if not args.confirm_quiescent:
            raise SystemExit("activate requires --confirm-quiescent")
        result = activate_cutover(
            root,
            owner_id=f"paper-cutover-{os.getpid()}",
            identity=current_process_identity(),
            code_identity=str(args.code_identity),
        )
        output = {
            "schema": result["schema"],
            "status": result["status"],
            "manifest_path": result["manifest_path"],
            "database_path": result["database_path"],
            "account_generation_id": result["account_generation_id"],
            "manifest_digest": result["manifest_digest"],
            "integrity_check": result["integrity_check"],
            "paper_only": True,
            "execution_allowed": False,
        }
    elif args.command == "rollback":
        result = rollback_cutover(root)
        output = {
            "schema": result["schema"],
            "status": result["status"],
            "changed": result["changed"],
            "manifest_path": result["manifest_path"],
            "manifest_digest": result["manifest_digest"],
            "paper_only": True,
            "execution_allowed": False,
        }
    elif args.command == "shadow-parity":
        projection = PaperEvidenceStore.read_completed_projection(
            args.v2_database,
            "trades",
        )
        output = compare_shadow_parity(_load_items(args.legacy_projection), projection)
    elif args.command == "shadow-replay":
        from src.research_lab.providers.okx_public import (
            OkxPublicMarketDataProvider,
            _httpx_get_direct,
        )

        output = run_forward_shadow_replay(
            root,
            args.shadow_root,
            provider=OkxPublicMarketDataProvider(
                timeout=float(args.timeout_seconds),
                http_get=_httpx_get_direct,
            ),
            owner_id=f"paper-shadow-{os.getpid()}",
            identity=current_process_identity(),
            code_identity=str(args.code_identity),
            now_ms=int(args.now_ms),
        )
    else:
        result = load_cutover_manifest(root, require_active=False)
        output = {
            "schema": result["schema"],
            "status": result["status"],
            "account_generation_id": result["account_generation_id"],
            "manifest_digest": result["manifest_digest"],
            "paper_only": True,
            "execution_allowed": False,
        }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
