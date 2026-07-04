# -*- coding: utf-8 -*-
"""Exit-first re-sim across the WHOLE wrong-exit pool (Block 2, research-only).

The forensic found the #1 wall is not signal but EXIT: ~2220 candidates have MFE>1% but capture<0.3 — the
move happened, the exit gave it back, across momentum_breakout / bb_volume_fade / mean_reversion_fade.
exit_phase2 already re-sims dynamic exits but only for mean_reversion_fade. This runs the SAME audited,
no-look-ahead exit grid (early_tp / trailing / trailing_tight / break_even / time_decay / partial_tp /
hold_long) over EVERY family's wrong-exit candidates, with realistic per-TF holds inherited from each
candidate's validated params, and reports old_net → best_exit → delta per family/symbol/TF.

Reuses exit_phase2.recover_with_phase2 (family-agnostic) + the Setup Outcome Memory's MFE/capture facts.
Outcome classes: exit_recovered_candidate / needs_forward_only / exit_still_bad / thin_noise. Nothing is
paper-ready (a recovered exit is in-sample; needs_forward_only is a deflated-bridge survivor → shadow
only). Read-only; no order/money/live path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.exit_phase2 import recover_with_phase2
from src.research_lab.setup_outcome_memory import build_memory_index
from src.research_lab.shadow_forward import _params_for_uc

MFE_MIN = 1.0          # the move existed (favorable excursion > 1%)
CAPTURE_MAX = 0.30     # but the exit captured < 30% of it -> wrong exit
_RECOVERED = {"exit_recovered_candidate", "needs_forward_only"}


def wrong_exit_pool(private_root: Path, *, families: tuple[str, ...] | None = None,
                    limit: int | None = None) -> list[dict[str, Any]]:
    """Candidates with the wrong-exit signature (MFE>1% & capture<0.3) across ALL families."""
    out: list[dict[str, Any]] = []
    for r in build_memory_index(Path(private_root)):
        mfe = float(r.get("avg_mfe_pct") or 0.0)
        cap = float(r.get("avg_capture_ratio") or 0.0)
        if not (mfe > MFE_MIN and 0.0 < cap < CAPTURE_MAX):
            continue
        if families and r["family"] not in families:
            continue
        if not r.get("uc_key"):
            continue
        out.append({"uc_key": r["uc_key"], "symbol": r["symbol"], "timeframe": r["timeframe"],
                    "family": r["family"], "mfe": round(mfe, 3), "capture": round(cap, 3)})
    return out[:limit] if limit else out


def run(private_root: Path, *, families: tuple[str, ...] | None = None, limit: int | None = 200,
        validate: bool = True) -> dict[str, Any]:
    private_root = Path(private_root)
    rows: list[dict[str, Any]] = []
    skipped: dict[str, int] = {"no_params": 0, "no_candles": 0, "no_signals": 0, "no_result": 0}
    pool = wrong_exit_pool(private_root, families=families, limit=limit)
    for c in pool:
        params = _params_for_uc(private_root, c["uc_key"])
        if not params:
            skipped["no_params"] += 1   # candidate's params not in the brain (legacy/old run path)
            continue
        res = recover_with_phase2(private_root, symbol=c["symbol"], timeframe=c["timeframe"],
                                  family=c["family"], params=params, validate=validate)
        if not res:
            skipped["no_result"] += 1
            continue
        if "outcome_class" not in res:
            # recover returns {"skipped": "no_candles"|"no_signals"} when it cannot reproduce the trades
            skipped[str(res.get("skipped") or "no_signals")] = \
                skipped.get(str(res.get("skipped") or "no_signals"), 0) + 1
            continue
        rows.append({"uc_key": c["uc_key"], "symbol": c["symbol"], "timeframe": c["timeframe"],
                     "family": c["family"], "mfe": c["mfe"], "capture": c["capture"],
                     "old_net": res["baseline_net"], "best_exit": res["best_mode"],
                     "best_net": res["best_net"], "delta": round(res["best_net"] - res["baseline_net"], 4),
                     "n_trades": res["n_trades"], "outcome_class": res["outcome_class"],
                     "paper_forward_ready": False})
    summary = _summarize(rows)
    summary["skipped"] = {k: v for k, v in skipped.items() if v}
    summary["skipped_total"] = len(pool) - len(rows)
    summary["coverage"] = round(len(rows) / len(pool), 3) if pool else 0.0
    return {"pool": len(pool), "evaluated": len(rows), "summary": summary, "rows": rows}


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_class: dict[str, int] = {}
    by_mode: dict[str, int] = {}
    by_family: dict[str, dict[str, int]] = {}
    for r in rows:
        by_class[r["outcome_class"]] = by_class.get(r["outcome_class"], 0) + 1
        fam = by_family.setdefault(r["family"], {"n": 0, "recovered": 0})
        fam["n"] += 1
        if r["outcome_class"] in _RECOVERED:
            by_mode[r["best_exit"]] = by_mode.get(r["best_exit"], 0) + 1
            fam["recovered"] += 1
    recovered = [r for r in rows if r["outcome_class"] in _RECOVERED]
    return {
        "evaluated": len(rows),
        "exit_recovered_candidate": by_class.get("exit_recovered_candidate", 0),
        "needs_forward_only": by_class.get("needs_forward_only", 0),
        "exit_still_bad": by_class.get("still_bad", 0),
        "thin_noise": by_class.get("thin_noise", 0),
        "by_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
        "best_exit_modes": dict(sorted(by_mode.items(), key=lambda kv: -kv[1])),
        "by_family": dict(sorted(by_family.items(), key=lambda kv: -kv[1]["n"])),
        "top_recovered": sorted(recovered, key=lambda r: -r["delta"])[:10],
        "note": "exit-first re-sim across all families (no look-ahead). recovered = in-sample best-of-grid "
                "improvement; needs_forward_only survived a deflated bridge -> shadow only; nothing paper-ready",
    }


def write_snapshot(private_root: Path, report: dict[str, Any]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "exit_first_resim.json"
    payload = {"schema": "exit_first_resim.v1",
               "disclaimer": "Exit-first re-sim across the whole wrong-exit pool. Research-only; recovered "
                             "is in-sample, needs_forward_only is shadow-only, nothing is paper-ready.",
               "pool": report.get("pool"), "evaluated": report.get("evaluated"),
               "summary": report.get("summary"),
               "by_uc_key": {r["uc_key"]: r for r in report.get("rows", [])}}
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
    ap = argparse.ArgumentParser(description="Exit-first re-sim across the whole wrong-exit pool (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--families", default="", help="comma-separated families; empty = all")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--no-validate", action="store_true")
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    fams = tuple(s.strip() for s in args.families.split(",") if s.strip()) or None
    report = run(Path(args.private_root), families=fams, limit=args.limit, validate=not args.no_validate)
    print(json.dumps({"pool": report["pool"], "evaluated": report["evaluated"], "summary": report["summary"]},
                     ensure_ascii=False, indent=2, default=str))
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), report))


if __name__ == "__main__":
    main()
