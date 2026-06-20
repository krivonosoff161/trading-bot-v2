# -*- coding: utf-8 -*-
"""Parameter authority layered over the strategy registry.

The strategy registry remains the source of truth for strategy ids, default
parameters and required data. This module adds executable validation: allowed
keys, scalar types/ranges, and the paper/LLM risk-reward gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.config_io import CONFIG_DIR, load_yaml_mapping
from src.research_lab.strategy_registry import REGISTRY, get_strategy

DEFAULT_PATH = CONFIG_DIR / "param_schemas.yaml"
META_KEYS = {"direction", "side"}
META_ENUMS = {"direction": {"long", "short", "both"}, "side": {"long", "short", "both"}}


@dataclass(frozen=True)
class ParamValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def load_param_policy(path: str | Path = DEFAULT_PATH) -> dict[str, Any]:
    return load_yaml_mapping(path, what="param_schemas")


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _is_intish(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return int(value) == float(value)
    except (TypeError, ValueError):
        return False


def _range_for_key(key: str, default: Any, policy: dict[str, Any]) -> dict[str, Any]:
    explicit = (policy.get("ranges") or {}).get(key)
    if isinstance(explicit, dict):
        return explicit
    if isinstance(default, bool):
        return {"type": "bool"}
    fallback = policy.get("fallback_ranges") or {}
    if isinstance(default, int) and not isinstance(default, bool):
        cfg = fallback.get("int") or {}
        max_v = max(int(cfg.get("min_max", 10)), int(default) * int(cfg.get("max_mult", 4)))
        return {"type": "int", "min": int(cfg.get("min", 0)), "max": max_v}
    cfg = fallback.get("number") or {}
    base = abs(float(default or 0))
    max_v = max(float(cfg.get("min_max", 1.0)), base * float(cfg.get("max_mult", 4)))
    return {"type": "number", "min": float(cfg.get("min", 0.0)), "max": max_v}


def _validate_one(key: str, value: Any, default: Any, policy: dict[str, Any]) -> list[str]:
    spec = _range_for_key(key, default, policy)
    typ = str(spec.get("type") or "")
    if typ == "bool":
        return [] if isinstance(value, bool) else [f"{key}:expected_bool"]
    if typ == "int":
        if not _is_intish(value):
            return [f"{key}:expected_int"]
        val = int(value)
    else:
        num = _num(value)
        if num is None:
            return [f"{key}:expected_number"]
        val = num
    lo = spec.get("min")
    hi = spec.get("max")
    errors: list[str] = []
    if lo is not None and val < float(lo):
        errors.append(f"{key}:below_min:{lo}")
    if hi is not None and val > float(hi):
        errors.append(f"{key}:above_max:{hi}")
    return errors


def validate_params(
    strategy_id: str,
    params: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    require_executable: bool = False,
) -> ParamValidationResult:
    """Validate one strategy parameter object.

    Unknown keys are rejected; known keys are checked against registry-derived
    type/range rules. ``require_executable`` adds the paper/LLM gate:
    hold/stop/take must exist and take_pct must be at least 2R by default.
    """
    policy = policy or load_param_policy()
    if strategy_id not in REGISTRY:
        return ParamValidationResult(False, [f"unknown_strategy:{strategy_id}"])
    if not isinstance(params, dict) or not params:
        return ParamValidationResult(False, ["params_missing"])
    defaults = dict(get_strategy(strategy_id).parameter_defaults)
    allowed = set(defaults) | META_KEYS
    errors: list[str] = []
    for key in sorted(set(params) - allowed):
        errors.append(f"{key}:unknown_param")
    for key, value in params.items():
        if key in META_ENUMS:
            if str(value).lower() not in META_ENUMS[key]:
                errors.append(f"{key}:invalid_value")
        elif key in defaults:
            errors.extend(_validate_one(key, value, defaults[key], policy))
    if require_executable:
        errors.extend(_executable_errors(params, policy))
    return ParamValidationResult(not errors, sorted(set(errors)))


def validate_parameter_grid(
    strategy_id: str,
    variants: list[dict[str, Any]],
    *,
    policy: dict[str, Any] | None = None,
    require_executable: bool = False,
) -> ParamValidationResult:
    errors: list[str] = []
    if not isinstance(variants, list) or not variants:
        return ParamValidationResult(False, ["parameter_grid_empty"])
    for idx, params in enumerate(variants):
        result = validate_params(
            strategy_id, params, policy=policy, require_executable=require_executable
        )
        errors.extend(f"variant_{idx}:{err}" for err in result.errors)
    return ParamValidationResult(not errors, sorted(set(errors)))


def _executable_errors(params: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    cfg = policy.get("executable") or {}
    required = list(cfg.get("required_keys") or ("hold_bars", "stop_pct", "take_pct"))
    errors: list[str] = []
    for key in required:
        if key not in params:
            errors.append(f"{key}:required_executable")
    if errors:
        return errors
    stop = _num(params.get("stop_pct"))
    take = _num(params.get("take_pct"))
    hold = _num(params.get("hold_bars"))
    if stop is None or stop <= 0:
        errors.append("stop_pct:invalid_executable")
    if take is None or take <= 0:
        errors.append("take_pct:invalid_executable")
    if hold is None or hold <= 0 or not _is_intish(params.get("hold_bars")):
        errors.append("hold_bars:invalid_executable")
    min_rr = float(cfg.get("min_reward_risk", 2.0))
    if stop and take and take < stop * min_rr:
        errors.append(f"take_pct:reward_risk_below_{min_rr:g}r")
    return errors


def executable_params_ready(strategy_id: str, params: dict[str, Any]) -> bool:
    return validate_params(strategy_id, params, require_executable=True).ok


def validate_horizon(
    timeframe: str, params: dict[str, Any], *, policy: dict[str, Any] | None = None
) -> list[str]:
    """Reject a hold_bars outside the per-timeframe holding-horizon band (wrong_horizon).

    The band lives in the ``horizon`` section of the param policy (1 bar = the timeframe),
    keeping a setup's hold sane for its scale. No band for a timeframe => no constraint.
    """
    policy = policy or load_param_policy()
    band = (policy.get("horizon") or {}).get(str(timeframe))
    if not isinstance(band, dict):
        return []
    hold = _num(params.get("hold_bars"))
    if hold is None:
        return []
    errors: list[str] = []
    lo, hi = band.get("min_hold_bars"), band.get("max_hold_bars")
    if lo is not None and hold < float(lo):
        errors.append(f"hold_bars:horizon_below_{lo}_for_{timeframe}")
    if hi is not None and hold > float(hi):
        errors.append(f"hold_bars:horizon_above_{hi}_for_{timeframe}")
    return errors


def executable_exit_params(
    strategy_id: str,
    params: dict[str, Any] | None = None,
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return params with executable exits filled/adjusted from registry defaults.

    This is for internally generated farm sweeps only. External proposals are still
    rejected when they violate the schema; the farm may normalize registry defaults
    so old research hypotheses can be recalculated with a paper-executable 1:2 RR.
    """
    policy = policy or load_param_policy()
    out = dict(get_strategy(strategy_id).parameter_defaults)
    out.update(params or {})
    cfg = policy.get("executable") or {}
    min_rr = float(cfg.get("min_reward_risk", 2.0))
    stop = _num(out.get("stop_pct"))
    take = _num(out.get("take_pct"))
    if stop is not None and stop > 0:
        target_take = stop * min_rr
        if take is None or take < target_take:
            out["take_pct"] = round(target_take, 6)
    if _num(out.get("hold_bars")) is None or float(out.get("hold_bars") or 0) <= 0:
        out["hold_bars"] = int(get_strategy(strategy_id).parameter_defaults.get("hold_bars") or 5)
    return out
