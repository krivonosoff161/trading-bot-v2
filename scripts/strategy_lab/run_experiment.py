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
from src.research_lab.candle_store import CandleStore  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="Experiment spec JSON")
    ap.add_argument(
        "--out-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Private strategy-lab root",
    )
    ap.add_argument("--dry-run", action="store_true", help="Evaluate but do not write outputs")
    ap.add_argument("--include-rejects", action="store_true", help="Debug: also upsert REJECT rows into the candidate registry")
    ap.add_argument("--allow-public-output", action="store_true", help="Allow writing under this public repo")
    args = ap.parse_args()

    spec = ExperimentSpec.from_json(Path(args.spec))
    out_root = resolve_private_root(args.out_root, allow_public_output=args.allow_public_output)
    results = evaluate_spec(spec, candle_store=CandleStore(out_root))
    promoted = sum(1 for r in results if r.decision == "PROMOTE_FOR_PRESSURE_TEST")
    observed = sum(1 for r in results if r.decision == "OBSERVE")
    rejected = sum(1 for r in results if r.decision == "REJECT")
    print(
        f"experiment={spec.experiment_id} runs={len(results)} "
        f"promote={promoted} observe={observed} reject={rejected}"
    )
    if args.dry_run:
        return
    out_dir = write_run_outputs(
        spec,
        results,
        out_root,
        allow_public_output=args.allow_public_output,
        include_rejects=args.include_rejects,
    )
    print(f"wrote experiments/completed/{out_dir.name}")


if __name__ == "__main__":
    main()
