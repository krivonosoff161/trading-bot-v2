"""Adaptive policy selector for the safe main-paper runtime.

The policy layer is intentionally narrow: it may choose a bounded execution
profile from deterministic facts, but it cannot set prices, verdicts, paper
readiness, or execution permissions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = "MainAdaptivePolicy.v1"
SUMMARY_SCHEMA = "main_adaptive_policy.v1"

FORBIDDEN_FIELDS = {
    "entry",
    "entry_zone",
    "stop",
    "stop_loss",
    "take_profit",
    "take_profit_plan",
    "tp",
    "tp1",
    "price",
    "paper_ready",
    "paper_forward_ready",
    "validator_verdict",
    "execution_allowed",
    "order",
    "leverage",
}


@dataclass(frozen=True)
class MainAdaptivePolicy:
    policy_id: str
    source_signal_id: str
    okx_inst_id: str
    timeframe: str
    side: str
    setup_family: str
    regime_hint: str
    execution_profile: str
    entry_profile: str
    exit_profile: str
    stop_profile: str
    max_hold_profile: str
    confidence: float
    reason_codes: list[str] = field(default_factory=list)
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError(f"schema must be {SCHEMA}")
        if not self.paper_only:
            raise ValueError("adaptive policy must be paper_only")
        if self.execution_allowed:
            raise ValueError("adaptive policy must never allow execution")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        if not self.policy_id or not self.source_signal_id:
            raise ValueError("policy_id and source_signal_id are required")
        if not self.execution_profile or not self.exit_profile:
            raise ValueError("execution_profile and exit_profile are required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _policy_id(parts: list[str]) -> str:
    raw = "|".join(parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"main_policy_{digest}"


def _profile_for(row: dict[str, Any]) -> tuple[str, str, str, str, str, str, float, list[str]]:
    family = str(row.get("setup_family") or "").lower()
    timeframe = str(row.get("timeframe") or "").lower()
    exit_mode = str(row.get("exit_mode") or "").lower()
    risk_pct = float(row.get("risk_pct") or 0.0)
    reasons = [f"family:{family or 'unknown'}", f"tf:{timeframe or 'unknown'}"]

    if family == "early_tp_tactical":
        profile = (
            "fast_tactical_watch",
            "limit_or_pullback",
            "early_tp_partial_be",
            "tight_atr_cap",
            "short",
            "impulse_exhaustion_scalp",
            0.72,
        )
        reasons.append("forward_lead:early_tp_tactical")
    elif family in {"mean_reversion_fade", "reversal_fade"}:
        profile = (
            "mean_reversion_watch",
            "reclaim_or_retest",
            "partial_be_or_fast_tp",
            "structure_stop",
            "medium",
            "stretched_reversion",
            0.64,
        )
        reasons.append("family_prefers_reversion")
    elif family == "liquidity_sweep_reclaim":
        profile = (
            "sweep_reclaim_watch",
            "reclaim_confirmation",
            "fast_tp_or_abort",
            "sweep_extreme_stop",
            "short",
            "liquidity_sweep",
            0.48,
        )
        reasons.append("forward_negative_family:cautious")
    elif family in {"continuation", "pullback_continuation", "momentum_breakout"}:
        profile = (
            "cautious_followthrough_watch",
            "pullback_required",
            "partial_be",
            "wide_move_cap",
            "medium",
            "trend_followthrough",
            0.46,
        )
        reasons.append("continuation_forward_risk")
    else:
        profile = (
            "generic_paper_watch",
            "contract_entry_only",
            "contract_exit_only",
            "contract_stop_only",
            "contract_hold",
            "unknown",
            0.40,
        )
        reasons.append("unknown_family")

    if timeframe == "15m":
        reasons.append("short_tf")
    elif timeframe in {"4h", "1d"}:
        reasons.append("slow_tf")
    if risk_pct > 8:
        reasons.append("risk_too_wide")
        profile = (*profile[:6], max(0.20, profile[6] - 0.18))
    elif 0 < risk_pct <= 3:
        reasons.append("compact_risk")
        profile = (*profile[:6], min(1.0, profile[6] + 0.05))
    if exit_mode:
        reasons.append(f"contract_exit:{exit_mode}")

    return profile[0], profile[1], profile[2], profile[3], profile[4], profile[5], profile[6], reasons


def build_policy(row: dict[str, Any]) -> MainAdaptivePolicy:
    """Build a deterministic adaptive policy for a queue row."""
    signal_id = str(row.get("source_signal_id") or row.get("runtime_id") or "")
    okx_inst_id = str(row.get("okx_inst_id") or row.get("pair") or "")
    timeframe = str(row.get("timeframe") or "")
    side = str(row.get("side") or "")
    family = str(row.get("setup_family") or "")
    execution, entry, exit_profile, stop, hold, regime, confidence, reasons = _profile_for(row)
    return MainAdaptivePolicy(
        policy_id=_policy_id([signal_id, okx_inst_id, timeframe, side, family, execution, exit_profile]),
        source_signal_id=signal_id,
        okx_inst_id=okx_inst_id,
        timeframe=timeframe,
        side=side,
        setup_family=family,
        regime_hint=regime,
        execution_profile=execution,
        entry_profile=entry,
        exit_profile=exit_profile,
        stop_profile=stop,
        max_hold_profile=hold,
        confidence=round(float(confidence), 4),
        reason_codes=reasons,
    )


def validate_advisor_policy(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate future LLM policy advice.

    The LLM may suggest bounded profile labels only. Any numeric trading level,
    execution permission, order field, or validator field is rejected.
    """
    problems: list[str] = []
    keys = {str(key) for key in payload}
    forbidden = sorted(keys & FORBIDDEN_FIELDS)
    if forbidden:
        problems.append(f"forbidden_fields:{','.join(forbidden)}")
    if payload.get("execution_allowed") is True:
        problems.append("execution_allowed_true")
    if payload.get("paper_only") is False:
        problems.append("paper_only_false")
    for key in ("execution_profile", "entry_profile", "exit_profile", "stop_profile"):
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            problems.append(f"{key}_must_be_string")
    return not problems, problems


def write_policy_artifacts(private_root: Path, policies: list[MainAdaptivePolicy]) -> dict[str, Any]:
    private_root = Path(private_root)
    out_jsonl = private_root / "state" / "derived" / "main_adaptive_policy.jsonl"
    out_snapshot = private_root / "state" / "derived" / "main_adaptive_policy.json"
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for policy in policies:
            fh.write(json.dumps(policy.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    by_profile: dict[str, int] = {}
    by_family: dict[str, int] = {}
    for policy in policies:
        by_profile[policy.execution_profile] = by_profile.get(policy.execution_profile, 0) + 1
        by_family[policy.setup_family] = by_family.get(policy.setup_family, 0) + 1

    summary = {
        "schema": SUMMARY_SCHEMA,
        "row_schema": SCHEMA,
        "policies": len(policies),
        "paper_only": True,
        "execution_allowed": False,
        "jsonl_path": str(out_jsonl),
        "snapshot_path": str(out_snapshot),
        "by_execution_profile": by_profile,
        "by_family": by_family,
        "items": [policy.to_dict() for policy in policies[:200]],
    }
    out_snapshot.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary

