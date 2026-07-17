# -*- coding: utf-8 -*-
"""Derived setup lifecycle and positive/negative research grouping.

This module does not create a second source of truth. It rebuilds a read-only
view from canonical artifacts: farm_tasks.unique_candidates, hard-validation
requests/verdicts/setup cards, and the append-only paper_trades.jsonl journal.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.research_lab.farm_tasks_db import tasks_db_path
from src.research_lab.paper_journal import read_paper_outcomes
from src.research_lab.validation_handoff import validation_state

HARD_FAILED = {
    "HARD_REJECT", "FAILED_OVERFIT", "FAILED_COSTS", "FAILED_FRAGILITY",
    "FAILED_OOS", "FAILED_DATA_QUALITY", "REGIME_ONLY",
}

# Coarse tactical shelf derived ONLY from unique_candidates columns (the per-candidate
# fast view over the whole DB; the deep MFE/MAE drill lives in trade_path_diagnostics).
# CLOSED vocabulary, research-only — NONE of these ever equals PAPER_FORWARD_READY or
# grants paper/trade access. NOT_TACTICAL / '' means "no tactical claim".
TACTICAL_STATUSES = (
    "NEEDS_OI_CONTEXT", "NEEDS_MICRO_DATA", "TACTICAL_REGIME_ONLY", "TACTICAL_COST_SENSITIVE",
    "TACTICAL_THIN_WINDOW", "TACTICAL_UNDERDATA", "REJECTED_CONFIRMED_BAD", "NOT_TACTICAL",
)
POWER_FLOOR = 10  # validator _check_splits has no power below n=10


def _oi_micro_families() -> dict[str, str]:
    """family -> 'oi'|'micro' for families that declare those data needs (reuse registry)."""
    from src.research_lab.strategy_registry import REGISTRY, get_strategy
    out: dict[str, str] = {}
    for fam in REGISTRY:
        req = set(get_strategy(fam).required_data)
        if "oi" in req:
            out[fam] = "oi"
        elif "microstructure" in req:
            out[fam] = "micro"
    return out


def derive_tactical_status(row: dict[str, Any], hard_status: str, oi_micro: dict[str, str]) -> str:
    """Coarse research-only tactical shelf from unique_candidates columns. Never promotable."""
    family = str(row.get("family") or "")
    decision = str(row.get("decision") or "")
    lite = str(row.get("validation_status") or "")
    n = int(row.get("n_trades") or 0)
    avg = float(row.get("avg_net_pct") or 0.0)
    regime = str(row.get("regime_bucket") or "")
    rejected = decision == "REJECT" or lite == "REJECT" or hard_status in HARD_FAILED
    if not rejected:
        return ""  # only characterize the rejected/failed population
    kind = oi_micro.get(family)
    if kind == "oi":
        return "NEEDS_OI_CONTEXT"
    if kind == "micro":
        return "NEEDS_MICRO_DATA"
    if hard_status == "REGIME_ONLY" or (lite == "REGIME_SPECIFIC" and regime):
        return "TACTICAL_REGIME_ONLY"
    if hard_status == "FAILED_COSTS":
        return "TACTICAL_COST_SENSITIVE"
    if 1 <= n <= 2 and avg > 0:
        return "TACTICAL_THIN_WINDOW"
    if n == 0 or hard_status == "NEEDS_MORE_DATA" or (1 <= n <= 2 and avg <= 0):
        return "TACTICAL_UNDERDATA"
    if n >= POWER_FLOOR and avg <= 0:
        return "REJECTED_CONFIRMED_BAD"
    return "NOT_TACTICAL"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_unique_candidates(private_root: Path) -> list[dict[str, Any]]:
    path = tasks_db_path(private_root)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM unique_candidates ORDER BY updated_at DESC").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _request_contexts(private_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    from src.research_lab.validation_generation import (
        current_candidate_ids,
        read_current_validation_artifact,
    )

    by_candidate: dict[str, dict[str, Any]] = {}
    candidate_to_uc: dict[str, str] = {}
    active_ids = current_candidate_ids(private_root)
    if active_ids is not None:
        for cid in active_ids:
            data = read_current_validation_artifact(private_root, cid, "request") or {}
            if not data:
                continue
            by_candidate[cid] = data
            metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
            uc_key = str(metrics.get("uc_key") or "")
            if uc_key:
                candidate_to_uc[cid] = uc_key
        return by_candidate, candidate_to_uc
    req_dir = private_root / "hard_validation" / "requests"
    if not req_dir.exists():
        return by_candidate, candidate_to_uc
    for path in req_dir.glob("*.json"):
        data = _read_json(path)
        cid = str(data.get("candidate_id") or path.stem)
        if not cid:
            continue
        by_candidate[cid] = data
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        uc_key = str(metrics.get("uc_key") or "")
        if uc_key:
            candidate_to_uc[cid] = uc_key
    return by_candidate, candidate_to_uc


def _verdicts(private_root: Path) -> dict[str, str]:
    from src.research_lab.validation_generation import (
        current_candidate_ids,
        read_current_validation_artifact,
    )

    out: dict[str, str] = {}
    active_ids = current_candidate_ids(private_root)
    if active_ids is not None:
        for cid in active_ids:
            data = read_current_validation_artifact(private_root, cid, "verdict") or {}
            if data:
                out[cid] = str(data.get("hard_status") or "")
        return out
    vdir = private_root / "hard_validation" / "verdicts"
    if not vdir.exists():
        return out
    for path in vdir.glob("*.json"):
        data = _read_json(path)
        cid = str(data.get("candidate_id") or path.stem)
        if cid:
            out[cid] = str(data.get("hard_status") or "")
    return out


def _cards(private_root: Path) -> dict[str, dict[str, Any]]:
    from src.research_lab.validation_generation import read_current_setup_card

    out: dict[str, dict[str, Any]] = {}
    cards_dir = private_root / "setup_library" / "cards"
    if not cards_dir.exists():
        return out
    for path in cards_dir.glob("*.json"):
        data = read_current_setup_card(private_root, path) or {}
        cid = str(data.get("candidate_id") or "")
        if cid:
            out[cid] = data
    return out


def _paper_by_uc(private_root: Path, candidate_to_uc: dict[str, str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in read_paper_outcomes(private_root):
        cid = str(row.get("candidate_id") or "")
        uc_key = candidate_to_uc.get(cid)
        if not uc_key:
            continue
        bucket = out.setdefault(uc_key, {"count": 0, "net_sum": 0.0, "wins": 0, "losses": 0, "latest_state": ""})
        net = float(row.get("net_pct") or 0.0)
        bucket["count"] += 1
        bucket["net_sum"] += net
        bucket["wins"] += int(net > 0)
        bucket["losses"] += int(net < 0)
        bucket["latest_state"] = str(row.get("state") or bucket["latest_state"])
    return out


def _candidate_validation_id(row: dict[str, Any], uc_to_validation: dict[str, str]) -> str:
    uc_key = str(row.get("uc_key") or "")
    return uc_to_validation.get(uc_key, str(row.get("candidate_id") or ""))


def _derive_state(row: dict[str, Any], hard_status: str, card: dict[str, Any] | None,
                  paper: dict[str, Any]) -> str:
    if int(paper.get("count") or 0) > 0:
        net = float(paper.get("net_sum") or 0.0)
        if net > 0:
            return "PAPER_POSITIVE_OBSERVED"
        if net < 0:
            return "PAPER_NEGATIVE_OBSERVED"
        return "PAPER_MIXED_OR_FLAT"
    if card and card.get("paper_forward_ready"):
        return "PAPER_PLAN_READY"
    if hard_status == "PAPER_FORWARD_READY":
        return "HARD_PASSED"
    if hard_status == "NEEDS_MORE_DATA":
        return "NEEDS_MORE_DATA"
    if hard_status in HARD_FAILED:
        return "HARD_FAILED"
    status = str(row.get("validation_status") or "")
    decision = str(row.get("decision") or "")
    if status in {"FORWARD_PAPER", "REGIME_SPECIFIC"}:
        return "HARD_ELIGIBLE"
    if decision == "REJECT":
        return "LITE_REJECTED"
    return "LITE_OBSERVE" if decision else "DISCOVERED"


def derive_setup_lifecycle(private_root: Path) -> list[dict[str, Any]]:
    private_root = Path(private_root)
    reqs, candidate_to_uc = _request_contexts(private_root)
    uc_to_validation = {uc: cid for cid, uc in candidate_to_uc.items()}
    verdicts = _verdicts(private_root)
    cards = _cards(private_root)
    paper = _paper_by_uc(private_root, candidate_to_uc)
    oi_micro = _oi_micro_families()
    rows: list[dict[str, Any]] = []
    for row in _load_unique_candidates(private_root):
        validation_id = _candidate_validation_id(row, uc_to_validation)
        card = cards.get(validation_id)
        hard_status = verdicts.get(validation_id) or str(row.get("hard_status") or "")
        exported = validation_id in reqs
        p = paper.get(str(row.get("uc_key") or ""), {})
        state = _derive_state(row, hard_status, card, p)
        tactical = derive_tactical_status(row, hard_status, oi_micro)
        rows.append({
            "uc_key": str(row.get("uc_key") or ""),
            "validation_candidate_id": validation_id,
            "setup_id": str((card or {}).get("setup_id") or ""),
            "source_candidate_id": str(row.get("candidate_id") or ""),
            "symbol": str(row.get("symbol") or ""),
            "timeframe": str(row.get("timeframe") or ""),
            "family": str(row.get("family") or ""),
            "params_hash": str(row.get("params_hash") or ""),
            "data_fingerprint": str(row.get("data_fingerprint") or ""),
            "lite_status": str(row.get("validation_status") or ""),
            "hard_status": hard_status,
            "validation_state": validation_state(str(row.get("validation_status") or ""), hard_status, exported),
            "setup_card_exists": bool(card),
            "paper_forward_ready": bool((card or {}).get("paper_forward_ready")),
            "paper_outcome_count": int(p.get("count") or 0),
            "paper_net_sum": round(float(p.get("net_sum") or 0.0), 6),
            "paper_avg_net": round(float(p.get("net_sum") or 0.0) / max(1, int(p.get("count") or 0)), 6),
            "paper_win_count": int(p.get("wins") or 0),
            "paper_loss_count": int(p.get("losses") or 0),
            "latest_paper_state": str(p.get("latest_state") or ""),
            "derived_lifecycle_state": state,
            "n_trades": int(row.get("n_trades") or 0),
            "avg_net_pct": round(float(row.get("avg_net_pct") or 0.0), 6),
            "regime_bucket": str(row.get("regime_bucket") or ""),
            "tactical_status": tactical,
        })
    return rows


def summarize_setup_lifecycle(private_root: Path) -> dict[str, Any]:
    rows = derive_setup_lifecycle(private_root)
    by_state: dict[str, int] = {}
    by_hard: dict[str, int] = {}
    by_tactical: dict[str, int] = {}
    for row in rows:
        by_state[row["derived_lifecycle_state"]] = by_state.get(row["derived_lifecycle_state"], 0) + 1
        hard = row["hard_status"]
        if hard:
            by_hard[hard] = by_hard.get(hard, 0) + 1
        tac = row.get("tactical_status") or ""
        if tac:
            by_tactical[tac] = by_tactical.get(tac, 0) + 1
    return {
        "available": True,
        "total": len(rows),
        "by_state": by_state,
        "hard_status": by_hard,
        "by_tactical": dict(sorted(by_tactical.items(), key=lambda kv: -kv[1])),
        "positive_setups": sum(1 for r in rows if r["derived_lifecycle_state"] == "PAPER_POSITIVE_OBSERVED"),
        "negative_setups": sum(1 for r in rows if r["derived_lifecycle_state"] == "PAPER_NEGATIVE_OBSERVED"),
        "mixed_or_flat": sum(1 for r in rows if r["derived_lifecycle_state"] == "PAPER_MIXED_OR_FLAT"),
        "no_paper_sample": sum(1 for r in rows if int(r["paper_outcome_count"]) == 0),
    }


def write_setup_lifecycle_snapshot(private_root: Path) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "setup_lifecycle.json"
    payload = {"summary": summarize_setup_lifecycle(private_root), "rows": derive_setup_lifecycle(private_root)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
