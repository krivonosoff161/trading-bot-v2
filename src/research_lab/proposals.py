# -*- coding: utf-8 -*-
"""Generate private next-experiment specs from candidate registry entries.

This module is deterministic and conservative. It does not call an LLM and it
does not claim that a candidate is profitable. It only turns existing research
labels into small follow-up experiment specs that a worker can run.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from src.research_lab.candidate_registry import load_entries, registry_path
from src.research_lab.paths import resolve_private_root

SCHEMA = "strategy_lab_proposal.v1"
DEFAULT_DATA_GLOB = (
    "scripts/analysis/research/_okxhist/ai_scanner_feasibility/{symbol}_*.json"
)
STATUS_RANK = {"FORWARD_PAPER": 3, "REGIME_SPECIFIC": 2, "OBSERVE": 1}
MAX_PARAMS_PER_SPEC = 7


def proposal_root(private_root: Path) -> Path:
    return private_root / "proposals"


def proposal_log_path(private_root: Path) -> Path:
    return proposal_root(private_root) / "proposal_registry.jsonl"


def spec_dir(private_root: Path) -> Path:
    return proposal_root(private_root) / "specs"


def load_proposal_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def generate_proposals(
    entries: list[dict[str, Any]],
    *,
    max_proposals: int = 8,
    data_glob: str = DEFAULT_DATA_GLOB,
) -> list[dict[str, Any]]:
    candidates = sorted(entries, key=_entry_sort_key)
    proposals = []
    seen_keys: set[str] = set()
    for entry in candidates:
        status = str(entry.get("validation_status") or "")
        if status not in STATUS_RANK:
            continue
        proposal = _proposal_for_entry(entry, data_glob=data_glob)
        if proposal is None:
            continue
        key = _proposal_key(proposal)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        proposals.append(proposal)
        if len(proposals) >= max(1, max_proposals):
            break
    return proposals


def write_proposals(
    private_root: Path,
    proposals: list[dict[str, Any]],
    *,
    allow_public_output: bool = False,
) -> dict[str, Any]:
    private_root = resolve_private_root(
        private_root, allow_public_output=allow_public_output
    )
    out_dir = spec_dir(private_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = proposal_log_path(private_root)
    existing = {
        str(row.get("proposal_key") or _proposal_key(row))
        for row in load_proposal_log(log_path)
    }
    written = []
    for proposal in proposals:
        key = _proposal_key(proposal)
        if key in existing:
            continue
        filename = f"{proposal['experiment_id']}.json"
        path = out_dir / filename
        path.write_text(
            json.dumps(proposal["spec"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        row = {
            "schema": SCHEMA,
            "proposal_id": proposal["proposal_id"],
            "experiment_id": proposal["experiment_id"],
            "source_candidate_id": proposal["source_candidate_id"],
            "source_status": proposal["source_status"],
            "reason": proposal["reason"],
            "spec_label": f"proposals/specs/{filename}",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "proposal_key": key,
        }
        _append_jsonl(log_path, row)
        existing.add(key)
        written.append(row)
    return {
        "generated": len(proposals),
        "written": len(written),
        "already_known": len(proposals) - len(written),
        "specs": written,
    }


def generate_and_write_from_registry(
    private_root: Path,
    *,
    max_proposals: int = 8,
    allow_public_output: bool = False,
) -> dict[str, Any]:
    private_root = resolve_private_root(
        private_root, allow_public_output=allow_public_output
    )
    entries = load_entries(registry_path(private_root))
    proposals = generate_proposals(entries, max_proposals=max_proposals)
    result = write_proposals(
        private_root, proposals, allow_public_output=allow_public_output
    )
    result["registry_entries"] = len(entries)
    return result


def _proposal_for_entry(
    entry: dict[str, Any], *, data_glob: str
) -> dict[str, Any] | None:
    status = str(entry.get("validation_status") or "")
    symbol = str(entry.get("symbol") or "")
    strategy_id = str(entry.get("strategy_id") or "")
    params = entry.get("params")
    if not symbol or not strategy_id or not isinstance(params, dict):
        return None

    if status == "REGIME_SPECIFIC":
        filters = _filters_from_entry(entry)
        if not filters:
            return None
        variants = [dict(params)]
        reason = "regime_specific_rerun"
    else:
        filters = {}
        variants = _parameter_neighborhood(params)
        reason = "parameter_neighborhood_sweep"

    source_id = str(entry.get("candidate_id") or "")
    proposal_id = _short_hash(
        {
            "source": source_id,
            "status": status,
            "variants": variants,
            "filters": filters,
        }
    )
    experiment_id = f"auto_{reason}_{strategy_id}_{symbol}_{proposal_id}"
    spec = {
        "experiment_id": experiment_id,
        "data_glob": data_glob,
        "symbols": [symbol],
        "families": [strategy_id],
        "fees_bps": _fee_guess(symbol),
        "slippage_bps": _slippage_guess(symbol),
        "min_trades": _min_trades_guess(status),
        "split_ratio": 0.7,
        "max_runs": min(MAX_PARAMS_PER_SPEC, len(variants)),
        "parameter_grid": {strategy_id: variants[:MAX_PARAMS_PER_SPEC]},
    }
    if filters:
        spec["filters"] = filters
    return {
        "proposal_id": proposal_id,
        "experiment_id": experiment_id,
        "source_candidate_id": source_id,
        "source_status": status,
        "reason": reason,
        "spec": spec,
    }


def _parameter_neighborhood(params: dict[str, Any]) -> list[dict[str, Any]]:
    variants = [dict(params)]
    for key in sorted(params):
        value = params[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        for scaled in (_scaled_value(key, value, 0.8), _scaled_value(key, value, 1.2)):
            candidate = dict(params)
            candidate[key] = scaled
            if candidate not in variants:
                variants.append(candidate)
            if len(variants) >= MAX_PARAMS_PER_SPEC:
                return variants
    return variants


def _scaled_value(key: str, value: int | float, mult: float) -> int | float:
    raw = float(value) * mult
    if key in {
        "lookback",
        "period",
        "ma",
        "trend_ma",
        "pullback_ma",
        "below_bars",
        "hold_bars",
        "retest_window",
    }:
        return max(1, int(round(raw)))
    return round(max(0.01, raw), 4)


def _filters_from_entry(entry: dict[str, Any]) -> dict[str, list[str]]:
    bucket = ""
    for reason in entry.get("validation_reasons") or []:
        text = str(reason)
        if text.startswith("strong_regime_bucket:"):
            bucket = text.split(":", 1)[1]
            break
    if not bucket:
        summary = entry.get("regime_summary")
        if isinstance(summary, dict):
            bucket = str(summary.get("dominant_bucket") or "")
    parts = [p for p in bucket.split("|") if p and p != "unknown"]
    keys = ["volatility", "trend", "volume"]
    return {key: [value] for key, value in zip(keys, parts) if value}


def _entry_sort_key(entry: dict[str, Any]) -> tuple[int, float, float, int, str]:
    metrics = (
        raw_metrics
        if isinstance(raw_metrics := entry.get("metrics_summary"), dict)
        else {}
    )
    return (
        -STATUS_RANK.get(str(entry.get("validation_status") or ""), 0),
        -_float(metrics.get("test_avg_net_pct")),
        -_float(metrics.get("avg_net_pct")),
        -int(metrics.get("n_trades") or 0),
        str(entry.get("candidate_id") or ""),
    )


def _proposal_key(proposal: dict[str, Any]) -> str:
    payload = proposal.get("spec") if "spec" in proposal else proposal
    return _short_hash(payload)


def _short_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _fee_guess(symbol: str) -> float:
    return (
        9.0
        if any(tag in symbol for tag in ("PEPE", "PENGU", "PUMP", "DOGE", "WLD"))
        else 7.0
    )


def _slippage_guess(symbol: str) -> float:
    return (
        6.0
        if any(tag in symbol for tag in ("PEPE", "PENGU", "PUMP", "DOGE", "WLD"))
        else 3.0
    )


def _min_trades_guess(status: str) -> int:
    return 8 if status == "REGIME_SPECIFIC" else 12


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
