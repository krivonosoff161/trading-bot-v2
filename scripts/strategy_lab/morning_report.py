# -*- coding: utf-8 -*-
"""Morning report — summarize the last overnight research loop run.

Reads the private root and reports: last loop mode/duration/iterations,
jobs completed/deferred/failed, strategies/families tested, symbols tested,
candidates by validation status, rejects and reasons, LLM requests/tokens/cost,
data missing by timeframe, queue state, stale/running hints, and recommended
next command. No absolute private paths in stdout.

    python -m scripts.strategy_lab.morning_report
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.candidate_registry import registry_path, registry_summary  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.proposal_store import load_proposals, proposals_path, rejection_reason_counts, status_counts  # noqa: E402
from src.research_lab.research_loop import loop_summary, read_loop_report  # noqa: E402
from src.research_lab.state_db import dashboard_snapshot, default_db_path  # noqa: E402
from src.research_lab.stop_intent import read_stop_intent  # noqa: E402


def _fmt_counts(counts: dict) -> str:
    return ", ".join(f"{k}: {v}" for k, v in sorted((counts or {}).items())) or "none"


def _missing_by_timeframe(state: dict) -> str:
    cycle = state.get("last_cycle") or {}
    missing = cycle.get("missing_by_timeframe") or {}
    if not missing:
        loop = state.get("last_loop") or {}
        missing = loop.get("missing_by_timeframe") or {}
    if not missing:
        return "no data"
    return ", ".join(f"{tf}: {n}" for tf, n in sorted(missing.items()))


def _stale_hints(state: dict) -> list[str]:
    hints: list[str] = []
    sd = state.get("state_db") or {}
    qc = sd.get("queue_counts") or {}
    running = qc.get("running", 0)
    if running > 0:
        hints.append(f"{running} job(s) in running state (may be stale if worker is not alive)")
    return hints


def _count_files(root: Path, rel: str, pattern: str = "*.json") -> int:
    path = root / rel
    if not path.exists():
        return 0
    return len(list(path.glob(pattern)))


def _count_jsonl_rows(root: Path, rel: str, filename: str) -> int:
    path = root / rel / filename
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _stop_hint(private_root: Path) -> str:
    stop = read_stop_intent(private_root)
    if stop:
        return f"Stop intent active (requested at {stop.get('requested_at', '?')}, reason: {stop.get('reason', '?')})"
    return ""


def generate_morning_report(private_root: Path) -> dict:
    """Generate the morning report data structure."""
    private_root = resolve_private_root(private_root, allow_public_output=False)

    loop_report = read_loop_report(private_root)
    loop = loop_summary(loop_report)

    snapshot = dashboard_snapshot(default_db_path(private_root))
    queue_counts = snapshot.get("queue_counts") or {}
    candidates = snapshot.get("candidates") or []

    reg = registry_summary(registry_path(private_root))
    props = status_counts(load_proposals(proposals_path(private_root)))
    prop_reject_reasons = rejection_reason_counts(load_proposals(proposals_path(private_root)))

    families_tested = set()
    symbols_tested = set()
    for c in candidates:
        if c.get("family"):
            families_tested.add(c["family"])
        if c.get("symbol"):
            symbols_tested.add(c["symbol"])

    reject_reasons: dict[str, int] = {}
    for c in candidates:
        if c.get("validation_status") == "REJECT":
            reasons = str(c.get("reasons") or "")
            for r in reasons.split("|"):
                r = r.strip()
                if r:
                    reject_reasons[r] = reject_reasons.get(r, 0) + 1

    stop_hint = _stop_hint(private_root)
    stale = _stale_hints(snapshot)

    next_cmd = "python -m scripts.strategy_lab.status"
    if loop.get("available") and not loop.get("stop_requested"):
        next_cmd = "python -m scripts.strategy_lab.status  # review results, then start again if desired"

    return {
        "loop": {
            "mode": loop.get("mode", "unknown"),
            "duration_minutes": loop.get("duration_minutes", 0),
            "iterations": loop.get("iterations", 0),
            "stop_requested": loop.get("stop_requested", False),
        },
        "jobs": {
            "completed": loop.get("worker_completed", 0),
            "deferred": loop.get("worker_deferred", 0),
            "failed": loop.get("worker_failed", 0),
        },
        "queue": {
            "pending": queue_counts.get("queued", 0),
            "running": queue_counts.get("running", 0),
            "completed": queue_counts.get("completed", 0),
            "failed": queue_counts.get("failed", 0),
        },
        "strategies_tested": sorted(families_tested),
        "symbols_tested": sorted(symbols_tested),
        "candidates": {
            "total": reg.get("entries", 0),
            "unique": reg.get("unique_candidates", 0),
            "by_validation": reg.get("by_validation_status") or {},
        },
        "rejects": {"reasons": reject_reasons, "total": sum(reject_reasons.values())},
        "llm": {
            "requests": 0,
            "tokens": 0,
            "cost_rub": loop.get("llm_cost_rub", 0.0),
            "validated": loop.get("llm_validated", 0),
            "rejected": loop.get("llm_rejected", 0),
        },
        "data_missing_by_timeframe": _missing_by_timeframe(snapshot),
        "proposals": props,
        "hard_validation": {
            "requests": _count_files(private_root, "hard_validation/requests"),
            "reports": _count_files(private_root, "hard_validation/reports"),
            "verdicts": _count_files(private_root, "hard_validation/verdicts"),
            "feedback": _count_jsonl_rows(private_root, "hard_validation/feedback", "feedback.jsonl"),
        },
        "setup_library": {
            "cards": _count_files(private_root, "setup_library/cards"),
            "reports": _count_files(private_root, "setup_library/reports", "*.md"),
            "index": _count_jsonl_rows(private_root, "setup_library", "setup_index.jsonl"),
        },
        "proposal_reject_reasons": prop_reject_reasons,
        "stale_hints": stale,
        "stop_hint": stop_hint,
        "recommended_next_command": next_cmd,
    }


def _print_report(report: dict) -> None:
    print("=" * 56)
    print("  Strategy Lab — Morning Report")
    print("=" * 56)
    loop = report["loop"]
    print(f"\nLast loop: {loop['mode']} ({loop['iterations']} iterations, "
          f"{loop['duration_minutes']} min)")
    if loop.get("stop_requested"):
        print("  ** Stop was requested during the loop **")

    jobs = report["jobs"]
    print(f"\nJobs: completed={jobs['completed']}, deferred={jobs['deferred']}, failed={jobs['failed']}")

    queue = report["queue"]
    print(f"Queue: pending={queue['pending']}, running={queue['running']}, "
          f"completed={queue['completed']}, failed={queue['failed']}")

    if report["strategies_tested"]:
        print(f"\nStrategies tested: {', '.join(report['strategies_tested'])}")
    else:
        print("\nStrategies tested: none")

    if report["symbols_tested"]:
        print(f"Symbols tested: {', '.join(report['symbols_tested'])}")
    else:
        print("Symbols tested: none")

    cand = report["candidates"]
    print(f"\nCandidates: {cand['total']} total, {cand['unique']} unique")
    if cand["by_validation"]:
        print(f"  By validation: {_fmt_counts(cand['by_validation'])}")

    rej = report["rejects"]
    if rej["total"] > 0:
        print(f"\nRejects: {rej['total']}")
        for reason, count in sorted(rej["reasons"].items(), key=lambda x: -x[1]):
            print(f"  {reason}: {count}")

    llm = report["llm"]
    print(f"\nLLM: validated={llm['validated']}, rejected={llm['rejected']}, "
          f"cost={llm['cost_rub']} RUB")

    print(f"\nData missing: {report['data_missing_by_timeframe']}")
    print(f"Proposals: {_fmt_counts(report['proposals'])}")
    hv = report["hard_validation"]
    print(f"Hard validation: requests={hv['requests']}, reports={hv['reports']}, "
          f"verdicts={hv['verdicts']}, feedback={hv['feedback']}")
    sl = report["setup_library"]
    print(f"Setup library: cards={sl['cards']}, reports={sl['reports']}, index={sl['index']}")
    if report.get("proposal_reject_reasons"):
        print(f"  Rejection reasons: {_fmt_counts(report['proposal_reject_reasons'])}")

    if report["stale_hints"]:
        print("\nHints:")
        for hint in report["stale_hints"]:
            print(f"  - {hint}")

    if report["stop_hint"]:
        print(f"\n{report['stop_hint']}")

    print(f"\nNext: {report['recommended_next_command']}")
    print("=" * 56)


def main() -> None:
    ap = argparse.ArgumentParser(description="Morning report for Strategy Lab overnight run")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--json", action="store_true", help="Output as JSON instead of formatted text")
    args = ap.parse_args()

    private_root = Path(args.private_root).expanduser()
    report = generate_morning_report(private_root)

    if args.json:
        import json
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)


if __name__ == "__main__":
    main()
