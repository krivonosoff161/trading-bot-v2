# -*- coding: utf-8 -*-
"""Queue a strategy-lab experiment spec for local worker execution."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.state_db import (  # noqa: E402
    connect,
    default_db_path,
    enqueue_experiment,
    ensure_experiment_queued,
    init_db,
)
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="Experiment spec JSON")
    ap.add_argument("--priority", type=int, default=100)
    ap.add_argument("--ensure", action="store_true", help="Do not duplicate queued/running job for the same spec")
    ap.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Private strategy-lab root",
    )
    ap.add_argument("--allow-public-output", action="store_true", help="Allow writing under this public repo")
    args = ap.parse_args()

    spec_path = Path(args.spec).expanduser().resolve()
    if not spec_path.exists():
        raise SystemExit(f"spec not found: {spec_path}")
    private_root = resolve_private_root(args.private_root, allow_public_output=args.allow_public_output)
    db_path = default_db_path(private_root)
    conn = connect(db_path)
    init_db(conn)
    if args.ensure:
        job_id, created = ensure_experiment_queued(conn, spec_path, priority=args.priority)
    else:
        job_id = enqueue_experiment(conn, spec_path, priority=args.priority)
        created = True
    conn.close()
    action = "queued" if created else "already_queued"
    print(f"{action} job_id={job_id} spec={spec_path.name} db=strategy-lab/state/{db_path.name}")


if __name__ == "__main__":
    main()
