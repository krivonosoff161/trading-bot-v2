# -*- coding: utf-8 -*-
"""Setup Outcome Memory — a derived, rebuildable read-model over the farm's canonical
artifacts, plus a read-through gate so a REPEATED signal consults prior outcomes BEFORE
the farm spends compute.

It is NOT a second source of truth and adds NO registry: ``build_memory_index`` rebuilds
entirely from farm_tasks.unique_candidates + ``setup_lifecycle`` (which already joins
verdicts/cards/paper) + ``trade_path_diagnostics`` (reject sub-reason + path facts) +
``exit_recovery`` (recovered exits). Research-only: NO outcome here ever grants
PAPER_FORWARD_READY — that flag stays owned by the hard validator + setup cards.

Two layers:
  * ``build_gate_index`` / ``lookup``: the cheap hot path (unique_candidates columns only),
    consulted by the coordinator at plan time so an identical data fingerprint that already
    produced only dead candidates is not re-swept ("skip_known_bad"), a family cell that is
    historically all-rejected is deprioritised, and an eligible/recovered prior is flagged
    for re-validation ("revisit") instead of a blind fresh sweep.
  * ``build_memory_index`` / views: the rich read-model (joins per-trade path facts and
    recovered exits) for the derived snapshot + the positive / rejected / tactical /
    confirmed-bad / recovered / needs-data sub-views.

No money path, no orders, no live, no .env. Everything is read-only and derived.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.setup_lifecycle import HARD_FAILED, derive_setup_lifecycle
from src.research_lab.trade_path_diagnostics import POWER_FLOOR, characterize_rejects

ELIGIBLE_LITE = {"FORWARD_PAPER", "REGIME_SPECIFIC"}

# Closed outcome vocabulary (research-only labels). POSITIVE_VALIDATED is the ONLY class a
# hard-passed setup gets; none of these is a trade signal or grants paper-forward access.
OUTCOME_CLASSES = (
    "POSITIVE_VALIDATED", "EXIT_RECOVERED", "STATISTICAL_CANDIDATE", "THIN_BUT_PROMISING",
    "TACTICAL_1_2_TRADE", "WRONG_EXIT", "WRONG_TIMEFRAME", "COST_SENSITIVE",
    "NEEDS_OI_DATA", "CONFIRMED_BAD", "INSUFFICIENT_DATA", "UNCHARACTERIZED",
)

_SUBREASON_TO_CLASS = {
    "tactical_candidate": "TACTICAL_1_2_TRADE",
    "validator_too_strict": "THIN_BUT_PROMISING",
    "wrong_exit": "WRONG_EXIT",
    "wrong_timeframe": "WRONG_TIMEFRAME",
    "wrong_costs": "COST_SENSITIVE",
    "missing_oi_micro": "NEEDS_OI_DATA",
    "confirmed_bad": "CONFIRMED_BAD",
    "insufficient_data": "INSUFFICIENT_DATA",
    "uncharacterized": "UNCHARACTERIZED",
}
_POSITIVE_STATES = {"HARD_PASSED", "PAPER_PLAN_READY", "PAPER_POSITIVE_OBSERVED"}

# A family cell needs this many priors, this share rejected with zero eligible, to down-rank.
DEPRIORITIZE_MIN = 5
DEPRIORITIZE_BAD_SHARE = 0.8


def derive_outcome_class(lifecycle_state: str, subreason: str, recovered: bool,
                         hard_status: str = "") -> str:
    """Unify hard verdict + lifecycle state + reject sub-reason + recovered flag into one label.

    POSITIVE_VALIDATED means "cleared the hard validator" — it stays positive even if a later
    paper sample is negative (that fact lives in lifecycle_state / paper fields, not here).
    """
    if hard_status == "PAPER_FORWARD_READY" or lifecycle_state in _POSITIVE_STATES:
        return "POSITIVE_VALIDATED"
    if recovered:
        return "EXIT_RECOVERED"
    if lifecycle_state == "HARD_ELIGIBLE":
        return "STATISTICAL_CANDIDATE"
    return _SUBREASON_TO_CLASS.get(subreason, "UNCHARACTERIZED")


def _okx_inst(symbol: str) -> str:
    return str(symbol or "").replace("_", "-")


# ── read-through gate (cheap hot path: unique_candidates columns only) ──────────────────
@dataclass
class GateIndex:
    by_sweep: dict[str, dict[str, Any]] = field(default_factory=dict)  # sym|tf|fam|fingerprint
    by_cell: dict[str, dict[str, Any]] = field(default_factory=dict)   # sym|tf|fam


@dataclass
class MemoryVerdict:
    action: str            # fresh | skip_known_bad | deprioritize | revisit
    reason: str
    matched: str = "none"  # exact | cell | none
    prior_eligible: int = 0
    prior_confirmed_bad: int = 0
    prior_max_n_trades: int = 0


def _flags(row: dict[str, Any]) -> tuple[bool, bool, bool]:
    """(eligible, confirmed_bad, rejected) for one unique_candidates row."""
    lite = str(row.get("validation_status") or "")
    hard = str(row.get("hard_status") or "")
    decision = str(row.get("decision") or "")
    n = int(row.get("n_trades") or 0)
    avg = float(row.get("avg_net_pct") or 0.0)
    eligible = lite in ELIGIBLE_LITE or hard == "PAPER_FORWARD_READY"
    confirmed_bad = n >= POWER_FLOOR and avg <= 0
    rejected = decision == "REJECT" or lite == "REJECT" or hard in HARD_FAILED
    return eligible, confirmed_bad, rejected


def build_gate_index(candidates: list[dict[str, Any]]) -> GateIndex:
    """Aggregate prior outcomes per exact sweep (sym|tf|fam|fingerprint) and per family cell."""
    idx = GateIndex()
    for row in candidates:
        sym, tf = str(row.get("symbol") or ""), str(row.get("timeframe") or "")
        fam, fp = str(row.get("family") or ""), str(row.get("data_fingerprint") or "")
        eligible, confirmed_bad, rejected = _flags(row)
        n = int(row.get("n_trades") or 0)
        for key, store in ((f"{sym}|{tf}|{fam}|{fp}", idx.by_sweep), (f"{sym}|{tf}|{fam}", idx.by_cell)):
            agg = store.setdefault(
                key, {"n": 0, "n_eligible": 0, "n_confirmed_bad": 0, "n_rejected": 0, "max_n_trades": 0})
            agg["n"] += 1
            agg["n_eligible"] += int(eligible)
            agg["n_confirmed_bad"] += int(confirmed_bad)
            agg["n_rejected"] += int(rejected)
            agg["max_n_trades"] = max(agg["max_n_trades"], n)
    return idx


def lookup(index: GateIndex, *, symbol: str, timeframe: str, family: str,
           data_fingerprint: str) -> MemoryVerdict:
    """Read-through: what does prior memory say about (re-)computing this exact sweep?"""
    sweep = index.by_sweep.get(f"{symbol}|{timeframe}|{family}|{data_fingerprint}")
    if sweep and sweep["n"] > 0:
        v = MemoryVerdict("", "", "exact", sweep["n_eligible"],
                          sweep["n_confirmed_bad"], sweep["max_n_trades"])
        if sweep["n_eligible"] > 0:
            v.action, v.reason = "revisit", "prior_eligible_on_identical_data"
            return v
        # Identical fingerprint already produced ZERO eligible candidates — re-running the
        # SAME data reproduces it. Skip; the path to more evidence is NEW data / re-validation.
        v.action = "skip_known_bad"
        v.reason = ("confirmed_bad_identical_data" if sweep["n_confirmed_bad"] > 0
                    else "no_eligible_identical_data")
        return v
    cell = index.by_cell.get(f"{symbol}|{timeframe}|{family}")
    if cell and cell["n"] > 0:
        if cell["n_eligible"] > 0:
            return MemoryVerdict("revisit", "cell_has_eligible_prior", "cell",
                                 cell["n_eligible"], cell["n_confirmed_bad"], cell["max_n_trades"])
        if cell["n"] >= DEPRIORITIZE_MIN and cell["n_rejected"] / cell["n"] >= DEPRIORITIZE_BAD_SHARE:
            return MemoryVerdict("deprioritize", "cell_historically_all_rejected", "cell",
                                 0, cell["n_confirmed_bad"], cell["max_n_trades"])
    return MemoryVerdict("fresh", "unseen_or_inconclusive")


# ── rich read-model (joins per-trade path facts + recovered exits) ──────────────────────
def _recovered_cells(private_root: Path) -> set[str]:
    """sym|tf|fam cells flagged by exit_recovery's derived snapshot (coarse — it has no uc_key)."""
    path = Path(private_root) / "state" / "derived" / "exit_recovery.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    cands = data.get("exit_recovered_candidates", []) if isinstance(data, dict) else []
    return {f"{c.get('symbol')}|{c.get('timeframe')}|{c.get('family')}" for c in cands if isinstance(c, dict)}


def _uc_run_dirs(private_root: Path) -> dict[str, str]:
    path = tasks_db_path(private_root)
    if not path.exists():
        return {}
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT uc_key, run_dir_label FROM unique_candidates").fetchall()
    finally:
        conn.close()
    return {str(r["uc_key"]): str(r["run_dir_label"] or "") for r in rows}


def build_memory_index(private_root: Path) -> list[dict[str, Any]]:
    """One unified outcome record per unique candidate (derived; never written back to source)."""
    private_root = Path(private_root)
    lifecycle = derive_setup_lifecycle(private_root)
    subreason = {r["uc_key"]: r for r in characterize_rejects(private_root)}
    recovered_cells = _recovered_cells(private_root)
    run_dirs = _uc_run_dirs(private_root)
    records: list[dict[str, Any]] = []
    for lc in lifecycle:
        uc = lc["uc_key"]
        cell = f"{lc['symbol']}|{lc['timeframe']}|{lc['family']}"
        sub = subreason.get(uc, {})
        sub_reason = str(sub.get("reject_subreason") or "")
        recovered = sub_reason == "wrong_exit" and cell in recovered_cells
        outcome = derive_outcome_class(lc["derived_lifecycle_state"], sub_reason, recovered,
                                       lc["hard_status"])
        records.append({
            "uc_key": uc, "symbol": lc["symbol"], "okx_inst": _okx_inst(lc["symbol"]),
            "timeframe": lc["timeframe"], "family": lc["family"],
            "params_hash": lc["params_hash"], "data_fingerprint": lc["data_fingerprint"],
            "regime_bucket": lc["regime_bucket"], "n_trades": lc["n_trades"],
            "baseline_net": lc["avg_net_pct"],
            "avg_mfe_pct": float(sub.get("avg_mfe_pct") or 0.0),
            "avg_mae_pct": float(sub.get("avg_mae_pct") or 0.0),
            "avg_capture_ratio": float(sub.get("avg_capture_ratio") or 0.0),
            "lite_status": lc["lite_status"], "hard_status": lc["hard_status"],
            "validation_state": lc["validation_state"], "tactical_status": lc["tactical_status"],
            "rejection_reason": sub_reason,
            "recovery_reason": "exit_recovered_cell" if recovered else "",
            "outcome_class": outcome, "lifecycle_state": lc["derived_lifecycle_state"],
            "paper_forward_ready": bool(lc["paper_forward_ready"]),
            "paper_outcome_count": lc["paper_outcome_count"], "artifact_ref": run_dirs.get(uc, ""),
        })
    return records


# ── sub-views (the "separate databases" as filters over the one index) ──────────────────
def _of_class(records: list[dict[str, Any]], classes: set[str]) -> list[dict[str, Any]]:
    return [r for r in records if r["outcome_class"] in classes]


def positive_setups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"POSITIVE_VALIDATED"})


def recovered_setups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"EXIT_RECOVERED"})


def statistical_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"STATISTICAL_CANDIDATE"})


def tactical_setups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"TACTICAL_1_2_TRADE", "THIN_BUT_PROMISING"})


def rejected_research(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"WRONG_EXIT", "WRONG_TIMEFRAME", "COST_SENSITIVE",
                               "TACTICAL_1_2_TRADE", "THIN_BUT_PROMISING", "NEEDS_OI_DATA"})


def confirmed_bad_setups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"CONFIRMED_BAD"})


def needs_data_setups(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _of_class(records, {"INSUFFICIENT_DATA", "NEEDS_OI_DATA"})


def summarize_memory(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_class: dict[str, int] = {}
    for r in records:
        by_class[r["outcome_class"]] = by_class.get(r["outcome_class"], 0) + 1
    return {
        "total": len(records),
        "by_outcome_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
        "positive": len(positive_setups(records)),
        "recovered": len(recovered_setups(records)),
        "statistical_candidates": len(statistical_candidates(records)),
        "tactical": len(tactical_setups(records)),
        "rejected_research": len(rejected_research(records)),
        "confirmed_bad": len(confirmed_bad_setups(records)),
        "needs_data": len(needs_data_setups(records)),
        # Invariant guard: paper-forward-ready is allowed ONLY behind a hard PAPER_FORWARD_READY
        # verdict. A non-zero count means a research/tactical label leaked paper access — a bug.
        "paper_ready_without_hard_pass": sum(
            1 for r in records if r["paper_forward_ready"] and r.get("hard_status") != "PAPER_FORWARD_READY"),
    }


def write_memory_snapshot(private_root: Path) -> Path:
    """Write the derived, rebuildable read-model (NOT a source of truth, never gates money)."""
    records = build_memory_index(private_root)
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "setup_outcome_memory.json"
    payload = {
        "schema": "setup_outcome_memory.v1",
        "disclaimer": "Derived read-model rebuilt from canonical sources. Research-only: no "
                      "outcome here grants PAPER_FORWARD_READY or is a trade signal.",
        "summary": summarize_memory(records),
        "records": records,
    }
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
    ap = argparse.ArgumentParser(description="Setup Outcome Memory (derived read-model + gate).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true", help="write the derived snapshot artifact")
    args = ap.parse_args()
    records = build_memory_index(Path(args.private_root))
    print(json.dumps(summarize_memory(records), ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_memory_snapshot(Path(args.private_root)))


if __name__ == "__main__":
    main()
