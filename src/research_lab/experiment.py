# -*- coding: utf-8 -*-
"""Small deterministic strategy-discovery runner.

The public code defines how experiments are evaluated. The private research
repository stores the actual result tables, candidate scorecards, and graph
exports. Output writers live in src/research_lab/outputs.py.
"""

from __future__ import annotations

import bisect
import glob
import hashlib
import itertools
import json
import time
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.research_lab.entry_timing import event_anchored_timing
from src.research_lab.candle_identity import candle_evidence_fingerprint
from src.research_lab.regime import regime_at, regime_breakdown, regime_matches
from src.research_lab.strategy_registry import get_strategy
from src.research_lab.validator import validate_candidate
from src.research_lab import gpu_kernels
from src.research_lab.gpu_runtime import (
    GPU,
    GPU_SUPPORTED_FAMILIES,
    auto_gpu_worthwhile,
    array_module,
    resolve_backend,
)
from src.research_lab.simulator_contract import (
    build_cost_ledger,
    build_legacy_combined_cost_ledger,
    build_trade_quantity_ledger,
    legacy_fixture_manifest,
    profit_factor_state,
    reconcile_partial_fills,
)

# Descriptor of the exit mode the GPU/batched simulator faithfully reproduces.
GPU_SUPPORTED_SIMULATION_MODES = ("fixed_sl_tp_hold_long_short",)
CPU_DYNAMIC_EXIT_MODES = (
    "early_tp",
    "trailing",
    "trailing_tight",
    "break_even",
    "time_decay",
    "partial_tp",
    "hold_long",
)

COST_STRESS_MULT = 1.5  # validator stress: costs scaled by this factor

# A family's required_data token -> (candle field that proves it's present, NEEDS status).
# When a required field is absent on every bar, the family is data-starved: it is
# classified NEEDS_<...>_DATA instead of being presented as an active (and rejected) edge.
_REQUIRED_DATA_FIELD = {
    "oi": ("oi", "NEEDS_OI_DATA"),
    "funding": ("funding", "NEEDS_FLOW_DATA"),
    "microstructure": ("obi_top5", "NEEDS_MICRO_DATA"),
}


def missing_required_data(family: str, candles: list[dict[str, Any]]) -> str | None:
    """NEEDS_<...>_DATA status if the family needs a candle field that is absent, else None."""
    try:
        required = get_strategy(family).required_data
    except ValueError:
        return None
    for token in required:
        field_name, status = _REQUIRED_DATA_FIELD.get(token, (None, None))
        if field_name and not any(c.get(field_name) is not None for c in candles):
            return status
    return None


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    data_glob: str
    symbols: list[str]
    families: list[str]
    parameter_grid: dict[str, list[dict[str, Any]]]
    fees_bps: float = 7.0
    slippage_bps: float = 3.0
    min_trades: int = 20
    split_ratio: float = 0.7
    max_runs: int = 0
    filters: dict[str, list[str]] = field(default_factory=dict)
    regime_params: dict[str, Any] = field(default_factory=dict)
    event_context: dict[str, Any] = field(default_factory=dict)
    plan_meta: dict[str, Any] = field(default_factory=dict)
    timeframe: str = "1d"
    backend: str = "cpu"
    data_snapshot_id: str = ""
    data_evidence_hash: str = ""
    data_snapshot_bindings: list[dict[str, Any]] = field(default_factory=list)
    search_family_definition: dict[str, Any] = field(default_factory=dict)
    search_family_id: str = ""
    validation_progress: InitVar[Callable[[str], None] | None] = None

    def __post_init__(self, validation_progress: Callable[[str], None] | None) -> None:
        from src.research_lab.search_family_definition import (
            build_declared_grid_definition,
            validate_experiment_spec_binding,
            validate_search_family_definition,
        )

        if bool(self.search_family_definition) != bool(self.search_family_id):
            raise ValueError("search family definition and id must be supplied together")
        if self.search_family_definition:
            validate_search_family_definition(
                self.search_family_definition,
                expected_id=self.search_family_id,
                progress=validation_progress,
            )
            validate_experiment_spec_binding(self)
            return
        definition, definition_id = build_declared_grid_definition(self)
        object.__setattr__(self, "search_family_definition", definition)
        object.__setattr__(self, "search_family_id", definition_id)
        validate_experiment_spec_binding(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "data_glob": self.data_glob,
            "symbols": list(self.symbols),
            "families": list(self.families),
            "parameter_grid": dict(self.parameter_grid),
            "fees_bps": self.fees_bps,
            "slippage_bps": self.slippage_bps,
            "min_trades": self.min_trades,
            "split_ratio": self.split_ratio,
            "max_runs": self.max_runs,
            "filters": dict(self.filters),
            "regime_params": dict(self.regime_params),
            "event_context": dict(self.event_context),
            "plan_meta": dict(self.plan_meta),
            "timeframe": self.timeframe,
            "backend": self.backend,
            "data_snapshot_id": self.data_snapshot_id,
            "data_evidence_hash": self.data_evidence_hash,
            "data_snapshot_bindings": list(self.data_snapshot_bindings),
            "search_family_definition": dict(self.search_family_definition),
            "search_family_id": self.search_family_id,
        }

    def write_json(self, path: Path) -> Path:
        path = Path(path)
        payload = json.dumps(
            self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != payload:
                raise ValueError("immutable experiment spec collision")
            return path
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
        return path

    @classmethod
    def from_json(cls, path: Path) -> "ExperimentSpec":
        data = json.loads(path.read_text(encoding="utf-8"))
        has_definition = bool(
            data.get("search_family_definition") and data.get("search_family_id")
        )
        legacy_lineage_keys = {
            "raw_grids",
            "search_axes",
            "sampler_version",
            "sampler_seed_material",
            "parameter_search_contract",
            "tested_variant_hashes",
        }
        if legacy_lineage_keys.intersection(data):
            raise ValueError("unbound search-family lineage in unknown JSON extras")
        allowed_keys = {
            "experiment_id",
            "data_glob",
            "symbols",
            "families",
            "parameter_grid",
            "fees_bps",
            "slippage_bps",
            "min_trades",
            "split_ratio",
            "max_runs",
            "filters",
            "regime_params",
            "event_context",
            "plan_meta",
            "timeframe",
            "backend",
            "data_snapshot_id",
            "data_evidence_hash",
            "data_snapshot_bindings",
            "search_family_definition",
            "search_family_id",
        }
        unknown_keys = sorted(set(data) - allowed_keys)
        if unknown_keys:
            raise ValueError(
                "unknown ExperimentSpec JSON fields cannot be dropped: "
                + ", ".join(unknown_keys)
            )
        if not has_definition:
            raise ValueError(
                "legacy compiled ExperimentSpec is compiled_subspace_only; "
                "an explicit SearchFamilyDefinition.v2 is required"
            )
        return cls(
            experiment_id=str(data["experiment_id"]),
            data_glob=str(data["data_glob"]),
            symbols=[str(s) for s in data.get("symbols", [])],
            families=[str(f) for f in data.get("families", [])],
            parameter_grid={str(k): list(v) for k, v in data.get("parameter_grid", {}).items()},
            fees_bps=float(data.get("fees_bps", 7.0)),
            slippage_bps=float(data.get("slippage_bps", 3.0)),
            min_trades=int(data.get("min_trades", 20)),
            split_ratio=float(data.get("split_ratio", 0.7)),
            max_runs=int(data.get("max_runs", 0)),
            filters={str(k): [str(x) for x in v] for k, v in (data.get("filters") or {}).items()},
            regime_params=dict(data.get("regime_params") or {}),
            event_context=dict(data.get("event_context") or {}),
            plan_meta=dict(data.get("plan_meta") or {}),
            timeframe=str(data.get("timeframe") or "1d"),
            backend=str(data.get("backend") or "cpu"),
            data_snapshot_id=str(data.get("data_snapshot_id") or ""),
            data_evidence_hash=str(data.get("data_evidence_hash") or ""),
            data_snapshot_bindings=[
                dict(item) for item in data.get("data_snapshot_bindings", [])
            ],
            search_family_definition=dict(data.get("search_family_definition") or {}),
            search_family_id=str(data.get("search_family_id") or ""),
        )


@dataclass(frozen=True)
class RunResult:
    run_id: str
    symbol: str
    family: str
    params: dict[str, Any]
    metrics: dict[str, Any]
    decision: str
    reasons: list[str]
    trades: list[dict[str, Any]] = field(default_factory=list)
    validation_status: str = ""
    validation_reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    next_action: str = ""
    regime_summary: dict[str, Any] = field(default_factory=dict)


def _execution_error_result(
    spec: ExperimentSpec,
    *,
    symbol: str,
    family: str,
    params: dict[str, Any],
    data_label: str,
    data_fingerprint: str,
    data_snapshot_id: str,
    family_data_snapshot_id: str,
    family_data_evidence_hash: str,
    execution_identity: dict[str, Any],
    timeframe: str,
    stress_extra: float,
    error: Exception,
) -> RunResult:
    metrics = compute_metrics([], spec.split_ratio, spec.min_trades, stress_extra)
    metrics.update(
        {
            "data_file_label": data_label,
            "data_file_timeframe": timeframe,
            "data_source": "sqlite" if data_label.startswith("sqlite:") else "json",
            "data_fingerprint": data_fingerprint,
            "data_snapshot_id": data_snapshot_id,
            "data_evidence_hash": data_fingerprint,
            "family_data_snapshot_id": family_data_snapshot_id,
            "family_data_evidence_hash": family_data_evidence_hash,
            "execution_identity": dict(execution_identity),
            "execution_error_type": type(error).__name__,
        }
    )
    return RunResult(
        run_id=stable_run_id(
            symbol,
            family,
            params,
            experiment_id=spec.experiment_id,
            data_fingerprint=data_fingerprint,
            timeframe=timeframe,
            fees_bps=spec.fees_bps,
            slippage_bps=spec.slippage_bps,
            split_ratio=spec.split_ratio,
            filters=spec.filters,
        ),
        symbol=symbol,
        family=family,
        params=params,
        metrics=metrics,
        decision="ERROR",
        reasons=[f"execution_error:{type(error).__name__}"],
        trades=[],
        validation_status="ERROR",
        validation_reasons=["variant_execution_failed"],
        risk_flags=["execution_error"],
        next_action="inspect deterministic synthetic failure evidence before retry",
        regime_summary={},
    )


# Optional flow/microstructure fields carried through to the feature layer when the
# prepared file was enriched (e.g. by enrich_flow_data / the loop's funding enricher).
_OPTIONAL_CANDLE_FIELDS = ("funding", "oi", "index_px", "obi_top5", "spread_bps", "trade_delta_100")


def load_candles(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        confirm = str(row.get("confirm", "1")).strip().lower()
        if confirm in {"0", "false", "no"}:
            continue
        try:
            candle: dict[str, Any] = {
                "ts": int(row["ts"]),
                "date": str(row.get("date") or ""),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "vol": float(row.get("vol") or 0.0),
            }
        except (KeyError, TypeError, ValueError):
            continue
        for flow_field in _OPTIONAL_CANDLE_FIELDS:
            value = row.get(flow_field)
            if value is not None:
                try:
                    candle[flow_field] = float(value)
                except (TypeError, ValueError):
                    pass
        known = {"ts", "date", "open", "high", "low", "close", "vol", *_OPTIONAL_CANDLE_FIELDS}
        candle.update({str(key): value for key, value in row.items() if key not in known})
        out.append(candle)
    return sorted(out, key=lambda r: int(r["ts"]))


def _candle_identity(
    symbol: str, timeframe: str, candles: list[dict[str, Any]],
) -> str:
    """Stable identity for the exact bounded series handed to every variant."""
    return candle_evidence_fingerprint(symbol, timeframe, candles) or ""


def _load_experiment_candles(
    data_glob: str,
    symbol: str,
    *,
    timeframe: str | None,
    candle_store: Any | None,
) -> tuple[list[dict[str, Any]], str, str, str] | None:
    """Load once from the canonical store, with JSON as migration fallback."""
    wanted_tf = str(timeframe or "").strip().lower()
    if candle_store is not None and wanted_tf:
        from src.research_lab.candle_library import load_canonical_candles

        selected = load_canonical_candles(
            candle_store.private_root,
            symbol,
            wanted_tf,
            fallback_glob=data_glob,
            purpose="experiment",
            coverage_policy="gap_free",
        )
        if selected.rows:
            return (
                selected.rows,
                selected.label,
                selected.manifest.evidence_hash,
                selected.manifest.snapshot_id,
            )

    path = choose_symbol_file(data_glob, symbol, timeframe=timeframe)
    if not path:
        return None
    rows = load_candles(path)
    if not rows:
        return None
    resolved_tf = wanted_tf or _derive_timeframe(rows)
    from src.research_lab.candle_snapshot import build_snapshot

    legacy_rows = []
    for row in rows:
        item = dict(row)
        item["_source"] = "legacy-json"
        item["_provenance_status"] = "legacy_unknown"
        legacy_rows.append(item)
    snapshot = build_snapshot(
        symbol=symbol,
        timeframe=resolved_tf,
        rows=legacy_rows,
        start_ts=int(legacy_rows[0]["ts"]),
        end_ts=int(legacy_rows[-1]["ts"]),
        as_of_ms=None,
        purpose="experiment",
        coverage_policy="gap_free",
        source_backend="json",
    )
    return (
        snapshot.rows, path.name, snapshot.manifest.evidence_hash,
        snapshot.manifest.snapshot_id,
    )


def _derive_timeframe(candles: list[dict[str, Any]]) -> str:
    """Best-effort timeframe label from candle spacing (e.g. '1d', '15m').

    Returns '' when it cannot be inferred. Normalized to lowercase so it matches
    the timeframe-profile / registry convention (1d, 1h, 4h, 15m, 1m).
    """
    from src.research_lab.data_inventory import infer_timeframe

    ts_values = [int(c["ts"]) for c in candles if "ts" in c]
    label = infer_timeframe(ts_values)
    return "" if label == "unknown" else label.lower()


def choose_symbol_file(
    pattern: str, symbol: str, *, timeframe: str | None = None,
) -> Path | None:
    normalized = symbol.replace("-", "_").replace("/", "_")
    candidates = [Path(p) for p in glob.glob(pattern.format(symbol=normalized))]
    if not candidates:
        candidates = [Path(p) for p in glob.glob(pattern.format(symbol=symbol))]
    if not candidates:
        return None
    if timeframe is None:
        return sorted(candidates, key=lambda p: (_safe_row_count(p), str(p)), reverse=True)[0]
    from src.research_lab.data_inventory import inspect_file
    wanted = str(timeframe).strip().lower()
    matching: list[tuple[int, Path]] = []
    for p in candidates:
        info = inspect_file(p)
        if info.get("timeframe", "").lower() == wanted and info.get("quality_status") == "usable":
            matching.append((int(info.get("rows") or 0), p))
    if not matching:
        return None
    matching.sort(key=lambda rp: (-rp[0], str(rp[1])))
    return matching[0][1]


def _safe_row_count(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return len(data) if isinstance(data, list) else 0


def generate_signals(
    candles: list[dict[str, Any]],
    family: str,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Delegate to the strategy registry; `family` is the strategy_id."""
    return get_strategy(family).generate_signals(candles, params)


def annotate_signals_with_regime(
    candles: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    regime_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    for sig in signals:
        sig["regime"] = regime_at(candles, int(sig["idx"]), regime_params)
    return signals


def filter_signals(signals: list[dict[str, Any]], filters: dict[str, list[str]]) -> list[dict[str, Any]]:
    if not filters:
        return signals
    return [s for s in signals if regime_matches(s.get("regime") or {}, filters)]


def simulate_trades(
    candles: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    params: dict[str, Any],
    *,
    fees_bps: float,
    slippage_bps: float,
    require_complete_horizon: bool = False,
) -> list[dict[str, Any]]:
    exit_mode = str(params.get("exit_mode") or "baseline").strip().lower()
    if exit_mode and exit_mode != "baseline":
        return simulate_dynamic_exit_trades(
            candles, signals, params, exit_mode=exit_mode,
            fees_bps=fees_bps, slippage_bps=slippage_bps,
            require_complete_horizon=require_complete_horizon,
        )
    hold_bars = int(params.get("hold_bars", 5))
    stop_pct = float(params.get("stop_pct", 0.0))
    take_pct = float(params.get("take_pct", 0.0))
    cost_pct = (fees_bps + slippage_bps) / 10000.0
    trades = []
    for sig in signals:
        idx = int(sig["idx"])
        horizon_end = idx + hold_bars
        exit_idx = min(horizon_end, len(candles) - 1)
        if idx >= len(candles) or exit_idx <= idx:
            continue
        side = str(sig["side"])
        entry = float(candles[idx]["open"])
        if entry <= 0:
            continue
        exit_price = float(candles[exit_idx]["close"])
        outcome = "time_exit"
        actual_exit_idx = exit_idx
        for j in range(idx, exit_idx + 1):
            high = float(candles[j]["high"])
            low = float(candles[j]["low"])
            if side == "long":
                stop = entry * (1 - stop_pct / 100) if stop_pct > 0 else None
                take = entry * (1 + take_pct / 100) if take_pct > 0 else None
                if stop and low <= stop:
                    exit_price, outcome, actual_exit_idx = stop, "stop", j
                    break
                if take and high >= take:
                    exit_price, outcome, actual_exit_idx = take, "take", j
                    break
            else:
                stop = entry * (1 + stop_pct / 100) if stop_pct > 0 else None
                take = entry * (1 - take_pct / 100) if take_pct > 0 else None
                if stop and high >= stop:
                    exit_price, outcome, actual_exit_idx = stop, "stop", j
                    break
                if take and low <= take:
                    exit_price, outcome, actual_exit_idx = take, "take", j
                    break
        if require_complete_horizon and outcome == "time_exit" and horizon_end >= len(candles):
            continue
        trades.append(finalize_trade(
            candles, idx, actual_exit_idx, side, entry, exit_price, outcome, sig, cost_pct,
            simulator_manifest=legacy_fixture_manifest(),
            cost_ledger=build_cost_ledger(fees_bps=fees_bps, slippage_bps=slippage_bps),
        ))
    return trades


def _dynamic_mode_params(params: dict[str, Any], exit_mode: str) -> dict[str, Any]:
    stop = float(params.get("stop_pct") or 0.0) or 1.0
    take = float(params.get("take_pct") or 0.0) or (stop * 2)
    hold = int(params.get("hold_bars") or 5)
    if exit_mode == "early_tp":
        return {"kind": "fixed", "take_pct": round(take * 0.5, 6), "hold_bars": hold}
    if exit_mode == "trailing":
        return {"kind": "trailing", "trail_pct": round(stop, 6), "hold_bars": hold}
    if exit_mode == "trailing_tight":
        return {"kind": "trailing", "trail_pct": round(stop * 0.5, 6), "hold_bars": hold}
    if exit_mode == "break_even":
        return {"kind": "break_even", "be_trigger_pct": round(stop, 6), "hold_bars": hold}
    if exit_mode == "time_decay":
        return {"kind": "time_decay", "hold_bars": hold}
    if exit_mode in {"partial_tp", "partial_be"}:
        return {
            "kind": "partial",
            "tp1_pct": round(take * 0.5, 6),
            "tp2_pct": round(take, 6),
            "hold_bars": hold,
        }
    if exit_mode == "hold_long":
        return {"kind": "fixed", "hold_bars": max(1, hold * 2)}
    return {"kind": "fixed", "hold_bars": hold}


def _level(entry: float, pct: float, *, long_: bool) -> float | None:
    if pct <= 0:
        return None
    return entry * (1 + pct / 100) if long_ else entry * (1 - pct / 100)


def _scan_dynamic_exit(
    candles: list[dict[str, Any]],
    idx: int,
    cap: int,
    entry: float,
    side: str,
    stop_pct: float,
    take_pct: float,
    mode: dict[str, Any],
) -> tuple[float, str, int]:
    long_ = side == "long"
    kind = str(mode.get("kind") or "fixed")
    take_pct = float(mode.get("take_pct", take_pct))
    initial_stop = _level(entry, stop_pct, long_=not long_) if stop_pct > 0 else None
    take = _level(entry, take_pct, long_=long_)
    stop = initial_stop
    water = entry
    span = max(1, cap - idx)
    for j in range(idx, cap + 1):
        high = float(candles[j]["high"])
        low = float(candles[j]["low"])
        if stop is not None and ((long_ and low <= stop) or (not long_ and high >= stop)):
            return stop, ("trail" if kind == "trailing" else "stop"), j
        if take is not None and ((long_ and high >= take) or (not long_ and low <= take)):
            return take, "take", j
        water = max(water, high) if long_ else min(water, low)
        if kind == "trailing":
            trail = _level(water, float(mode["trail_pct"]), long_=not long_)
            stop = max(initial_stop or trail, trail) if long_ else min(initial_stop or trail, trail)
        elif kind == "break_even":
            trigger = _level(entry, float(mode["be_trigger_pct"]), long_=long_)
            if trigger is not None and ((long_ and water >= trigger) or (not long_ and water <= trigger)):
                stop = entry
        elif kind == "time_decay":
            decayed = stop_pct * max(0.0, 1 - (j - idx) / span)
            stop = _level(entry, decayed, long_=not long_)
    return float(candles[cap]["close"]), "time_exit", cap


def _partial_dynamic_trade(
    candles: list[dict[str, Any]],
    idx: int,
    cap: int,
    entry: float,
    side: str,
    stop_pct: float,
    mode: dict[str, Any],
    sig: dict[str, Any],
    cost_pct: float,
) -> dict[str, Any] | None:
    long_ = side == "long"
    direction = 1 if long_ else -1
    tp1 = _level(entry, float(mode["tp1_pct"]), long_=long_)
    tp2 = _level(entry, float(mode["tp2_pct"]), long_=long_)
    stop = _level(entry, stop_pct, long_=not long_) if stop_pct > 0 else None
    filled_first = False
    first_exit = entry
    outcome = "time_exit"
    exit_price = float(candles[cap]["close"])
    exit_idx = cap
    for j in range(idx, cap + 1):
        high = float(candles[j]["high"])
        low = float(candles[j]["low"])
        if not filled_first:
            if stop is not None and ((long_ and low <= stop) or (not long_ and high >= stop)):
                exit_price, exit_idx, outcome = stop, j, "stop"
                break
            if tp1 is not None and ((long_ and high >= tp1) or (not long_ and low <= tp1)):
                filled_first = True
                first_exit = tp1
                stop = entry
                continue
        else:
            if tp2 is not None and ((long_ and high >= tp2) or (not long_ and low <= tp2)):
                exit_price, exit_idx, outcome = tp2, j, "take"
                break
            if stop is not None and ((long_ and low <= stop) or (not long_ and high >= stop)):
                exit_price, exit_idx, outcome = stop, j, "partial_be"
                break
    first_fraction = 0.5 if filled_first else 0.0
    first_ret = direction * (first_exit / entry - 1) * 100 if filled_first else 0.0
    second_ret = direction * (exit_price / entry - 1) * 100
    ret_pct = first_fraction * first_ret + (1 - first_fraction) * second_ret
    net_pct = ret_pct - cost_pct * 100
    mfe_pct, mae_pct, mfe_off, mae_off = _trade_path(
        candles, idx, exit_idx, entry, side,
        exit_price=exit_price, outcome=outcome,
    )
    capture = round(ret_pct / mfe_pct, 4) if mfe_pct > 0 else 0.0
    return {
        "entry_ts": candles[idx]["ts"],
        "exit_ts": candles[exit_idx]["ts"],
        "side": side,
        "entry": round(entry, 10),
        "exit": round(exit_price, 10),
        "gross_pct": round(ret_pct, 4),
        "net_pct": round(net_pct, 4),
        "outcome": outcome,
        "reason": sig.get("reason"),
        "regime": sig.get("regime") or {},
        "mfe_pct": round(mfe_pct, 4),
        "mae_pct": round(mae_pct, 4),
        "capture_of_mfe": capture,
        "bars_held": int(exit_idx - idx),
        "time_to_mfe": int(mfe_off),
        "time_to_mae": int(mae_off),
        "tp_before_sl": True if outcome == "take" else (False if outcome == "stop" else None),
        "bars_to_tp": int(exit_idx - idx) if outcome == "take" else None,
        "bars_to_sl": int(exit_idx - idx) if outcome == "stop" else None,
        "adverse_before_favorable": bool(mae_off < mfe_off),
        "path_quality": _path_quality(mfe_pct, capture, mae_off < mfe_off),
        "partial_exit_fraction": first_fraction,
        "partial_leg_prices": (
            [float(first_exit), float(exit_price)] if first_fraction else [float(exit_price)]
        ),
        "path_observation": _path_observation(outcome),
    }


def simulate_dynamic_exit_trades(
    candles: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    params: dict[str, Any],
    *,
    exit_mode: str,
    fees_bps: float,
    slippage_bps: float,
    require_complete_horizon: bool = False,
) -> list[dict[str, Any]]:
    mode = _dynamic_mode_params(params, exit_mode)
    hold_bars = int(mode.get("hold_bars", params.get("hold_bars", 5)))
    stop_pct = float(params.get("stop_pct", 0.0))
    take_pct = float(params.get("take_pct", 0.0))
    cost_pct = (fees_bps + slippage_bps) / 10000.0
    trades: list[dict[str, Any]] = []
    for sig in signals:
        idx = int(sig["idx"])
        horizon_end = idx + hold_bars
        cap = min(horizon_end, len(candles) - 1)
        if idx >= len(candles) or cap <= idx:
            continue
        side = str(sig["side"])
        entry = float(candles[idx]["open"])
        if entry <= 0:
            continue
        if mode["kind"] == "partial":
            trade = _partial_dynamic_trade(candles, idx, cap, entry, side, stop_pct, mode, sig, cost_pct)
            if trade is not None:
                if require_complete_horizon and trade.get("outcome") == "time_exit" and horizon_end >= len(candles):
                    continue
                manifest = legacy_fixture_manifest()
                fraction = float(trade.get("partial_exit_fraction") or 0.0)
                quantities = [fraction, 1.0 - fraction] if fraction else [1.0]
                reconciliation = reconcile_partial_fills(
                    1.0,
                    [
                        {"quantity": quantity, "price": price, "cost_pct": cost_pct * 100.0}
                        for quantity, price in zip(quantities, trade["partial_leg_prices"])
                    ],
                    entry_price=entry,
                    side=side,
                )
                if abs(float(reconciliation["net_return_pct"]) - float(trade["net_pct"])) > 1e-3:
                    raise ValueError("partial fill reconciliation does not match trade return")
                trade.update({
                    "simulator_manifest": manifest,
                    "simulator_model_id": manifest["simulator_model_id"],
                    "simulator_evidence_tier": manifest["evidence_tier"],
                    "unsupported_simulator_dimensions": list(manifest["unsupported_dimensions"]),
                    "cost_ledger": build_cost_ledger(
                        fees_bps=fees_bps, slippage_bps=slippage_bps,
                    ),
                    "quantity_ledger": build_trade_quantity_ledger(
                        closed_legs=(
                            (float(trade.get("partial_exit_fraction") or 0.0),
                             1.0 - float(trade.get("partial_exit_fraction") or 0.0))
                            if float(trade.get("partial_exit_fraction") or 0.0) > 0
                            else (1.0,)
                        ),
                    ),
                    "fill_reconciliation": reconciliation,
                })
                trades.append(trade)
            continue
        exit_price, outcome, exit_idx = _scan_dynamic_exit(
            candles, idx, cap, entry, side, stop_pct, take_pct, mode
        )
        if require_complete_horizon and outcome == "time_exit" and horizon_end >= len(candles):
            continue
        trades.append(finalize_trade(
            candles, idx, exit_idx, side, entry, exit_price, outcome, sig, cost_pct,
            simulator_manifest=legacy_fixture_manifest(),
            cost_ledger=build_cost_ledger(fees_bps=fees_bps, slippage_bps=slippage_bps),
        ))
    return trades


def finalize_trade(
    candles: list[dict[str, Any]],
    idx: int,
    actual_exit_idx: int,
    side: str,
    entry: float,
    exit_price: float,
    outcome: str,
    sig: dict[str, Any],
    cost_pct: float,
    *,
    simulator_manifest: dict[str, Any] | None = None,
    cost_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one trade dict from a decided exit (entry, exit_price, outcome, bars).

    Single source of truth for the trade arithmetic + rounding, shared by the CPU
    simulator and the GPU/batched simulator so their outputs are identical by
    construction (the GPU path only decides exit_price/outcome/actual_exit_idx).
    """
    direction = 1 if side == "long" else -1
    ret_pct = direction * (exit_price / entry - 1) * 100
    net_pct = ret_pct - cost_pct * 100
    mfe_pct, mae_pct, mfe_off, mae_off = _trade_path(
        candles, idx, actual_exit_idx, entry, side,
        exit_price=exit_price, outcome=outcome,
    )
    capture = round(ret_pct / mfe_pct, 4) if mfe_pct > 0 else 0.0
    bars_held = int(actual_exit_idx - idx)
    adverse_first = mae_off < mfe_off
    manifest = simulator_manifest or legacy_fixture_manifest()
    ledger = cost_ledger or build_legacy_combined_cost_ledger(cost_pct)
    if abs(float(ledger.get("total_pct") or 0.0) - cost_pct * 100.0) > 1e-9:
        raise ValueError("trade cost ledger does not reconcile to deducted cost")
    return {
        "entry_ts": candles[idx]["ts"],
        "exit_ts": candles[actual_exit_idx]["ts"],
        "side": side,
        "entry": round(entry, 10),
        "exit": round(exit_price, 10),
        "net_pct": round(net_pct, 4),
        "outcome": outcome,
        "reason": sig.get("reason"),
        "regime": sig.get("regime") or {},
        "mfe_pct": round(mfe_pct, 4),
        "mae_pct": round(mae_pct, 4),
        "capture_of_mfe": capture,
        # Trade-path instrumentation (descriptive; computed over the ALREADY-decided hold
        # window, so it never changes the exit decision and adds no look-ahead).
        "bars_held": bars_held,
        "time_to_mfe": int(mfe_off),
        "time_to_mae": int(mae_off),
        "tp_before_sl": True if outcome == "take" else (False if outcome == "stop" else None),
        "bars_to_tp": bars_held if outcome == "take" else None,
        "bars_to_sl": bars_held if outcome == "stop" else None,
        "adverse_before_favorable": bool(adverse_first),
        "path_quality": _path_quality(mfe_pct, capture, adverse_first),
        "path_observation": _path_observation(outcome),
        "simulator_manifest": manifest,
        "simulator_model_id": manifest["simulator_model_id"],
        "simulator_evidence_tier": manifest["evidence_tier"],
        "unsupported_simulator_dimensions": list(manifest["unsupported_dimensions"]),
        "cost_ledger": ledger,
        "quantity_ledger": build_trade_quantity_ledger(),
    }


def _path_observation(outcome: str) -> str:
    return (
        "full_ohlc_through_close"
        if str(outcome) == "time_exit" else "exit_bar_censored_to_exit_price"
    )


def _path_quality(mfe_pct: float, capture: float, adverse_before_favorable: bool) -> str:
    """One-word path label for the outcome-memory drill (wrong_exit = 'gave_back')."""
    if mfe_pct <= 0:
        return "no_move"
    if capture >= 0.7:
        return "clean_capture"
    if capture < 0.3:
        return "gave_back"          # the move happened but the exit gave it back (wrong_exit)
    return "heat_first" if adverse_before_favorable else "partial"


def _trade_path(
    candles: list[dict[str, Any]],
    entry_idx: int,
    exit_idx: int,
    entry: float,
    side: str,
    *,
    exit_price: float | None = None,
    outcome: str = "time_exit",
) -> tuple[float, float, int, int]:
    """MFE/MAE magnitude (pct of entry) AND the bar offset where each occurred, over the
    realized hold window [entry_idx, exit_idx]. Descriptive only — no look-ahead beyond the
    already-decided exit. Returns (mfe_pct, mae_pct, mfe_bar_offset, mae_bar_offset)."""
    hi = lo = None
    hi_off = lo_off = 0
    intrabar_exit = str(outcome) != "time_exit" and exit_price is not None
    last_full_idx = exit_idx - 1 if intrabar_exit else exit_idx
    for off, j in enumerate(range(entry_idx, last_full_idx + 1)):
        h, low = float(candles[j]["high"]), float(candles[j]["low"])
        if hi is None or h > hi:
            hi, hi_off = h, off
        if lo is None or low < lo:
            lo, lo_off = low, off
    if intrabar_exit:
        off = max(0, exit_idx - entry_idx)
        known = [float(entry), float(exit_price)] if exit_idx == entry_idx else [float(exit_price)]
        h, low = max(known), min(known)
        if hi is None or h > hi:
            hi, hi_off = h, off
        if lo is None or low < lo:
            lo, lo_off = low, off
    if side == "long":
        favorable, adverse, fav_off, adv_off = (hi - entry) / entry * 100, (entry - lo) / entry * 100, hi_off, lo_off
    else:
        favorable, adverse, fav_off, adv_off = (entry - lo) / entry * 100, (hi - entry) / entry * 100, lo_off, hi_off
    return max(0.0, favorable), max(0.0, adverse), fav_off, adv_off


def _trade_excursions(
    candles: list[dict[str, Any]],
    entry_idx: int,
    exit_idx: int,
    entry: float,
    side: str,
) -> tuple[float, float]:
    """Backward-compatible (mfe, mae) magnitudes; ``_trade_path`` is the timed version."""
    mfe, mae, _fav, _adv = _trade_path(candles, entry_idx, exit_idx, entry, side)
    return mfe, mae


def _entry_timing_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-trade timing: how much favorable move we captured vs heat taken.

    Heuristic trade-quality proxy for the "direction right, entry late" pain.
    The event-precise version (src/research_lab/entry_timing.py) is used in the
    event-driven sweep path where a real pre-move window exists.
    """
    if not trades:
        return {}
    mfes = [float(t.get("mfe_pct") or 0.0) for t in trades]
    maes = [float(t.get("mae_pct") or 0.0) for t in trades]
    captures = [float(t.get("capture_of_mfe") or 0.0) for t in trades]
    late = sum(1 for t in trades if float(t.get("mae_pct") or 0.0) > float(t.get("mfe_pct") or 0.0))
    n = len(trades)
    return {
        "avg_mfe_pct": round(sum(mfes) / n, 4),
        "avg_mae_pct": round(sum(maes) / n, 4),
        "avg_capture_ratio": round(sum(captures) / n, 4),
        "late_entry_rate": round(late / n, 4),
    }


def event_entry_timing_for_run(
    candles: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    event_context: dict[str, Any],
) -> dict[str, Any]:
    """Event-anchored timing for one run, when the spec carries an event context.

    Maps the historical event's ts window onto this symbol's bars and measures the
    first trade that entered at/after the move start. The simulator still made the
    entry decision with no look-ahead; this only adds an event-anchored measurement.
    Returns {} when there is no usable context or no qualifying trade (callers then
    keep the existing proxy metrics unchanged).
    """
    if not event_context or not trades:
        return {}
    try:
        start_ts = int(event_context["move_start_ts"])
        end_ts = int(event_context["move_end_ts"])
    except (KeyError, TypeError, ValueError):
        return {}
    direction = str(event_context.get("direction") or "up")
    ts = [int(c["ts"]) for c in candles]
    n = len(ts)
    if n == 0:
        return {}
    move_start_idx = bisect.bisect_left(ts, start_ts)
    if move_start_idx >= n:
        return {}
    move_end_idx = min(max(bisect.bisect_left(ts, end_ts), move_start_idx), n - 1)
    entry_idx = None
    for trade in trades:
        try:
            entry_ts = int(trade["entry_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if entry_ts >= start_ts:
            j = bisect.bisect_left(ts, entry_ts)
            if 0 <= j < n:
                entry_idx = j
                break
    if entry_idx is None:
        return {}
    eval_end = min(n - 1, max(entry_idx, move_end_idx))
    try:
        return event_anchored_timing(
            candles,
            move_start_idx=move_start_idx,
            move_end_idx=move_end_idx,
            entry_idx=entry_idx,
            direction=direction,
            eval_end_idx=eval_end,
        )
    except ValueError:
        return {}


def compute_metrics(
    trades: list[dict[str, Any]],
    split_ratio: float,
    min_trades: int,
    stress_extra_cost_pct: float = 0.0,
) -> dict[str, Any]:
    n = len(trades)
    returns = [float(t["net_pct"]) for t in trades]
    wins = [r for r in returns if r > 0]
    split = max(1, min(n, int(n * split_ratio))) if n else 0
    train = returns[:split]
    test = returns[split:]
    total = sum(returns)
    best_share = max((abs(r) for r in returns), default=0.0) / abs(total) if total else 0.0
    avg = total / n if n else 0.0
    pf_state = profit_factor_state(returns)
    return {
        "n_trades": n,
        "win_rate": round(len(wins) / n, 4) if n else 0.0,
        "avg_net_pct": round(avg, 4),
        "total_net_pct": round(total, 4),
        "profit_factor": pf_state["value"],
        "profit_factor_state": pf_state,
        "max_drawdown_pct": round(_max_drawdown(returns), 4),
        "best_trade_share": round(best_share, 4),
        "train_avg_net_pct": round(sum(train) / len(train), 4) if train else 0.0,
        "test_avg_net_pct": round(sum(test) / len(test), 4) if test else 0.0,
        "test_trades": len(test),
        "min_trades": min_trades,
        "stress_avg_net_pct": round(avg - stress_extra_cost_pct, 4),
        "regime_breakdown": regime_breakdown(trades),
        "entry_timing": _entry_timing_metrics(trades),
        "aggregate_basis": "independent_what_if_additive_v1",
        "portfolio_metrics_status": "unavailable_without_chronological_account_allocation",
    }


def _max_drawdown(returns: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for ret in returns:
        equity += ret
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return dd


def grade_candidate(metrics: dict[str, Any]) -> tuple[str, list[str]]:
    reasons = []
    if metrics["n_trades"] < metrics["min_trades"]:
        reasons.append("too_few_trades")
    pf_state = metrics.get("profit_factor_state") or {}
    pf_value = metrics.get("profit_factor")
    if pf_state.get("state") not in {"finite", "positive_infinity"}:
        reasons.append("profit_factor_unavailable")
    elif pf_value is not None and float(pf_value) < 1.15:
        reasons.append("weak_profit_factor")
    if metrics["avg_net_pct"] <= 0:
        reasons.append("negative_or_flat_average")
    if metrics["test_trades"] < 5:
        reasons.append("weak_oos_sample")
    if metrics["test_avg_net_pct"] <= 0:
        reasons.append("oos_not_positive")
    if metrics["best_trade_share"] > 0.45:
        reasons.append("single_trade_dominance")
    if metrics["max_drawdown_pct"] > abs(metrics["total_net_pct"]) * 0.8 and metrics["total_net_pct"] > 0:
        reasons.append("drawdown_too_large_vs_total")

    if not reasons:
        return "PROMOTE_FOR_PRESSURE_TEST", ["passed_basic_gates"]
    if metrics["n_trades"] >= metrics["min_trades"] and metrics["avg_net_pct"] > 0 and metrics["test_avg_net_pct"] > 0:
        return "OBSERVE", reasons
    return "REJECT", reasons


def stable_run_id(
    symbol: str,
    family: str,
    params: dict[str, Any],
    *,
    experiment_id: str = "",
    data_fingerprint: str = "",
    timeframe: str = "",
    fees_bps: float = 0.0,
    slippage_bps: float = 0.0,
    split_ratio: float = 0.0,
    filters: dict[str, list[str]] | None = None,
) -> str:
    payload = {
        "experiment_id": experiment_id,
        "symbol": symbol,
        "family": family,
        "params": params,
        "data_fingerprint": data_fingerprint,
        "timeframe": timeframe,
        "fees_bps": fees_bps,
        "slippage_bps": slippage_bps,
        "split_ratio": split_ratio,
        "filters": filters or {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def evaluate_spec(
    spec: ExperimentSpec,
    runtime_meta: dict[str, Any] | None = None,
    *,
    candle_store: Any | None = None,
) -> list[RunResult]:
    """Run a spec. ``backend`` decides the signal-generation path:

    cpu  -> the scalar reference path (unchanged).
    gpu  -> the vectorized array kernel on a GPU backend for supported families
            (others fall back to the scalar path, recorded honestly). Raises if
            ``gpu`` was requested but no GPU backend is available (never silent CPU).
    auto -> gpu when available, else cpu with a recorded fallback reason.

    ``runtime_meta`` (optional) is filled with the resolved backend, gpu
    availability, fallback reason, elapsed_ms, and accelerated-run count.
    """
    resolution = resolve_backend(spec.backend)
    if runtime_meta is not None:
        runtime_meta.update(resolution.to_dict())
        runtime_meta["gpu_supported_families"] = list(GPU_SUPPORTED_FAMILIES)
    if not resolution.ok():
        # gpu explicitly requested but unavailable: explicit error, never CPU-in-disguise.
        raise RuntimeError(resolution.error)
    use_gpu = resolution.effective_backend == GPU
    xp = array_module(resolution.backend_name) if use_gpu else None
    from src.research_lab import gpu_simulator  # local import avoids an import cycle

    started = time.perf_counter()
    accelerated_signal_runs = 0
    accelerated_simulation_runs = 0
    cpu_fallback_families: set[str] = set()
    sim_fallback_reasons: set[str] = set()

    stress_extra = (spec.fees_bps + spec.slippage_bps) / 10000.0 * 100 * (COST_STRESS_MULT - 1)
    results = []
    tf = spec.timeframe if spec.timeframe else None
    loaded: dict[tuple[str, str], tuple[list[dict[str, Any]], str, str, str]] = {}
    for symbol in spec.symbols:
        cache_key = (symbol, str(tf or ""))
        candle_bundle = _load_experiment_candles(
            spec.data_glob, symbol, timeframe=tf, candle_store=candle_store,
        )
        if candle_bundle is not None:
            loaded[cache_key] = candle_bundle
    if (spec.data_snapshot_id or spec.data_evidence_hash) and len(loaded) != len(spec.symbols):
        raise RuntimeError("queued candle snapshot drift: a scheduled symbol is unavailable")
    resolved_snapshot_id = ""
    resolved_evidence_hash = ""
    resolved_snapshot_bindings: list[dict[str, Any]] = []
    if loaded:
        from src.research_lab.search_family_definition import (
            normalize_snapshot_bindings,
            snapshot_set_identity,
        )

        resolved_snapshot_bindings = normalize_snapshot_bindings(
            [
                {
                    "symbol": symbol,
                    "timeframe": str(tf or _derive_timeframe(bundle[0])),
                    "snapshot_id": bundle[3],
                    "evidence_hash": bundle[2],
                    "row_count": len(bundle[0]),
                }
                for (symbol, _timeframe), bundle in loaded.items()
            ]
        )
        resolved_snapshot_id, resolved_evidence_hash = snapshot_set_identity(
            resolved_snapshot_bindings
        )
    if (
        spec.data_snapshot_id and resolved_snapshot_id != spec.data_snapshot_id
    ) or (
        spec.data_evidence_hash and resolved_evidence_hash != spec.data_evidence_hash
    ) or (
        spec.data_snapshot_bindings
        and resolved_snapshot_bindings != list(spec.data_snapshot_bindings)
    ):
        raise RuntimeError(
            "queued candle snapshot drift: selected evidence no longer matches the queued spec"
        )
    for symbol, family in itertools.product(spec.symbols, spec.families):
        cache_key = (symbol, str(tf or ""))
        candle_bundle = loaded.get(cache_key)
        if candle_bundle is None:
            candle_bundle = _load_experiment_candles(
                spec.data_glob, symbol, timeframe=tf, candle_store=candle_store,
            )
            if candle_bundle is not None:
                loaded[cache_key] = candle_bundle
        if candle_bundle is None:
            continue
        candles, data_label, data_fingerprint, data_snapshot_id = candle_bundle
        # Record the resolved timeframe so downstream provenance (candidate
        # registry -> hard validation -> setup cards) never loses it. When the
        # spec carries no explicit timeframe, derive it from the candle spacing
        # instead of leaving it blank (the old behavior that produced "unknown").
        file_tf = tf or _derive_timeframe(candles)
        family_variants = spec.parameter_grid.get(family, [])
        strategy_defaults = dict(get_strategy(family).parameter_defaults)
        needs = missing_required_data(family, candles)
        if needs is not None:
            for raw_params in family_variants:
                params = {**strategy_defaults, **dict(raw_params or {})}
                zero = compute_metrics([], spec.split_ratio, spec.min_trades, stress_extra)
                zero["data_file_label"] = data_label
                zero["data_file_timeframe"] = file_tf or ""
                zero["data_source"] = "sqlite" if data_label.startswith("sqlite:") else "json"
                zero["data_fingerprint"] = data_fingerprint
                zero["data_snapshot_id"] = data_snapshot_id
                zero["data_evidence_hash"] = data_fingerprint
                zero["family_data_snapshot_id"] = resolved_snapshot_id
                zero["family_data_evidence_hash"] = resolved_evidence_hash
                zero["execution_identity"] = {
                    "requested_backend": spec.backend,
                    "resolved_backend": resolution.effective_backend,
                    "backend_name": "not_executed",
                    "signal_backend": "not_executed",
                    "signal_kernel": "not_executed_data_gate",
                    "signal_backend_reason": "data_gate",
                    "signal_candle_count": len(candles),
                    "signal_family_variant_count": len(family_variants),
                    "simulation_backend": "not_executed",
                    "simulator": "not_executed_data_gate",
                    "terminal_phase": "data_gate",
                }
                results.append(RunResult(
                    run_id=stable_run_id(
                        symbol, family, params, experiment_id=spec.experiment_id,
                        data_fingerprint=data_fingerprint, timeframe=file_tf or "",
                        fees_bps=spec.fees_bps, slippage_bps=spec.slippage_bps,
                        split_ratio=spec.split_ratio, filters=spec.filters,
                    ), symbol=symbol, family=family,
                    params=params, metrics=zero, decision=needs,
                    reasons=["missing_required_data"], trades=[],
                    validation_status=needs, validation_reasons=[], risk_flags=["needs_data"],
                    next_action="provide the required OI/funding/microstructure data, then re-run",
                    regime_summary={}))
                if spec.max_runs and len(results) >= spec.max_runs:
                    _finalize_runtime_meta(
                        runtime_meta, started, accelerated_signal_runs,
                        accelerated_simulation_runs, cpu_fallback_families,
                        sim_fallback_reasons, n_variants=len(results),
                    )
                    return results
            continue
        auto_batch_ok = spec.backend != "auto" or auto_gpu_worthwhile(len(candles), len(family_variants))
        gpu_family = use_gpu and auto_batch_ok and gpu_kernels.supported_family(family)
        if use_gpu and not gpu_family:
            cpu_fallback_families.add(
                f"{family}:auto_batch_too_small" if not auto_batch_ok else family
            )
        prepared_arrays = gpu_kernels.prepare_candle_arrays(candles, xp=xp) if gpu_family else None
        for raw_params in family_variants:
            params = {**strategy_defaults, **dict(raw_params or {})}
            trial_identity = {
                "requested_backend": spec.backend,
                "resolved_backend": resolution.effective_backend,
                "backend_name": (
                    str(resolution.backend_name) if gpu_family else "numpy"
                ),
                "signal_backend": "gpu" if gpu_family else "cpu",
                "signal_kernel": "gpu_kernels" if gpu_family else "strategy_generator",
                "signal_backend_reason": (
                    "gpu_eligible"
                    if gpu_family
                    else "resolved_cpu"
                    if not use_gpu
                    else "auto_batch_too_small"
                    if not auto_batch_ok
                    else "unsupported_family"
                ),
                "signal_candle_count": len(candles),
                "signal_family_variant_count": len(family_variants),
                "simulation_backend": "not_executed",
                "simulator": "not_executed_before_simulation",
                "terminal_phase": "signal_generation",
            }
            try:
                if gpu_family:
                    signals = gpu_kernels.generate_signals_vectorized(
                        candles, family, params, xp=xp, arrays=prepared_arrays,
                    )
                    accelerated_signal_runs += 1
                else:
                    signals = generate_signals(candles, family, params)
            except Exception as exc:
                results.append(
                    _execution_error_result(
                        spec,
                        symbol=symbol,
                        family=family,
                        params=params,
                        data_label=data_label,
                        data_fingerprint=data_fingerprint,
                        data_snapshot_id=data_snapshot_id,
                        family_data_snapshot_id=resolved_snapshot_id,
                        family_data_evidence_hash=resolved_evidence_hash,
                        execution_identity=trial_identity,
                        timeframe=file_tf or "",
                        stress_extra=stress_extra,
                        error=exc,
                    )
                )
                if spec.max_runs and len(results) >= spec.max_runs:
                    _finalize_runtime_meta(
                        runtime_meta,
                        started,
                        accelerated_signal_runs,
                        accelerated_simulation_runs,
                        cpu_fallback_families,
                        sim_fallback_reasons,
                        n_variants=len(results),
                    )
                    return results
                continue
            try:
                signals = annotate_signals_with_regime(candles, signals, spec.regime_params)
                signals = filter_signals(signals, spec.filters)
                # Simulation backend is decided per variant: GPU only for the supported
                # exit mode and a batch that fits the VRAM cap; otherwise CPU with a
                # recorded reason (never a silent wrong-GPU result).
                use_gpu_sim = use_gpu
                if use_gpu:
                    ok_mode, mode_reason = gpu_simulator.supported_simulation_mode(params)
                    if not ok_mode:
                        use_gpu_sim, reason = False, mode_reason
                    elif not gpu_simulator.within_memory_cap(
                        len(signals), int(params.get("hold_bars", 5))
                    ):
                        use_gpu_sim, reason = False, "gpu_batch_exceeds_vram_cap"
                    if not use_gpu_sim:
                        sim_fallback_reasons.add(reason)
                if use_gpu_sim:
                    trial_identity.update(
                        backend_name=str(resolution.backend_name),
                        simulation_backend="gpu",
                        simulator="gpu_simulator",
                        terminal_phase="simulation",
                    )
                    trades = gpu_simulator.simulate_trades_batched(
                        candles, signals, params,
                        fees_bps=spec.fees_bps, slippage_bps=spec.slippage_bps, xp=xp,
                    )
                    accelerated_simulation_runs += 1
                else:
                    trial_identity.update(
                        simulation_backend="cpu",
                        simulator="cpu_simulator",
                        terminal_phase="simulation",
                    )
                    trades = simulate_trades(
                        candles, signals, params,
                        fees_bps=spec.fees_bps, slippage_bps=spec.slippage_bps,
                    )
                metrics = compute_metrics(
                    trades, spec.split_ratio, spec.min_trades, stress_extra
                )
                metrics["data_file_label"] = data_label
                metrics["data_file_timeframe"] = file_tf or ""
                metrics["data_source"] = (
                    "sqlite" if data_label.startswith("sqlite:") else "json"
                )
                metrics["data_fingerprint"] = data_fingerprint
                metrics["data_snapshot_id"] = data_snapshot_id
                metrics["data_evidence_hash"] = data_fingerprint
                metrics["family_data_snapshot_id"] = resolved_snapshot_id
                metrics["family_data_evidence_hash"] = resolved_evidence_hash
                trial_identity["terminal_phase"] = "completed"
                metrics["execution_identity"] = dict(trial_identity)
                if spec.event_context:
                    event_timing = event_entry_timing_for_run(
                        candles, trades, spec.event_context
                    )
                    if event_timing:
                        metrics["event_entry_timing"] = event_timing
                decision, reasons = grade_candidate(metrics)
                validation = validate_candidate(metrics, decision)
                result = RunResult(
                    run_id=stable_run_id(
                        symbol, family, params, experiment_id=spec.experiment_id,
                        data_fingerprint=data_fingerprint, timeframe=file_tf or "",
                        fees_bps=spec.fees_bps, slippage_bps=spec.slippage_bps,
                        split_ratio=spec.split_ratio, filters=spec.filters,
                    ),
                    symbol=symbol,
                    family=family,
                    params=params,
                    metrics=metrics,
                    decision=decision,
                    reasons=reasons,
                    trades=trades,
                    validation_status=validation.status,
                    validation_reasons=validation.reasons,
                    risk_flags=validation.risk_flags,
                    next_action=validation.next_action,
                    regime_summary=_regime_summary(metrics),
                )
            except Exception as exc:
                result = _execution_error_result(
                    spec,
                    symbol=symbol,
                    family=family,
                    params=params,
                    data_label=data_label,
                    data_fingerprint=data_fingerprint,
                    data_snapshot_id=data_snapshot_id,
                    family_data_snapshot_id=resolved_snapshot_id,
                    family_data_evidence_hash=resolved_evidence_hash,
                    execution_identity=trial_identity,
                    timeframe=file_tf or "",
                    stress_extra=stress_extra,
                    error=exc,
                )
            results.append(result)
            if spec.max_runs and len(results) >= spec.max_runs:
                _finalize_runtime_meta(runtime_meta, started, accelerated_signal_runs,
                                       accelerated_simulation_runs, cpu_fallback_families,
                                       sim_fallback_reasons, n_variants=len(results))
                return results
    _finalize_runtime_meta(runtime_meta, started, accelerated_signal_runs,
                           accelerated_simulation_runs, cpu_fallback_families,
                           sim_fallback_reasons, n_variants=len(results))
    return results


def _finalize_runtime_meta(
    runtime_meta: dict[str, Any] | None,
    started: float,
    accelerated_signal_runs: int,
    accelerated_simulation_runs: int,
    cpu_fallback_families: set[str],
    sim_fallback_reasons: set[str],
    n_variants: int = 0,
) -> None:
    if runtime_meta is None:
        return
    # How many parameter variants were evaluated in this sweep — the multiple-testing
    # trial count the validator uses to deflate significance (Phase 1.2).
    runtime_meta["n_variants_evaluated"] = int(n_variants)
    runtime_meta["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    # accelerated_runs kept as the signal-run count for backward compatibility.
    runtime_meta["accelerated_runs"] = accelerated_signal_runs
    runtime_meta["accelerated_signal_runs"] = accelerated_signal_runs
    runtime_meta["accelerated_simulation_runs"] = accelerated_simulation_runs
    runtime_meta["signal_backend"] = GPU if accelerated_signal_runs > 0 else "cpu"
    runtime_meta["simulation_backend"] = GPU if accelerated_simulation_runs > 0 else "cpu"
    resolved_backend = str(runtime_meta.get("effective_backend") or "cpu")
    runtime_meta["resolved_backend"] = resolved_backend
    runtime_meta["effective_backend"] = (
        GPU if accelerated_signal_runs > 0 or accelerated_simulation_runs > 0 else "cpu"
    )
    if resolved_backend == GPU and runtime_meta["effective_backend"] == "cpu":
        sim_fallback_reasons.add("no_workload_executed_on_gpu")
    runtime_meta["simulation_fallback_reason"] = "; ".join(sorted(sim_fallback_reasons))
    runtime_meta["gpu_supported_simulation_modes"] = list(GPU_SUPPORTED_SIMULATION_MODES)
    runtime_meta["cpu_fallback_families"] = sorted(cpu_fallback_families)


def _regime_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    breakdown = metrics.get("regime_breakdown") or {}
    if not breakdown:
        return {}
    dominant = max(breakdown.items(), key=lambda kv: kv[1].get("n_trades", 0))
    return {"dominant_bucket": dominant[0], "bucket_count": len(breakdown)}
