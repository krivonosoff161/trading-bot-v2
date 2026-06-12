# -*- coding: utf-8 -*-
"""Run one queued strategy-lab job.

This is the smallest safe 24/7 building block: an external loop can call it
periodically, while the worker itself handles one job and exits.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab import ExperimentSpec, evaluate_spec, write_run_outputs  # noqa: E402
from src.research_lab.state_db import (  # noqa: E402
    claim_next_job,
    complete_job,
    connect,
    default_db_path,
    fail_job,
    import_run_dir,
    init_db,
)

DEFAULT_PRIVATE_ROOT = Path.home() / "github_projects" / "trading-bot-research" / "strategy-lab"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Private strategy-lab root",
    )
    args = ap.parse_args()
    private_root = Path(args.private_root).expanduser()
    db_path = default_db_path(private_root)
    conn = connect(db_path)
    init_db(conn)
    job = claim_next_job(conn)
    if not job:
        print(f"db={db_path} queue=empty")
        conn.close()
        return
    job_id = int(job["job_id"])
    try:
        spec = ExperimentSpec.from_json(Path(str(job["spec_path"])))
        results = evaluate_spec(spec)
        run_dir = write_run_outputs(spec, results, private_root)
        import_run_dir(conn, private_root, run_dir)
        conn.commit()
        label = str(run_dir.relative_to(private_root)).replace("\\", "/")
        complete_job(conn, job_id, label)
        print(f"completed job_id={job_id} run={label} results={len(results)}")
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        fail_job(conn, job_id, "".join(traceback.format_exception_only(type(exc), exc)).strip())
        print(f"failed job_id={job_id} error={exc}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
