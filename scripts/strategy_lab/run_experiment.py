# -*- coding: utf-8 -*-
"""Run one strategy-lab experiment and write private outputs."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab import ExperimentSpec, evaluate_spec, write_run_outputs  # noqa: E402


DEFAULT_PRIVATE_ROOT = Path(r"C:\Users\krivo\github_projects\trading-bot-research\strategy-lab")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="Experiment spec JSON")
    ap.add_argument(
        "--out-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Private strategy-lab root",
    )
    ap.add_argument("--dry-run", action="store_true", help="Evaluate but do not write outputs")
    args = ap.parse_args()

    spec = ExperimentSpec.from_json(Path(args.spec))
    results = evaluate_spec(spec)
    promoted = sum(1 for r in results if r.decision == "PROMOTE_FOR_PRESSURE_TEST")
    observed = sum(1 for r in results if r.decision == "OBSERVE")
    rejected = sum(1 for r in results if r.decision == "REJECT")
    print(
        f"experiment={spec.experiment_id} runs={len(results)} "
        f"promote={promoted} observe={observed} reject={rejected}"
    )
    if args.dry_run:
        return
    out_dir = write_run_outputs(spec, results, Path(args.out_root))
    print(f"wrote {out_dir}")


if __name__ == "__main__":
    main()

