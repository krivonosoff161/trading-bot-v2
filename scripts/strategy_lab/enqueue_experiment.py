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

from src.research_lab.state_db import connect, default_db_path, enqueue_experiment, init_db  # noqa: E402

DEFAULT_PRIVATE_ROOT = Path.home() / "github_projects" / "trading-bot-research" / "strategy-lab"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="Experiment spec JSON")
    ap.add_argument("--priority", type=int, default=100)
    ap.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Private strategy-lab root",
    )
    args = ap.parse_args()

    spec_path = Path(args.spec).expanduser().resolve()
    if not spec_path.exists():
        raise SystemExit(f"spec not found: {spec_path}")
    private_root = Path(args.private_root).expanduser()
    db_path = default_db_path(private_root)
    conn = connect(db_path)
    init_db(conn)
    job_id = enqueue_experiment(conn, spec_path, priority=args.priority)
    conn.close()
    print(f"queued job_id={job_id} spec={spec_path.name} db={db_path}")


if __name__ == "__main__":
    main()
