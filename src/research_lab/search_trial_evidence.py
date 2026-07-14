"""Complete, immutable accounting for one bounded strategy search."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.research_lab.experiment import ExperimentSpec, RunResult
from src.research_lab.research_envelope import research_code_identity
from src.research_lab.strategy_registry import get_strategy

SCHEMA = "SearchTrialEvidence.v1"


def _sha256(payload: Any) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_search_trial_evidence(
    spec: ExperimentSpec,
    results: list[RunResult],
    runtime_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    planned: list[dict[str, Any]] = []
    for symbol in spec.symbols:
        for family in spec.families:
            defaults = dict(get_strategy(family).parameter_defaults)
            for params in spec.parameter_grid.get(family, []):
                planned.append(
                    {
                        "symbol": symbol,
                        "family": family,
                        "params": {**defaults, **dict(params or {})},
                    }
                )
    result_rows = [
        {
            "run_id": result.run_id,
            "symbol": result.symbol,
            "family": result.family,
            "params": result.params,
            "status": (
                "data_gate"
                if result.decision.startswith("NEEDS_") and not result.params
                else "evaluated"
            ),
            "decision": result.decision,
            "validation_status": result.validation_status,
            "data_fingerprint": result.metrics.get("data_fingerprint") or "",
        }
        for result in results
    ]
    evaluated_keys = {
        _sha256({"symbol": row["symbol"], "family": row["family"], "params": row["params"]})
        for row in result_rows
        if row["status"] == "evaluated"
    }
    unresolved = [
        {**row, "status": "not_evaluated", "reason": "budget_or_data_gate"}
        for row in planned
        if _sha256(row) not in evaluated_keys
    ]
    search_space = dict((spec.plan_meta or {}).get("search_space") or {})
    family_definition = {
        "symbols": list(spec.symbols),
        "families": list(spec.families),
        "timeframe": spec.timeframe,
        "filters": spec.filters,
        "parameter_grid": spec.parameter_grid,
        "fees_bps": spec.fees_bps,
        "slippage_bps": spec.slippage_bps,
        "split_ratio": spec.split_ratio,
        "min_trades": spec.min_trades,
    }
    payload = {
        "schema": SCHEMA,
        "experiment_id": spec.experiment_id,
        "multiple_testing_family_hash": _sha256(family_definition),
        "family_definition": family_definition,
        "search_space": {
            **search_space,
            "planned_in_spec": len(planned),
            "evaluated": sum(row["status"] == "evaluated" for row in result_rows),
            "data_gates": sum(row["status"] == "data_gate" for row in result_rows),
            "not_evaluated": len(unresolved),
        },
        "runtime": dict(runtime_meta or {}),
        "code_identity": research_code_identity(),
        "trials": [*result_rows, *unresolved],
        "paper_only": True,
        "execution_allowed": False,
    }
    payload["search_trial_evidence_id"] = f"ste_{_sha256(payload)}"
    return payload


def write_search_trial_evidence(run_dir: Path, evidence: dict[str, Any]) -> Path:
    path = Path(run_dir) / "search_trial_evidence.json"
    payload = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != payload:
        raise ValueError("immutable search trial evidence collision")
    if not path.exists():
        temporary = path.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
    return path
