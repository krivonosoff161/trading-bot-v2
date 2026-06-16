# -*- coding: utf-8 -*-
"""Research-machine demo: one bounded, visible, paper-only pass of the loop.

By DEFAULT this seeds from the EXISTING scanner watch_queue (it does NOT run a
fresh scanner pass) and exercises the farm + hard-validation + feedback half of
the loop on tiny limits:

    existing scanner watches  -> scanner-to-farm bridge (bounded sweeps)
                              -> Strategy Lab worker (one queued run)
                              -> hard-validation pipeline (export/validate/setup)
                              -> feedback reader (recommendations)
                              -> apply feedback recommendations (bounded follow-ups)
                              -> final status

The two ends of the scanner's own loop are OPT-IN (default OFF) because they make
live, keyless network calls:

    --run-scanner-pass   run one bounded scanner pass first (scanner_v0 --buffer
                         --limit N). Telegram send is force-disabled for the demo
                         child (no .env change). Read-only RSS/OKX/SEC fetches.
    --run-outcomes       resolve mature forward outcomes after (resolve_outcomes
                         --limit N). Keyless OKX calls; only scores matured cards.

Everything is bounded and paper-only: no order engine, no live trading, no
AUTO_TRADE, no paid LLM. Real artifacts go to the private research root only.

    python -m scripts.strategy_lab.run_research_machine_demo            # apply, latest watch
    python -m scripts.strategy_lab.run_research_machine_demo --dry-run  # preview only
    python -m scripts.strategy_lab.run_research_machine_demo --run-scanner-pass --run-outcomes

If the scanner queue has no data-available WATCH/GO event, the demo falls back to
a clearly-labelled synthetic smoke spec so the farm still has one bounded run.
That fallback is a demo seed, not a live signal.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
SMOKE_SPEC = "configs/strategy_lab/l2_smoke.json"


def _run(label: str, cmd: list[str], *, env_overrides: dict | None = None) -> tuple[int, str]:
    """Run one step, stream its output to the console, return (rc, stdout)."""
    print()
    print("-" * 60)
    print(f">>> {label}")
    print("    " + " ".join(cmd))
    print("-" * 60)
    env = dict(os.environ, PYTHONUTF8="1")
    if env_overrides:
        for key, value in env_overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
    proc = subprocess.run(
        cmd, cwd=str(ROOT), env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    return proc.returncode, proc.stdout or ""


def _stop(message: str) -> None:
    print()
    print("=" * 60)
    print(f"DEMO STOPPED: {message}")
    print("Recovery: re-run the failed step above manually, then resume the demo.")
    print("=" * 60)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Bounded paper-only research-machine demo.")
    ap.add_argument("--dry-run", action="store_true", help="Preview steps without writing artifacts")
    ap.add_argument("--timeframe", default="1d", help="Timeframe for the scanner-driven sweep")
    ap.add_argument("--max-symbols", type=int, default=1, help="Max scanner symbols to queue")
    ap.add_argument("--pipeline-limit", type=int, default=3, help="Max candidates per hard-validation step")
    ap.add_argument("--run-scanner-pass", action="store_true",
                    help="OPT-IN: run one bounded scanner pass first (live keyless network; Telegram off)")
    ap.add_argument("--run-outcomes", action="store_true",
                    help="OPT-IN: resolve mature forward outcomes after (live keyless OKX calls)")
    ap.add_argument("--scanner-limit", type=int, default=1, help="Cards per scanner pass / outcomes to resolve")
    ap.add_argument("--use-latest-watch", action="store_true",
                    help="Explicitly use the existing watch_queue (this is the default)")
    args = ap.parse_args()

    mode_flag = "--dry-run" if args.dry_run else "--apply"
    seed_mode = "fresh scanner pass" if args.run_scanner_pass else "existing watch_queue (latest watch)"
    print("=" * 60)
    print(f"  RESEARCH-MACHINE DEMO [{'DRY-RUN' if args.dry_run else 'APPLY'}] - paper-only")
    print(f"  Seed: {seed_mode}")
    print("  No order engine. No live trading. No AUTO_TRADE. No paid LLM.")
    print("=" * 60)

    # Optional Step S: a fresh, bounded scanner pass (opt-in; default OFF).
    # Telegram send is force-disabled for this child by blanking SCANNER_CHAT_ID
    # (env override for the subprocess only; the .env file is never touched).
    if args.run_scanner_pass:
        if args.dry_run:
            print("\n[scanner pass] --dry-run: skipping the live scanner pass.")
        else:
            rc, _ = _run(
                f"Step S - bounded scanner pass (limit {args.scanner_limit}, Telegram off)",
                [PY, "-X", "utf8", "src/scout/scanner_v0.py", "--buffer", "--limit", str(args.scanner_limit)],
                env_overrides={"SCANNER_CHAT_ID": None, "PUMP_CHAT_ID": None},
            )
            if rc != 0:
                _stop("scanner pass failed (re-run scanner_v0 manually to diagnose)")

    # Step 1: scanner -> farm bridge (reads the existing watch_queue.jsonl).
    rc, bridge_out = _run(
        "Step 1 - scanner-to-farm bridge (reads existing watch_queue)",
        [PY, "-X", "utf8", "-m", "scripts.strategy_lab.generate_event_sweeps",
         "--from-scanner", "--timeframe", args.timeframe,
         "--max-symbols", str(args.max_symbols), mode_flag],
    )
    if rc != 0:
        _stop("scanner-to-farm bridge failed")
    # A usable, data-available sweep exists when detected>0. Specs are idempotent
    # (deterministic experiment_id), so already_pending is expected on repeat runs.
    scanner_has_sweep = (not args.dry_run) and (" detected=0 " not in f" {bridge_out} ")
    result_status = "dry_run" if args.dry_run else "no_usable_event"

    # Step 2: demo-seed fallback only when the scanner queue yielded NO usable event.
    used_demo_seed = False
    if not args.dry_run and not scanner_has_sweep:
        print("\n[demo seed] no data-available watch in the queue; "
              "enqueueing a labelled synthetic smoke spec (not a live signal).")
        used_demo_seed = True
        rc, _ = _run(
            "Step 2 - demo-seed smoke spec (fallback)",
            [PY, "-X", "utf8", "-m", "scripts.strategy_lab.enqueue_experiment",
             "--spec", SMOKE_SPEC, "--ensure", "--priority", "40"],
        )
        if rc != 0:
            _stop("demo-seed enqueue failed")
    else:
        print("\n[Step 2] scanner queue produced a usable sweep - no demo seed needed."
              if scanner_has_sweep else "\n[Step 2] dry-run: skipping demo seed.")

    # Step 3: worker - process one pending job (ignore the desktop cadence throttle
    # for this single manual run). queue_empty is fine: idempotent specs may already
    # have completed in a prior run; their artifacts persist in the private root.
    if args.dry_run:
        print("\n[Step 3] dry-run: skipping worker_once (no jobs are queued).")
    else:
        rc, worker_out = _run(
            "Step 3 - Strategy Lab worker (one job)",
            [PY, "-X", "utf8", "-m", "scripts.strategy_lab.worker_once", "--ignore-cadence"],
        )
        if rc != 0:
            _stop("worker_once failed")
        if "completed job_id" in worker_out:
            result_status = "completed_new_run"
        elif "queue=empty" in worker_out or "queue_empty" in worker_out:
            result_status = "reused_existing_run" if (scanner_has_sweep or used_demo_seed) else "no_usable_event"
            print("\n[Step 3] queue empty - the queued spec already completed in a prior "
                  "run (idempotent); existing run artifacts remain in the private root.")
        elif "deferred" in worker_out:
            result_status = "worker_deferred"

    # Step 4: hard-validation pipeline (export -> validate -> feedback -> setup library)
    rc, _ = _run(
        "Step 4 - hard-validation pipeline",
        [PY, "-X", "utf8", "-m", "scripts.strategy_lab.validate_candidates_pipeline",
         "--limit", str(args.pipeline_limit)] + ([] if args.dry_run else ["--apply"]),
    )
    if rc != 0:
        _stop("hard-validation pipeline failed")

    # Step 5: feedback reader - verdicts -> farm recommendations (read-only view)
    rc, _ = _run(
        "Step 5 - feedback reader (recommendations)",
        [PY, "-X", "utf8", "-m", "scripts.strategy_lab.read_feedback"]
        + ([] if args.dry_run else ["--apply"]),
    )
    if rc != 0:
        _stop("feedback reader failed")

    # Step 6: apply feedback recommendations - bounded follow-up specs/notes (closes loop)
    rc, _ = _run(
        "Step 6 - apply feedback recommendations (bounded follow-ups)",
        [PY, "-X", "utf8", "-m", "scripts.strategy_lab.apply_feedback_recommendations",
         "--limit", str(args.pipeline_limit)] + (["--dry-run"] if args.dry_run else ["--apply"]),
    )
    if rc != 0:
        _stop("apply feedback recommendations failed")

    # Optional Step O: resolve mature forward outcomes (opt-in; default OFF; live OKX).
    if args.run_outcomes:
        if args.dry_run:
            print("\n[outcomes] --dry-run: skipping the live outcome resolver.")
        else:
            rc, _ = _run(
                f"Step O - resolve mature outcomes (limit {args.scanner_limit})",
                [PY, "-X", "utf8", "src/scout/resolve_outcomes.py", "--limit", str(args.scanner_limit)],
            )
            if rc != 0:
                print("\n[outcomes] resolver returned non-zero; continuing (outcomes are best-effort).")

    # Step 7: final status
    _run("Step 7 - Strategy Lab status",
         [PY, "-X", "utf8", "-m", "scripts.strategy_lab.status"])

    _print_summary(args, result_status, used_demo_seed)


def _print_summary(args, result_status: str, used_demo_seed: bool) -> None:
    print()
    print("=" * 60)
    print("  DEMO COMPLETE")
    print(f"  result_status: {result_status}")
    print(f"  scanner pass:  {'ran (bounded, Telegram off)' if args.run_scanner_pass and not args.dry_run else 'skipped (used existing watch_queue)'}")
    print(f"  outcomes:      {'ran (mature only)' if args.run_outcomes and not args.dry_run else 'skipped'}")
    print(f"  demo seed:     {'used (synthetic smoke spec)' if used_demo_seed else 'not used'}")
    print()
    if result_status == "completed_new_run":
        print("  A new bounded run completed this pass; fresh artifacts written.")
    elif result_status == "reused_existing_run":
        print("  No new run: the bounded spec already completed (idempotent). Existing")
        print("  run artifacts remain; validation/feedback re-ran against them.")
    elif result_status == "no_usable_event":
        print("  No usable scanner event and no fresh run produced this pass.")
    elif result_status == "dry_run":
        print("  Dry-run preview only; nothing was written.")
    print()
    print("  Exercised: scanner-to-farm bridge -> worker -> hard validation ->")
    print("  feedback reader -> feedback follow-ups." )
    if not (args.run_scanner_pass or args.run_outcomes):
        print("  NOT exercised this run: fresh scanner pass and outcome resolver")
        print("  (opt in with --run-scanner-pass / --run-outcomes).")
    print("  Artifacts (if --apply) under the private trading-bot-research root:")
    print("    plans/{event_specs,feedback_specs}, experiments/completed, candidate-registry,")
    print("    hard_validation/{requests,reports,verdicts,feedback,recommendations,followups},")
    print("    setup_library/cards.")
    print("  No live trading occurred.")
    print("=" * 60)


if __name__ == "__main__":
    main()
