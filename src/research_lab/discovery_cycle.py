# -*- coding: utf-8 -*-
"""Bounded continuous discovery cycle — one pass of the research contour (research-only).

Chains the pieces into a single, stop-file-aware, error-isolated pass:

  live_universe refresh + intake -> mover held-out OOS validation -> tactical track ->
  exit-first re-sim -> outcome memory -> a cycle artifact (what worked / what failed / why / next).

Each step is guarded: a failure in one is recorded and the cycle proceeds (so a network blip on the
universe fetch does not lose the tactical/exit refresh). Bounded by symbol caps and the stop-file. The
artifact is what farm_status_report surfaces as the latest live cycle. Nothing is promoted; no
order/money/live path — this only refreshes derived research views and the intake queue.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from src.research_lab.stop_intent import is_stop_requested


def _step(name: str, fn: Callable[[], Any], steps: list[dict[str, Any]]) -> Any:
    """Run one guarded step; record status/timing; never raise."""
    t0 = time.monotonic()
    try:
        result = fn()
        steps.append({"step": name, "status": "ok", "ms": round((time.monotonic() - t0) * 1000),
                      "result": result})
        return result
    except Exception as exc:  # noqa: BLE001 - isolate the step; the cycle must continue
        steps.append({"step": name, "status": f"error:{type(exc).__name__}", "detail": str(exc)[:160],
                      "ms": round((time.monotonic() - t0) * 1000)})
        return None


def run_cycle(private_root: Path, *, limit_symbols: int = 20, apply_intake: bool = True,
              now: float | None = None) -> dict[str, Any]:
    private_root = Path(private_root)
    now = now if now is not None else time.time()
    steps: list[dict[str, Any]] = []

    def _universe() -> dict[str, Any]:
        from src.research_lab.live_universe_selector import apply_intake as _apply
        from src.research_lab.live_universe_selector import run as _run
        from src.research_lab.live_universe_selector import write_snapshot
        res = _run(private_root, top_n_per_group=12, now=now)
        write_snapshot(private_root, res, generated_at=now)
        applied = _apply(private_root, res["intake_events"], now=now) if apply_intake else {"registered": 0}
        return {"tickers": res["tickers_seen"], "intake_events": len(res["intake_events"]),
                "registered": applied.get("registered")}

    def _validate() -> dict[str, Any]:
        from src.research_lab.mover_validation import run as _mv
        from src.research_lab.mover_validation import write_snapshot
        rep = _mv(private_root, limit_symbols=limit_symbols)
        write_snapshot(private_root, rep)
        return {"cells": rep["evaluated_cells"], "by_cell": rep["summary"]["by_cell"][:6]}

    def _tactical() -> dict[str, Any]:
        from src.research_lab.tactical_track import build_track, summarize, write_snapshot
        write_snapshot(private_root)
        return {k: summarize(build_track(private_root))[k]
                for k in ("tactical_leads", "underpowered_positive", "no_event", "forward_watch")}

    def _exit() -> dict[str, Any]:
        from src.research_lab.exit_first_resim import run as _ef
        from src.research_lab.exit_first_resim import write_snapshot
        rep = _ef(private_root, limit=120, validate=True)
        write_snapshot(private_root, rep)
        return {k: rep["summary"][k] for k in ("evaluated", "exit_recovered_candidate", "needs_forward_only")}

    def _memory() -> dict[str, Any]:
        from src.research_lab.setup_outcome_memory import build_memory_index, summarize_memory, write_memory_snapshot
        write_memory_snapshot(private_root)
        s = summarize_memory(build_memory_index(private_root))
        return {"total": s["total"], "one_shot": s.get("one_shot_candidates"),
                "cost_bound_maker_unlock": s.get("cost_bound_maker_unlock")}

    for name, fn in (("live_universe", _universe), ("mover_validation", _validate),
                     ("tactical_track", _tactical), ("exit_first_resim", _exit), ("outcome_memory", _memory)):
        if is_stop_requested(private_root):
            steps.append({"step": name, "status": "skipped_stop_file"})
            continue
        _step(name, fn, steps)
    report = {"cycle_ts": now, "limit_symbols": limit_symbols, "steps": steps,
              "what_worked_failed": _synthesize(steps), "stopped": is_stop_requested(private_root)}
    _write_cycle(private_root, report)
    return report


def _synthesize(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """The what-worked / what-failed / why / next table from this cycle's step results."""
    by_step = {s["step"]: s for s in steps}
    worked: list[str] = []
    failed: list[dict[str, str]] = []
    val = (by_step.get("mover_validation") or {}).get("result") or {}
    for cell in val.get("by_cell", []):
        line = f"{cell['family']}/{cell['timeframe']}: IS {cell['is_median_net']:+} OOS {cell['oos_median_net']:+} ({cell['verdict']})"
        if cell["verdict"] == "holds_oos_candidate":
            worked.append(line + " -> forward-watch")
        elif cell["verdict"] == "in_sample_only":
            failed.append({"what": line, "why": "in-sample positive collapses out-of-sample (overfit)",
                           "next": "drop or re-test with a different exit / regime filter"})
        else:
            failed.append({"what": line, "why": "flat/negative OOS on the mover universe",
                           "next": "needs new bars (true-forward) or a different family/TF"})
    for s in steps:
        if str(s.get("status", "")).startswith("error"):
            failed.append({"what": s["step"], "why": s.get("status"), "next": "inspect detail; re-run step"})
    return {"worked": worked, "failed": failed,
            "note": "research-only; holds_oos_candidate goes to forward-watch, never paper-ready"}


def _write_cycle(private_root: Path, report: dict[str, Any]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "discovery_cycle.json"
    payload = {"schema": "discovery_cycle.v1",
               "disclaimer": "One bounded pass of the research contour. Research-only; nothing promoted "
                             "or paper-ready; refreshes derived views + intake queue only.",
               **report}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> None:
    import argparse
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="Bounded continuous discovery cycle (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--limit-symbols", type=int, default=20)
    ap.add_argument("--no-apply", action="store_true", help="do not register intake events")
    args = ap.parse_args()
    rep = run_cycle(Path(args.private_root), limit_symbols=args.limit_symbols, apply_intake=not args.no_apply)
    print(json.dumps({"steps": [{"step": s["step"], "status": s["status"]} for s in rep["steps"]],
                      "what_worked_failed": rep["what_worked_failed"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
