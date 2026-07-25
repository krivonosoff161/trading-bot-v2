# -*- coding: utf-8 -*-
"""Apply hard-validation feedback recommendations as bounded next-step research.

Reads feedback recommendations (via the read-only feedback reader), and turns
them into deterministic next steps:

  * NARROW_PARAMS -> a bounded follow-up sweep queued for the worker.
  * WIDEN_PARAMS  -> a hard-capped follow-up sweep, else a note.
  * REGIME_SWEEP  -> a bounded regime-filtered follow-up sweep when evidence exists.
  * REQUIRE_MORE_DATA / PROMOTE / SUPPRESS / REJECT -> notes only.

Dry-run by default. ``--apply`` compiles + queues the allowed follow-up sweeps
(private root + SQLite queue, idempotent) and writes a decision log. Bounded by
``--limit`` (recommendations), ``--max-variants``, ``--max-symbols``, and
``--allowed-actions``. No LLM. No live trading. No order engine.

    python -m scripts.strategy_lab.apply_feedback_recommendations --dry-run
    python -m scripts.strategy_lab.apply_feedback_recommendations --apply --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.candidate_registry import load_entries  # noqa: E402
from src.research_lab.feedback_followup import QUEUEABLE_ACTIONS, plan_followups  # noqa: E402
from src.research_lab.feedback_reader import build_recommendations  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.search_family_definition import resolve_snapshot_set  # noqa: E402
from src.research_lab.sweep_compile import compile_sweep  # noqa: E402
from src.research_lab.sweep_spec import validate_sweep_spec  # noqa: E402
from src.research_lab.state_db import (  # noqa: E402
    connect,
    default_db_path,
    ensure_experiment_queued,
    init_db,
)
from src.research_lab.timeframes import load_timeframe_profiles  # noqa: E402
from src.research_lab.validation_feedback import load_feedback_queue  # noqa: E402

DEFAULT_DATA_GLOB = (
    "scripts/analysis/research/_okxhist/ai_scanner_feasibility/{symbol}_*.json"
)


def _load_cards(private_root: Path) -> list[dict]:
    from src.research_lab.validation_generation import read_current_setup_card

    cards_dir = private_root / "setup_library" / "cards"
    if not cards_dir.exists():
        return []
    out = []
    for path in sorted(cards_dir.glob("*.json")):
        payload = read_current_setup_card(private_root, path)
        if payload is not None:
            out.append(payload)
    return out


def _candidate_context_by_id(private_root: Path) -> dict[str, dict]:
    registry_file = private_root / "candidate-registry" / "candidates.jsonl"
    out: dict[str, dict] = {}
    for e in load_entries(registry_file):
        cid = str(e.get("candidate_id") or "")
        if not cid:
            continue
        context = {
            "params": dict(e.get("params") or {}),
            "filters": dict(e.get("filters") or {}),
            "validation_reasons": list(e.get("validation_reasons") or []),
            "regime_summary": dict(e.get("regime_summary") or {}),
            "validation_status": str(e.get("validation_status") or ""),
            "search_family_id": str(e.get("search_family_id") or ""),
            "search_trial_id": str(e.get("search_trial_id") or ""),
            "effective_n_trials": int(e.get("effective_n_trials") or 0),
        }
        if cid not in out or _context_score(context) > _context_score(out[cid]):
            out[cid] = context
    return out


def _context_score(context: dict) -> int:
    score = 0
    if context.get("validation_status") == "REGIME_SPECIFIC":
        score += 20
    if context.get("filters"):
        score += 10
    if any(
        str(r).startswith("strong_regime_bucket:")
        for r in context.get("validation_reasons") or []
    ):
        score += 10
    if context.get("regime_summary"):
        score += 1
    return score


def _night_mode() -> bool:
    return os.getenv("STRATEGY_LAB_NIGHT_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Apply feedback recommendations as bounded follow-up research."
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Compile + queue allowed follow-ups (default: dry-run)",
    )
    mode.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    ap.add_argument(
        "--limit", type=int, default=10, help="Max recommendations to process"
    )
    ap.add_argument(
        "--max-variants", type=int, default=8, help="Cap variants per follow-up sweep"
    )
    ap.add_argument(
        "--max-symbols",
        type=int,
        default=5,
        help="Cap distinct follow-up symbols to queue",
    )
    ap.add_argument(
        "--allowed-actions",
        default="NARROW_PARAMS,REGIME_SWEEP",
        help="Comma-separated actions allowed to queue (default: NARROW_PARAMS,REGIME_SWEEP)",
    )
    ap.add_argument("--data-glob", default=DEFAULT_DATA_GLOB)
    ap.add_argument("--priority", type=int, default=70)
    ap.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
    )
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()

    private_root = resolve_private_root(
        Path(args.private_root), allow_public_output=args.allow_public_output
    )
    dry_run = not args.apply
    allowed = {
        a.strip().upper() for a in args.allowed_actions.split(",") if a.strip()
    } & QUEUEABLE_ACTIONS

    recs = build_recommendations(
        load_feedback_queue(private_root), _load_cards(private_root)
    )
    plans = plan_followups(
        recs,
        _candidate_context_by_id(private_root),
        max_recommendations=args.limit,
        max_variants=args.max_variants,
        max_symbols=args.max_symbols,
        allowed_actions=allowed,
    )

    print(
        f"=== APPLY FEEDBACK RECOMMENDATIONS [{'DRY-RUN' if dry_run else 'APPLY'}] ==="
    )
    print(
        f"  recommendations: {len(recs)}  plans: {len(plans)}  allowed-to-queue: {sorted(allowed) or '(none)'}"
    )
    print()
    for p in plans:
        if p.queued:
            print(
                f"  QUEUE   {p.strategy_id}/{p.symbol}@{p.timeframe}  [{p.action}] {p.grid_preview}"
            )
        else:
            print(
                f"  NOTE    {p.strategy_id}/{p.symbol}@{p.timeframe}  [{p.action}] not_queued={p.not_queued_reason}"
            )
    print()

    queued = already = 0
    profiles = load_timeframe_profiles()
    policy = load_resource_policy(night_mode=_night_mode())
    queueable = [p for p in plans if p.queued and p.sweep is not None]

    # Deterministic validation gate before any compile/queue.
    valid_plans = []
    for p in queueable:
        sweep = p.sweep
        if sweep is None:
            continue
        result = validate_sweep_spec(
            sweep, timeframe_profiles=profiles, resource_policy=policy
        )
        if result.ok:
            valid_plans.append(p)
        else:
            print(f"  SKIP (invalid spec) {sweep.sweep_id}: {'; '.join(result.errors)}")

    if not dry_run and valid_plans:
        out_dir = private_root / "plans" / "feedback_specs"
        out_dir.mkdir(parents=True, exist_ok=True)
        db_path = default_db_path(private_root)
        conn = connect(db_path)
        init_db(conn)
        try:
            for p in valid_plans:
                sweep = p.sweep
                if sweep is None:
                    continue
                snapshot_id, evidence_hash, snapshot_bindings = resolve_snapshot_set(
                    private_root=private_root,
                    symbols=[sweep.anchor_symbol, *sweep.related_symbols],
                    timeframe=sweep.timeframe,
                    data_glob=args.data_glob,
                )
                exp = compile_sweep(
                    sweep,
                    data_glob=args.data_glob,
                    timeframe_profiles=profiles,
                    resource_policy=policy,
                    event_context={
                        "origin": "feedback_followup",
                        "candidate_id": p.candidate_id,
                        "hard_status_action": p.action,
                    },
                    data_snapshot_id=snapshot_id,
                    data_evidence_hash=evidence_hash,
                    data_snapshot_bindings=snapshot_bindings,
                )
                spec_path = out_dir / f"{exp.search_family_id}.json"
                exp.write_json(spec_path)
                _, created = ensure_experiment_queued(
                    conn, spec_path.resolve(), priority=args.priority
                )
                queued += int(created)
                already += int(not created)
        finally:
            conn.close()

    if not dry_run:
        log_dir = private_root / "hard_validation"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "followups.jsonl").write_text(
            "".join(json.dumps(p.to_dict(), ensure_ascii=False) + "\n" for p in plans),
            encoding="utf-8",
        )

    n_queue_plans = sum(1 for p in plans if p.queued)
    n_notes = len(plans) - n_queue_plans
    print("=== SUMMARY ===")
    print(f"  queueable plans: {n_queue_plans}  notes: {n_notes}")
    if dry_run:
        print("  (dry-run: nothing compiled, queued, or written; use --apply)")
    else:
        print(f"  queued new: {queued}  already-pending: {already}")
        print("  decision log: hard_validation/followups.jsonl (private root)")
    print(
        "  No live trading. No order engine. Recommendations queue research sweeps only."
    )


def _exp_to_dict(exp) -> dict:
    return exp.to_dict()


if __name__ == "__main__":
    main()
