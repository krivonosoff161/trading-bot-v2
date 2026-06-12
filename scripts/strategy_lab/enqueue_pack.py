# -*- coding: utf-8 -*-
"""Queue a directory of strategy-lab experiment specs.

This is the safe desktop "research pack" entrypoint: it validates each JSON
spec by loading it, then queues it in the private SQLite state DB. The worker
still processes one job at a time.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.experiment import ExperimentSpec  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.state_db import (  # noqa: E402
    connect,
    default_db_path,
    ensure_experiment_queued,
    init_db,
)


def discover_specs(pack_dir: Path, pattern: str = "*.json") -> list[Path]:
    pack_dir = pack_dir.expanduser().resolve()
    if not pack_dir.exists():
        raise FileNotFoundError(f"pack dir not found: {pack_dir}")
    specs = sorted(p for p in pack_dir.glob(pattern) if p.is_file())
    if not specs:
        raise FileNotFoundError(f"no specs matching {pattern!r} under {pack_dir}")
    return specs


def enqueue_pack(
    specs: list[Path],
    *,
    private_root: Path,
    priority: int = 100,
    ensure: bool = True,
    allow_public_output: bool = False,
) -> dict[str, Any]:
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    db_path = default_db_path(private_root)
    conn = connect(db_path)
    init_db(conn)
    rows = []
    try:
        for spec_path in specs:
            spec = ExperimentSpec.from_json(spec_path)
            if ensure:
                job_id, created = ensure_experiment_queued(conn, spec_path.resolve(), priority=priority)
            else:
                from src.research_lab.state_db import enqueue_experiment

                job_id = enqueue_experiment(conn, spec_path.resolve(), priority=priority)
                created = True
            rows.append(
                {
                    "job_id": job_id,
                    "created": created,
                    "experiment_id": spec.experiment_id,
                    "spec": spec_path.name,
                }
            )
    finally:
        conn.close()
    return {
        "db_label": f"strategy-lab/state/{db_path.name}",
        "queued": sum(1 for row in rows if row["created"]),
        "already_pending": sum(1 for row in rows if not row["created"]),
        "jobs": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="configs/strategy_lab/starter", help="Directory with JSON specs")
    ap.add_argument("--pattern", default="*.json", help="Glob pattern inside --dir")
    ap.add_argument("--priority", type=int, default=100)
    ap.add_argument("--no-ensure", action="store_true", help="Allow duplicate queued/running jobs")
    ap.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Private strategy-lab root",
    )
    ap.add_argument("--allow-public-output", action="store_true", help="Allow writing under this public repo")
    args = ap.parse_args()

    specs = discover_specs(Path(args.dir), args.pattern)
    result = enqueue_pack(
        specs,
        private_root=Path(args.private_root),
        priority=args.priority,
        ensure=not args.no_ensure,
        allow_public_output=args.allow_public_output,
    )
    print(
        f"pack={Path(args.dir).name} specs={len(specs)} queued={result['queued']} "
        f"already_pending={result['already_pending']} db={result['db_label']}"
    )
    for row in result["jobs"]:
        action = "queued" if row["created"] else "already_pending"
        print(f"{action} job_id={row['job_id']} experiment={row['experiment_id']} spec={row['spec']}")


if __name__ == "__main__":
    main()
