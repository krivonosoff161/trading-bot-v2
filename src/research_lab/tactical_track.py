# -*- coding: utf-8 -*-
"""Tactical track — a parallel, first-class verdict lane for small-n / one-shot setups (research-only).

The honest validator's count-walls (n<3/6/10) correctly stop a tactical setup from becoming a
statistical PAPER_FORWARD_READY — but they mislabel it (a 4-trade setup is tagged FAILED_OVERFIT, a
never-triggered setup is tagged a failure at all). This track classifies the SAME candidates on a
separate axis so the knowledge is not lost and a promising thin setup is routed to a forward-watch
instead of the trash:

  NO_EVENT             — n<3: the setup never triggered. NOT a bad setup; nothing to judge.
  KNOWN_BAD            — n>=POWER_FLOOR and net<=0: confirmed bad with power; do not recompute blindly.
  EXIT_PROBLEM         — MFE>1% but capture<0.3: the move happened, the exit gave it back (fixable).
  UNDERPOWERED_POSITIVE— 1<=n<POWER_FLOOR and net>0: a thin positive; too few trades to confirm.
  TACTICAL_LEAD        — an UNDERPOWERED_POSITIVE that also captured its move well (capture>=0.5).
  SHADOW_FORWARD       — already registered on the shadow/forward lane (watched on new bars).

NONE of these is paper-ready. TACTICAL_LEAD / UNDERPOWERED_POSITIVE are forward-watch candidates, never a
trade signal. Derived read-only from the Setup Outcome Memory; no order/money/live path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.setup_outcome_memory import build_memory_index

POWER_FLOOR = 10
MFE_MIN = 1.0
CAPTURE_MAX = 0.30
CAPTURE_GOOD = 0.50
TACTICAL_STATUSES = ("TACTICAL_LEAD", "SHADOW_FORWARD", "UNDERPOWERED_POSITIVE", "EXIT_PROBLEM",
                     "KNOWN_BAD", "NO_EVENT", "UNCLASSED")
_FORWARD_WATCH = {"TACTICAL_LEAD", "UNDERPOWERED_POSITIVE", "SHADOW_FORWARD"}


def tactical_verdict(record: dict[str, Any]) -> str:
    """Classify one outcome record onto the tactical axis (priority-ordered). Never paper-ready."""
    n = int(record.get("n_trades") or 0)
    net = float(record.get("baseline_net") or 0.0)
    mfe = float(record.get("avg_mfe_pct") or 0.0)
    cap = float(record.get("avg_capture_ratio") or 0.0)
    if record.get("shadow_status"):
        return "SHADOW_FORWARD"
    if n < 3:
        return "NO_EVENT"                       # never triggered — not a failure
    if 1 <= n < POWER_FLOOR and net > 0:        # a thin POSITIVE is a lead first (capture just splits it)
        return "TACTICAL_LEAD" if cap >= CAPTURE_GOOD else "UNDERPOWERED_POSITIVE"
    if mfe > MFE_MIN and 0.0 < cap < CAPTURE_MAX:  # move existed, result not positive -> the exit is the culprit
        return "EXIT_PROBLEM"
    if n >= POWER_FLOOR and net <= 0:
        return "KNOWN_BAD"
    return "UNCLASSED"


def build_track(private_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in build_memory_index(Path(private_root)):
        status = tactical_verdict(r)
        rows.append({"uc_key": r.get("uc_key"), "symbol": r.get("symbol"), "timeframe": r.get("timeframe"),
                     "family": r.get("family"), "n_trades": r.get("n_trades"), "net": r.get("baseline_net"),
                     "mfe": r.get("avg_mfe_pct"), "capture": r.get("avg_capture_ratio"),
                     "tactical_status": status, "forward_watch": status in _FORWARD_WATCH,
                     "paper_forward_ready": False})
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["tactical_status"]] = by_status.get(r["tactical_status"], 0) + 1
    leads = [r for r in rows if r["tactical_status"] == "TACTICAL_LEAD"]
    return {
        "total": len(rows),
        "by_status": dict(sorted(by_status.items(), key=lambda kv: -kv[1])),
        "forward_watch": sum(1 for r in rows if r["forward_watch"]),
        "no_event": by_status.get("NO_EVENT", 0),
        "tactical_leads": len(leads),
        "underpowered_positive": by_status.get("UNDERPOWERED_POSITIVE", 0),
        "exit_problem": by_status.get("EXIT_PROBLEM", 0),
        "known_bad": by_status.get("KNOWN_BAD", 0),
        "top_leads": sorted(leads, key=lambda r: -(float(r.get("net") or 0)))[:10],
        "paper_ready_leak": sum(1 for r in rows if r.get("paper_forward_ready")),  # invariant: must be 0
        "note": "parallel tactical lane; NO_EVENT != bad; leads/underpowered are forward-watch only, "
                "never paper-ready; the statistical validator is unchanged",
    }


def write_snapshot(private_root: Path) -> Path:
    rows = build_track(Path(private_root))
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "tactical_track.json"
    payload = {"schema": "tactical_track.v1",
               "disclaimer": "Parallel tactical verdict lane. Research-only; NO_EVENT is not a bad setup; "
                             "tactical leads are forward-watch, never paper-ready. Validator unchanged.",
               "summary": summarize(rows), "rows": rows}
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
    ap = argparse.ArgumentParser(description="Tactical verdict track (research-only, never paper-ready).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    rows = build_track(Path(args.private_root))
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2, default=str))
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root)))


if __name__ == "__main__":
    main()
