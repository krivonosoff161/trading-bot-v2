# -*- coding: utf-8 -*-
"""Derived trade-path characterization of rejected candidates (read-only, no compute).

A rejected/failed setup is not proven-bad. This module re-reads the EXISTING run
artifacts (experiments/<run_dir>/metrics.json, which already carry per-trade
mfe/mae/capture/outcome + an entry_timing aggregate) and the deduped
farm_tasks.unique_candidates rows, and derives, WITHOUT re-running any sweep/sim:

  * a deterministic reject sub-reason (the 8-way taxonomy: insufficient_data /
    tactical_candidate / wrong_exit / wrong_timeframe / wrong_costs /
    missing_oi_micro / validator_too_strict / confirmed_bad);
  * trade-path facts (avg/ best/ worst net, MFE/MAE, capture ratio, late-entry,
    tp-before-sl from the recorded outcome, timeout share).

It writes nothing back to the source DBs and never re-computes a trade; it is a pure
read of what already happened. No money path, no orders, no live.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.data_fingerprint import params_hash
from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.strategy_registry import REGISTRY, get_strategy

# Round-trip taker cost floor (fees 7bps + slippage 3bps = 0.1%) — the same assumption
# the validator's cost check uses; a tactical window must clear it to be worth recycling.
COST_PCT = 0.1
CAPTURE_LATE = 0.3          # avg_capture_ratio below this => entry gave back the move
THIN_MAX = 2               # n_trades in 1..THIN_MAX = a tactical (too-thin-for-validator) window
POWER_FLOOR = 10           # validator _check_splits fails below n=10 => below this it had no power


def oi_micro_families() -> set[str]:
    """Families that declare oi/microstructure data needs (reuse the registry, do not duplicate)."""
    out: set[str] = set()
    for fam in REGISTRY:
        req = set(get_strategy(fam).required_data)
        if req & {"oi", "microstructure"}:
            out.add(fam)
    return out


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_rejected_uc(private_root: Path) -> list[dict[str, Any]]:
    import sqlite3
    path = tasks_db_path(private_root)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        # Rejected = lite REJECT OR any hard failure (FAILED_*/HARD_REJECT/REGIME_ONLY),
        # so hard-failed FORWARD_PAPER candidates (e.g. FAILED_COSTS) are characterized too.
        rows = conn.execute(
            "SELECT uc_key, symbol, timeframe, family, params_hash, n_trades, avg_net_pct, "
            "regime_bucket, hard_status, validation_status, decision, run_dir_label "
            "FROM unique_candidates "
            "WHERE decision='REJECT' OR validation_status='REJECT' "
            "   OR hard_status LIKE 'FAILED%' OR hard_status IN ('HARD_REJECT','REGIME_ONLY')"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _index_run_results(private_root: Path, run_dir_label: str) -> dict[str, dict[str, Any]]:
    """params_hash -> result dict for one run's metrics.json (empty if missing)."""
    if not run_dir_label:
        return {}
    data = _read_json(Path(private_root) / run_dir_label / "metrics.json")
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for res in results:
        if isinstance(res, dict):
            out[params_hash(res.get("params") or {})] = res
    return out


def _trade_path_facts(result: dict[str, Any]) -> dict[str, Any]:
    """Aggregate trade-path facts from a result's stored per-trade records + entry_timing."""
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    trades = result.get("trades") or result.get("_trades") or []
    nets = [float(t.get("net_pct") or 0.0) for t in trades if isinstance(t, dict)]
    outcomes = [str(t.get("outcome") or "") for t in trades if isinstance(t, dict)]
    et = metrics.get("entry_timing") if isinstance(metrics.get("entry_timing"), dict) else {}
    n_tp = sum(1 for o in outcomes if o == "tp")
    n_sl = sum(1 for o in outcomes if o in ("stop", "sl"))
    n_timeout = sum(1 for o in outcomes if o in ("time_exit", "timeout"))
    return {
        "n_trades": int(metrics.get("n_trades") or len(trades)),
        "avg_net_pct": round(sum(nets) / len(nets), 4) if nets else float(metrics.get("avg_net_pct") or 0.0),
        "best_net_pct": round(max(nets), 4) if nets else 0.0,
        "worst_net_pct": round(min(nets), 4) if nets else 0.0,
        "avg_mfe_pct": float(et.get("avg_mfe_pct") or 0.0),
        "avg_mae_pct": float(et.get("avg_mae_pct") or 0.0),
        "avg_capture_ratio": float(et.get("avg_capture_ratio") or 0.0),
        "late_entry_rate": float(et.get("late_entry_rate") or 0.0),
        "n_tp": n_tp, "n_sl": n_sl, "n_timeout": n_timeout,
        "tp_before_sl_share": round(n_tp / len(outcomes), 4) if outcomes else 0.0,
        "trades_available": bool(trades),
    }


def classify_subreason(facts: dict[str, Any], hard_status: str, family: str,
                       oi_micro: set[str]) -> str:
    """Deterministic 8-way reject taxonomy from trade-path facts + hard_status + family."""
    n = int(facts.get("n_trades") or 0)
    avg = float(facts.get("avg_net_pct") or 0.0)
    best = float(facts.get("best_net_pct") or 0.0)
    mfe = float(facts.get("avg_mfe_pct") or 0.0)
    capture = float(facts.get("avg_capture_ratio") or 0.0)
    late = float(facts.get("late_entry_rate") or 0.0)
    if family in oi_micro:
        return "missing_oi_micro"
    if n == 0:
        return "insufficient_data"
    # Edge existed but the exit gave the move back (positive MFE, poor capture).
    if facts.get("trades_available") and mfe > avg + COST_PCT and capture < CAPTURE_LATE:
        return "wrong_exit"
    # Late entry on a real move (capture poor / majority late).
    if facts.get("trades_available") and (late >= 0.5 or (0.0 < capture < CAPTURE_LATE)) and mfe > COST_PCT:
        return "wrong_timeframe"
    if hard_status == "FAILED_COSTS" and (mfe > COST_PCT or best > COST_PCT):
        return "wrong_costs"
    # 1-2 trades the validator could never score, but the trades it had were net-positive.
    if n <= THIN_MAX and (avg > 0 or best > COST_PCT):
        return "tactical_candidate"
    # 3..9 trades: the validator's split/PSR checks auto-fail below n=10 even if net-positive.
    if 3 <= n < POWER_FLOOR and avg > 0:
        return "validator_too_strict"
    # Validator had power (n>=10) and it still failed net-negative.
    if n >= POWER_FLOOR and avg <= 0:
        return "confirmed_bad"
    return "uncharacterized"


_RECYCLABLE = {"tactical_candidate", "wrong_exit", "wrong_timeframe", "wrong_costs",
               "missing_oi_micro", "validator_too_strict"}


def characterize_rejects(private_root: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """One row per rejected unique_candidate with sub-reason + trade-path facts."""
    private_root = Path(private_root)
    oi_micro = oi_micro_families()
    uc = _load_rejected_uc(private_root)
    if limit:
        uc = uc[:limit]
    cache: dict[str, dict[str, dict[str, Any]]] = {}
    rows: list[dict[str, Any]] = []
    for r in uc:
        label = str(r.get("run_dir_label") or "")
        if label not in cache:
            cache[label] = _index_run_results(private_root, label)
        result = cache[label].get(str(r.get("params_hash") or ""))
        facts = _trade_path_facts(result) if result else {
            "n_trades": int(r.get("n_trades") or 0),
            "avg_net_pct": float(r.get("avg_net_pct") or 0.0),
            "best_net_pct": 0.0, "worst_net_pct": 0.0, "avg_mfe_pct": 0.0, "avg_mae_pct": 0.0,
            "avg_capture_ratio": 0.0, "late_entry_rate": 0.0, "n_tp": 0, "n_sl": 0,
            "n_timeout": 0, "tp_before_sl_share": 0.0, "trades_available": False,
        }
        hard = str(r.get("hard_status") or "")
        sub = classify_subreason(facts, hard, str(r.get("family") or ""), oi_micro)
        rows.append({
            "uc_key": str(r.get("uc_key") or ""), "symbol": str(r.get("symbol") or ""),
            "timeframe": str(r.get("timeframe") or ""), "family": str(r.get("family") or ""),
            "regime_bucket": str(r.get("regime_bucket") or ""), "hard_status": hard,
            "reject_subreason": sub, "recyclable": sub in _RECYCLABLE,
            "trades_available": facts["trades_available"], **{k: facts[k] for k in (
                "n_trades", "avg_net_pct", "best_net_pct", "worst_net_pct", "avg_mfe_pct",
                "avg_mae_pct", "avg_capture_ratio", "late_entry_rate", "n_tp", "n_sl",
                "n_timeout", "tp_before_sl_share")},
        })
    return rows


def summarize_characterization(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Deliverable tables: counts by sub-reason, family, timeframe + recyclable totals."""
    def _tally(key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    recyclable = [r for r in rows if r["recyclable"]]
    by_sub_tf: dict[str, dict[str, int]] = {}
    for r in rows:
        by_sub_tf.setdefault(r["reject_subreason"], {})
        tf = r["timeframe"]
        by_sub_tf[r["reject_subreason"]][tf] = by_sub_tf[r["reject_subreason"]].get(tf, 0) + 1
    return {
        "total_rejects": len(rows),
        "recyclable_total": len(recyclable),
        "confirmed_bad_total": sum(1 for r in rows if r["reject_subreason"] == "confirmed_bad"),
        "by_subreason": _tally("reject_subreason"),
        "by_family": _tally("family"),
        "recyclable_by_family": _tally_subset(recyclable, "family"),
        "by_subreason_timeframe": by_sub_tf,
        "trades_available_count": sum(1 for r in rows if r["trades_available"]),
    }


def _tally_subset(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
