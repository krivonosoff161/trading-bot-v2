# -*- coding: utf-8 -*-
"""Strategy library — one readable row per tested setup, so a new signal checks history before compute.

A derived, rebuildable JOIN over the existing snapshots (no new source of truth): Setup Outcome Memory
(family/params/tf/result/cost/tactical) + exit_first_resim (recovered exit_mode) + tactical_track
(forward_status) + the live-universe snapshot (universe_source). Each row answers, in human terms:

  family · symbol · timeframe · params_hash · universe_source · exit_mode · result · failure_reason ·
  forward_status

``lookup`` lets the farm consult the library at signal time: if an identical setup is already KNOWN_BAD
on this data, do not recompute; if it is a TACTICAL_LEAD / forward-watch, route it there; only a genuinely
new (symbol, tf, family, params) is fresh. Read-only; nothing here is paper-ready or an order path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.setup_outcome_memory import build_memory_index


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _live_mover_symbols(private_root: Path) -> set[str]:
    snap = _read_json(Path(private_root) / "discovery" / "live_universe.json")
    out: set[str] = set()
    for syms in (snap.get("groups") or {}).values():
        out.update(syms or [])
    return out


def build_library(private_root: Path) -> list[dict[str, Any]]:
    private_root = Path(private_root)
    derived = private_root / "state" / "derived"
    exit_by_uc = _read_json(derived / "exit_first_resim.json").get("by_uc_key") or {}
    tactical_by_uc = {r.get("uc_key"): r for r in (_read_json(derived / "tactical_track.json").get("rows") or [])}
    movers = _live_mover_symbols(private_root)
    rows: list[dict[str, Any]] = []
    for r in build_memory_index(private_root):
        uc = r.get("uc_key")
        ex = exit_by_uc.get(uc) or {}
        tac = tactical_by_uc.get(uc) or {}
        rows.append({
            "family": r["family"], "symbol": r["symbol"], "timeframe": r["timeframe"],
            "params_hash": r["params_hash"],
            "universe_source": "live_mover" if r["symbol"] in movers else "grind",
            "exit_mode": ex.get("best_exit") or "baseline",
            "result": {"n_trades": r["n_trades"], "net": r["baseline_net"],
                       "outcome_class": r["outcome_class"], "cost_class": r.get("cost_class"),
                       "exit_recovered_delta": ex.get("delta")},
            "failure_reason": r.get("rejection_reason") or r.get("next_action") or "",
            "forward_status": tac.get("tactical_status") or r.get("shadow_status") or "",
            "paper_forward_ready": False,
        })
    return rows


def lookup(library: list[dict[str, Any]], *, symbol: str, timeframe: str, family: str,
           params_hash: str | None = None) -> dict[str, Any]:
    """What does the library already know about this (symbol, tf, family[, params])? For plan-time gating."""
    matches = [r for r in library if r["symbol"] == symbol and r["timeframe"] == timeframe
               and r["family"] == family and (params_hash is None or r["params_hash"] == params_hash)]
    if not matches:
        return {"known": False, "action": "fresh"}
    statuses = {r["forward_status"] for r in matches if r["forward_status"]}
    classes = {r["result"]["outcome_class"] for r in matches}
    if "TACTICAL_LEAD" in statuses or "SHADOW_FORWARD" in statuses:
        return {"known": True, "action": "forward_watch", "n": len(matches)}
    if classes and classes <= {"CONFIRMED_BAD"}:
        return {"known": True, "action": "skip_known_bad", "n": len(matches)}
    return {"known": True, "action": "revisit", "n": len(matches)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, int] = {}
    by_exit: dict[str, int] = {}
    by_forward: dict[str, int] = {}
    for r in rows:
        by_source[r["universe_source"]] = by_source.get(r["universe_source"], 0) + 1
        by_exit[r["exit_mode"]] = by_exit.get(r["exit_mode"], 0) + 1
        fs = r["forward_status"] or "(none)"
        by_forward[fs] = by_forward.get(fs, 0) + 1
    return {"total": len(rows), "by_universe_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
            "by_exit_mode": dict(sorted(by_exit.items(), key=lambda kv: -kv[1])),
            "by_forward_status": dict(sorted(by_forward.items(), key=lambda kv: -kv[1])),
            "note": "readable derived library; check before compute. Research-only; nothing paper-ready"}


def write_snapshot(private_root: Path) -> Path:
    rows = build_library(Path(private_root))
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "strategy_library.json"
    payload = {"schema": "strategy_library.v1",
               "disclaimer": "Readable derived strategy library (join over memory/exit/tactical/universe). "
                             "Consult before compute. Research-only; nothing paper-ready or an order path.",
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
    ap = argparse.ArgumentParser(description="Readable strategy library (research-only, check before compute).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    rows = build_library(Path(args.private_root))
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root)))


if __name__ == "__main__":
    main()
