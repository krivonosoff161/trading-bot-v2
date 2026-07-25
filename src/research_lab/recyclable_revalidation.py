# -*- coding: utf-8 -*-
"""Phase D — honest re-validation of the recyclable shelf (research-only, dry-run).

The Setup Outcome Memory holds recyclable rejects: exit_recovered (wrong_exit that a different
in-sample exit rescued), validator_too_strict (3-9 trades, net-positive, below the n=10 power
floor) and tactical_candidate (1-2 trades). This module re-runs each through the SAME honest-backtest
bridge the validator uses, WITH the multiple-testing (Sidak) correction, to answer the only honest
question: does any survive proper statistics?

  * exit_recovered     — re-simulated UNDER its recovered exit, n_trials = exit-grid size (the
                         recovered exit is a best-of-grid choice, so significance is deflated by it);
  * validator_too_strict / tactical_candidate — re-simulated at baseline, n_trials = 1.

Everything runs ``dry_run=True``: NO verdict/report artifact is written, so the canonical lifecycle
and hard_status are untouched. A survivor is recorded as a research-only ``revalidation_status`` —
it is NEVER auto-promoted to PAPER_FORWARD_READY (that needs a human GO). Read-only re-simulation,
bounded by a per-bucket limit; no money/order/live path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.exit_recovery import (
    COST_PCT,
    FEES_BPS,
    MIN_TRADES_RECOVER,
    RECOVER_MARGIN,
    SLIP_BPS,
    _exit_grid,
)
from src.research_lab.experiment import (
    generate_signals,
    simulate_trades,
)
from src.research_lab.candle_library import load_canonical_candles
from src.research_lab.hard_validation_contract import (
    CONTRACT_VERSION,
    CandidateForValidation,
    trade_evidence_hash,
)
from src.research_lab.honest_backtest_bridge import run_validation
from src.research_lab.simulator_contract import (
    validate_simulator_assumption_manifest,
    validate_trade_contract,
)
from src.research_lab.trade_path_diagnostics import (
    _index_run_results,
    _load_rejected_uc,
    _trade_path_facts,
    classify_subreason,
    oi_micro_families,
)

REVALIDATE_SUBREASONS = ("wrong_exit", "validator_too_strict", "tactical_candidate")
# A bucket that is even theoretically promotable on survival (others stay research-only).
PASS_STATUS = "PAPER_FORWARD_READY"


def _avg_net(trades: list[dict[str, Any]]) -> float:
    nets = [float(t.get("net_pct") or 0.0) for t in trades]
    return sum(nets) / len(nets) if nets else 0.0


def _select(private_root: Path, limit_per_bucket: int | None) -> list[dict[str, Any]]:
    """Recyclable rejects (wrong_exit / validator_too_strict / tactical) with stored params."""
    oi_micro = oi_micro_families()
    cache: dict[str, dict[str, dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for r in _load_rejected_uc(private_root):
        label = str(r.get("run_dir_label") or "")
        if label not in cache:
            cache[label] = _index_run_results(private_root, label)
        result = cache[label].get(str(r.get("params_hash") or ""))
        if not result:
            continue
        sub = classify_subreason(
            _trade_path_facts(result),
            str(r.get("hard_status") or ""),
            str(r.get("family") or ""),
            oi_micro,
        )
        if sub not in REVALIDATE_SUBREASONS:
            continue
        if limit_per_bucket and counts.get(sub, 0) >= limit_per_bucket:
            continue
        counts[sub] = counts.get(sub, 0) + 1
        metrics = (
            raw_metrics
            if isinstance(raw_metrics := result.get("metrics"), dict)
            else {}
        )
        epoch = (
            raw_epoch
            if isinstance(raw_epoch := metrics.get("validation_epoch"), dict)
            else {}
        )
        out.append(
            {
                "uc_key": str(r.get("uc_key") or ""),
                "symbol": str(r.get("symbol") or ""),
                "timeframe": str(r.get("timeframe") or ""),
                "family": str(r.get("family") or ""),
                "params": dict(result.get("params") or {}),
                "subreason": sub,
                "evidence_stage": str(epoch.get("evidence_stage") or "selection_only"),
                "selection_data_fingerprint": str(
                    epoch.get("selection_data_fingerprint") or ""
                ),
                "selection_evidence_hash": str(
                    epoch.get("selection_evidence_hash") or ""
                ),
                "selection_evidence": list(epoch.get("selection_evidence") or []),
                "evaluation_data_fingerprint": str(
                    epoch.get("evaluation_data_fingerprint") or ""
                ),
                "hypothesis_frozen_at": str(epoch.get("hypothesis_frozen_at") or ""),
                "evaluation_started_at": str(epoch.get("evaluation_started_at") or ""),
            }
        )
    return out


def _resim(
    private_root: Path, item: dict[str, Any]
) -> tuple[list[dict[str, Any]], str, int, dict[str, Any]] | None:
    """Return (trades, exit_name, n_trials). wrong_exit -> best RECOVERED exit; else baseline."""
    selected = load_canonical_candles(
        private_root,
        item["symbol"],
        item["timeframe"],
        purpose="recyclable_revalidation",
        coverage_policy="gap_free",
    )
    candles = selected.rows
    if not candles:
        return None
    signals = generate_signals(candles, item["family"], item["params"])
    if not signals:
        return None
    if item["subreason"] != "wrong_exit":
        return (
            simulate_trades(
                candles,
                signals,
                item["params"],
                fees_bps=FEES_BPS,
                slippage_bps=SLIP_BPS,
            ),
            "baseline",
            1,
            selected.manifest.to_dict(),
        )
    grid = _exit_grid(item["params"])
    variants = {
        name: simulate_trades(
            candles,
            signals,
            {**item["params"], **ov},
            fees_bps=FEES_BPS,
            slippage_bps=SLIP_BPS,
        )
        for name, ov in grid
    }
    base = variants["baseline"]
    others = {k: v for k, v in variants.items() if k != "baseline"}
    best = max(others, key=lambda k: _avg_net(others[k]))
    # Only the RECOVERED wrong_exit (same gate as T3-A) re-validates under its recovered exit.
    if not (
        _avg_net(others[best]) > COST_PCT
        and _avg_net(others[best]) > _avg_net(base) + RECOVER_MARGIN
        and len(base) >= MIN_TRADES_RECOVER
    ):
        return None
    return variants[best], best, len(grid), selected.manifest.to_dict()


def _candidate(
    item: dict[str, Any],
    trades: list[dict[str, Any]],
    n_trials: int,
    snapshot_manifest: dict[str, Any] | None = None,
) -> CandidateForValidation:
    """Build a validation candidate from re-simulated trades; lite_status FORWARD_PAPER isolates
    the STATISTICAL question (these were lite-rejected; we re-test the stats, not the lite gate)."""
    if not trades:
        raise ValueError("revalidation candidate requires simulator-bound trades")
    simulator_manifest = validate_simulator_assumption_manifest(
        dict(trades[0].get("simulator_manifest") or {})
    )
    for trade in trades:
        validate_trade_contract(trade, simulator_manifest)
    unsupported = list(simulator_manifest["unsupported_dimensions"])
    return CandidateForValidation.from_dict(
        {
            "contract_version": CONTRACT_VERSION,
            "candidate_id": f"reval::{item['uc_key']}",
            "source_run_id": "recyclable_revalidation",
            "symbol": item["symbol"],
            "normalized_symbol": item["symbol"],
            "timeframe": item["timeframe"],
            "strategy_id": item["family"],
            "params": item["params"],
            "fees_bps": FEES_BPS,
            "slippage_bps": SLIP_BPS,
            "lite_status": "FORWARD_PAPER",
            "simulator_manifest": simulator_manifest,
            "unsupported_simulator_dimensions": unsupported,
            "metrics": {
                "n_trades": len(trades),
                "data_fingerprint": (
                    item.get("data_fingerprint")
                    or str(item["uc_key"]).rsplit("::", 1)[-1]
                ),
                "returns_basis": "net_pct",
                "costs_applied": True,
                "validation_epoch": {
                    "schema": "ValidationEpoch.v1",
                    "evidence_stage": str(
                        item.get("evidence_stage") or "selection_only"
                    ),
                    "selection_data_fingerprint": str(
                        item.get("selection_data_fingerprint") or ""
                    ),
                    "selection_evidence_hash": str(
                        item.get("selection_evidence_hash") or ""
                    ),
                    "selection_evidence": list(item.get("selection_evidence") or []),
                    "evaluation_data_fingerprint": str(
                        item.get("evaluation_data_fingerprint") or ""
                    ),
                    "evaluation_evidence_hash": trade_evidence_hash(trades),
                    "hypothesis_frozen_at": str(item.get("hypothesis_frozen_at") or ""),
                    "evaluation_started_at": str(
                        item.get("evaluation_started_at") or ""
                    ),
                },
                "data_snapshot_id": str(
                    (snapshot_manifest or {}).get("snapshot_id") or ""
                ),
                "data_evidence_hash": str(
                    (snapshot_manifest or {}).get("evidence_hash") or ""
                ),
                "data_provenance_status": str(
                    (snapshot_manifest or {}).get("provenance_status")
                    or "legacy_unknown"
                ),
                "simulator_manifest": simulator_manifest,
                "simulator_model_id": simulator_manifest["simulator_model_id"],
                "simulator_evidence_tier": simulator_manifest["evidence_tier"],
                "unsupported_simulator_dimensions": unsupported,
                "runtime": {"n_variants_evaluated": int(n_trials)},
            },
            "trades": [dict(t) for t in trades],
        }
    )


def revalidate(
    private_root: Path, *, limit_per_bucket: int | None = None
) -> list[dict[str, Any]]:
    """Re-validate the recyclable shelf through the honest bridge (dry-run, no artifacts)."""
    private_root = Path(private_root)
    rows: list[dict[str, Any]] = []
    for it in _select(private_root, limit_per_bucket):
        rs = _resim(private_root, it)
        if rs is None:
            continue
        trades, exit_name, n_trials, snapshot_manifest = rs
        verdict = run_validation(
            _candidate(it, trades, n_trials, snapshot_manifest),
            private_root,
            dry_run=True,
        )
        bucket = (
            "exit_recovered" if it["subreason"] == "wrong_exit" else it["subreason"]
        )
        rows.append(
            {
                "uc_key": it["uc_key"],
                "symbol": it["symbol"],
                "timeframe": it["timeframe"],
                "family": it["family"],
                "bucket": bucket,
                "n_trades": len(trades),
                "exit": exit_name,
                "n_trials": n_trials,
                "data_snapshot_id": str(snapshot_manifest.get("snapshot_id") or ""),
                "data_evidence_hash": str(snapshot_manifest.get("evidence_hash") or ""),
                "data_provenance_status": str(
                    snapshot_manifest.get("provenance_status") or ""
                ),
                "revalidation_status": str(verdict.get("hard_status") or ""),
                "hypothesis_frozen_at": str(it.get("hypothesis_frozen_at") or ""),
                "selection_cutoff_ts": max(
                    (t.get("exit_ts") or t.get("entry_ts") or 0)
                    for t in (it.get("selection_evidence") or [{}])
                ),
                "selection_data_fingerprint": str(
                    it.get("selection_data_fingerprint") or ""
                ),
                "selection_evidence": list(it.get("selection_evidence") or []),
            }
        )
    return rows


def summarize_revalidation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_bucket: dict[str, dict[str, int]] = {}
    survivors: list[dict[str, Any]] = []
    survivor_fields = (
        "uc_key",
        "symbol",
        "timeframe",
        "family",
        "bucket",
        "n_trades",
        "exit",
        "hypothesis_frozen_at",
        "selection_cutoff_ts",
        "selection_data_fingerprint",
        "selection_evidence",
        "data_snapshot_id",
        "data_evidence_hash",
        "data_provenance_status",
    )
    for r in rows:
        b = by_bucket.setdefault(r["bucket"], {})
        st = str(r["revalidation_status"])
        if st == PASS_STATUS and any(not r.get(key) for key in survivor_fields):
            st = "NEEDS_MORE_DATA"
        b[st] = b.get(st, 0) + 1
        if st == PASS_STATUS:
            survivors.append({key: r[key] for key in survivor_fields})
    return {
        "total": len(rows),
        "by_bucket": {
            b: dict(sorted(c.items(), key=lambda kv: -kv[1]))
            for b, c in by_bucket.items()
        },
        "survivors": len(survivors),
        "survivor_rows": survivors,
        "verdict": (
            "0 survived honest multiple-testing re-validation - the recyclable shelf is "
            "characterization, not edge"
            if not survivors
            else f"{len(survivors)} survived in-sample re-validation; NEEDS human GO + OOS "
            "forward-paper before any promotion (never auto paper-ready)"
        ),
    }


def write_revalidation_snapshot(private_root: Path, rows: list[dict[str, Any]]) -> Path:
    """Derived, research-only snapshot keyed by uc_key. Survivors are NOT promoted here."""
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "recyclable_revalidation.json"
    by_uc = {r["uc_key"]: r for r in rows if r.get("uc_key")}
    payload = {
        "schema": "recyclable_revalidation.v1",
        "disclaimer": "Honest dry-run re-validation of recyclable rejects with multiple-testing. "
        "Research-only: a survivor is a candidate for human review + OOS forward-paper, "
        "NEVER auto paper-ready. No canonical verdict/hard_status was written.",
        "summary": summarize_revalidation(rows),
        "by_uc_key": by_uc,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> None:
    import argparse
    import os
    import sys

    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT

    ap = argparse.ArgumentParser(
        description="Phase D recyclable re-validation (honest, dry-run)."
    )
    ap.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
    )
    ap.add_argument("--limit-per-bucket", type=int, default=0, help="0 = all")
    ap.add_argument(
        "--snapshot",
        action="store_true",
        help="write the derived revalidation artifact",
    )
    args = ap.parse_args()
    lim = args.limit_per_bucket or None
    rows = revalidate(Path(args.private_root), limit_per_bucket=lim)
    print(json.dumps(summarize_revalidation(rows), ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_revalidation_snapshot(Path(args.private_root), rows))


if __name__ == "__main__":
    main()
