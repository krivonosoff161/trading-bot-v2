"""Outcome retest specs for the paper-learning loop.

This is the deterministic bridge after the advisory outcome reviewer:

    outcome_review -> OutcomeRetestSpec -> bounded SweepSpec -> farm task

It never calls providers, never touches exchange/order modules, and never gives
LLM output trade authority. The LLM review only selects a retest intent; this
module turns that intent into a capped, auditable research spec.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Iterable

from src.research_lab.lineage_contract import stable_id
from src.research_lab.outcome_learning import load_outcome_reviews, load_training_rows
from src.research_lab.strategy_registry import REGISTRY
from src.research_lab.sweep_spec import SweepSpec

SCHEMA = "OutcomeRetestSpec.v1"
OUTCOME_RETEST_MAX_VARIANTS = 8

_RETEST_ACTIONS = {"retest_exit_or_capture", "retest_entry_timing", "compare_breakeven_policy"}
_PAPER_TO_EXECUTABLE_FAMILY = {
    "early_tp_tactical": "momentum_breakout",
    "continuation": "momentum_breakout",
    "momentum_continuation": "momentum_breakout",
    "pullback_continuation": "breakout_retest",
    "reversal_fade": "mean_reversion_fade",
    "liquidity_sweep_reclaim": "sfp_liquidity_sweep",
}


@dataclass(frozen=True)
class OutcomeRetestSpec:
    retest_id: str
    review_id: str
    source_ref: str
    paper_signal_id: str
    candidate_id: str
    symbol: str
    timeframe: str
    family: str
    actionability: str
    outcome_bucket: str
    source_family: str = ""
    dimensions: list[str] = field(default_factory=list)
    proposed_changes: list[str] = field(default_factory=list)
    baseline: dict[str, Any] = field(default_factory=dict)
    queueable: bool = False
    not_queueable_reason: str = ""
    sweep_spec: dict[str, Any] | None = None
    paper_only: bool = True
    execution_allowed: bool = False
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _float(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def _int(row: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def _pct_distance(entry: float, other: float) -> float:
    if entry <= 0 or other <= 0:
        return 0.0
    return round(abs(other - entry) / entry * 100.0, 4)


def _scaled_levels(base: float, factors: Iterable[float], *, floor: float = 0.0001) -> list[float]:
    values = {round(base * factor, 4) for factor in factors if base * factor >= floor}
    return sorted(values)


def _baseline(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "result": str(row.get("result") or ""),
        "diagnosis": str(row.get("diagnosis") or ""),
        "exit_mode": str(row.get("exit_mode") or ""),
        "net_pct": row.get("net_pct"),
        "mfe_pct": row.get("mfe_pct"),
        "mae_pct": row.get("mae_pct"),
        "capture": row.get("capture"),
        "max_hold_bars": _int(row, "max_hold_bars"),
        "risk_pct": row.get("risk_pct"),
    }


def paper_to_executable_family(source_family: str) -> str:
    family = str(source_family or "")
    return _PAPER_TO_EXECUTABLE_FAMILY.get(family, family)


def _executable_family(source_family: str) -> str:
    return paper_to_executable_family(source_family)


def _exit_grid(row: dict[str, Any], dimensions: Iterable[str]) -> tuple[dict[str, list[Any]], list[str]]:
    entry = _float(row, "entry_mid")
    tp1 = _float(row, "tp1")
    stop = _float(row, "stop_loss")
    hold = max(2, _int(row, "max_hold_bars", 5))
    take_pct = _pct_distance(entry, tp1)
    stop_pct = _pct_distance(entry, stop)
    dims = set(str(d) for d in dimensions)
    hold_grid = sorted({max(2, hold // 2), hold, max(2, hold * 2)})
    grid: dict[str, list[Any]] = {"hold_bars": hold_grid}
    changes = ["short/current/longer hold_bars to test capture horizon"]
    if take_pct > 0 and (
        "earlier_profit_lock" in dims
        or "tp_ladder" in dims
        or "exit_mode_partial_be_vs_fixed" in dims
        or "max_hold_after_tp1" in dims
    ):
        if stop_pct > 0:
            stop_grid = _scaled_levels(stop_pct, (0.75, 1.0))
            max_stop = max(stop_grid)
            rr_take_grid = [round(max_stop * rr, 4) for rr in (2.0, 3.0, 4.0)]
            observed_take = round(take_pct, 4)
            take_values = set(rr_take_grid)
            if observed_take >= round(max_stop * 2.0, 4):
                take_values.add(observed_take)
            grid["stop_pct"] = stop_grid
            grid["take_pct"] = sorted(take_values)[:3]
            changes.append("bounded stop_pct + RR-safe take_pct ladder for adaptive exit retest")
        else:
            grid["take_pct"] = _scaled_levels(take_pct, (0.75, 1.0, 1.5))
            changes.append("bounded take_pct ladder around observed tp1")
    if stop_pct > 0 and "take_pct" not in grid:
        grid["stop_pct"] = _scaled_levels(stop_pct, (0.75, 1.0, 1.25))
        changes.append("nearby stop_pct to test whether stop geometry was too tight")
    return grid, changes


def _entry_grid(row: dict[str, Any], dimensions: Iterable[str]) -> tuple[dict[str, list[Any]], list[str]]:
    dims = set(str(d) for d in dimensions)
    if not {"entry_zone_width", "entry_timeout", "pretrigger_watch"} & dims:
        return {}, []
    hold = max(2, _int(row, "max_hold_bars", 5))
    return (
        {"hold_bars": sorted({hold, hold + 1, hold + 2})},
        ["longer entry/hold window to test missed fill vs invalid setup"],
    )


def _grid_size(*grids: dict[str, list[Any]]) -> int:
    total = 1
    for grid in grids:
        for values in grid.values():
            if values:
                total *= len(values)
    return total


def _middle_only(values: list[Any]) -> list[Any]:
    if not values:
        return values
    return [values[len(values) // 2]]


def _first_middle(values: list[Any]) -> list[Any]:
    if len(values) <= 2:
        return values
    out = [values[0], values[len(values) // 2]]
    return out if out[0] != out[1] else [out[0]]


def _fit_retest_budget(
    entry_grid: dict[str, list[Any]],
    exit_grid: dict[str, list[Any]],
    *,
    max_variants: int = OUTCOME_RETEST_MAX_VARIANTS,
) -> tuple[dict[str, list[Any]], dict[str, list[Any]], list[str]]:
    """Keep outcome retests executable under the farm's quiet follow-up budget."""
    entry = {key: list(values) for key, values in entry_grid.items()}
    exit_ = {key: list(values) for key, values in exit_grid.items()}
    changes: list[str] = []

    def size() -> int:
        return _grid_size(entry, exit_)

    if size() <= max_variants:
        return entry, exit_, changes

    if entry and len(exit_.get("hold_bars") or []) > 1:
        exit_["hold_bars"] = _middle_only(exit_["hold_bars"])
        changes.append("capped exit hold_bars while testing entry timing")

    if size() > max_variants and len(exit_.get("take_pct") or []) > 2:
        exit_["take_pct"] = _first_middle(exit_["take_pct"])
        changes.append("capped take_pct ladder to fit executable retest budget")

    if size() > max_variants and len(exit_.get("stop_pct") or []) > 2:
        exit_["stop_pct"] = _first_middle(exit_["stop_pct"])
        changes.append("capped stop_pct ladder to fit executable retest budget")

    if size() > max_variants and len(exit_.get("hold_bars") or []) > 2:
        exit_["hold_bars"] = _first_middle(exit_["hold_bars"])
        changes.append("capped hold_bars ladder to fit executable retest budget")

    if size() > max_variants and len(entry.get("hold_bars") or []) > 2:
        entry["hold_bars"] = _first_middle(entry["hold_bars"])
        changes.append("capped entry hold_bars ladder to fit executable retest budget")

    # Last-resort deterministic trim: keep shrinking the largest remaining axis.
    while size() > max_variants:
        candidates: list[tuple[int, str, str]] = []
        for name, grid in (("entry", entry), ("exit", exit_)):
            for key, values in grid.items():
                if len(values) > 1:
                    candidates.append((len(values), name, key))
        if not candidates:
            break
        _, name, key = max(candidates)
        target = entry if name == "entry" else exit_
        target[key] = target[key][:1]
        changes.append(f"capped {name}.{key} to one value as final budget guard")

    return entry, exit_, changes


def _sweep_for_row(row: dict[str, Any], dimensions: list[str], retest_id: str) -> tuple[SweepSpec | None, list[str], str]:
    source_family = str(row.get("family") or "")
    family = _executable_family(source_family)
    symbol = str(row.get("symbol") or "")
    timeframe = str(row.get("timeframe") or "")
    if not symbol or not timeframe or not family:
        return None, [], "missing_symbol_timeframe_or_family"
    if family not in REGISTRY:
        return None, [], "family_not_in_strategy_registry"
    exit_grid, exit_changes = _exit_grid(row, dimensions)
    entry_grid, entry_changes = _entry_grid(row, dimensions)
    if not exit_grid and not entry_grid:
        return None, [], "no_deterministic_retest_grid"
    entry_grid, exit_grid, budget_changes = _fit_retest_budget(entry_grid, exit_grid)
    mapping_note = []
    if source_family and source_family != family:
        mapping_note.append(f"mapped paper family {source_family} -> executable farm family {family}")
    sweep = SweepSpec(
        sweep_id=f"outcome_{retest_id}",
        anchor_symbol=symbol,
        related_symbols=(),
        timeframe=timeframe,
        setup_family=family,
        entry_grid=entry_grid,
        exit_grid=exit_grid,
        max_variants=OUTCOME_RETEST_MAX_VARIANTS,
        backend="cpu",
        resource_class="normal",
        private_output_policy="private_only",
        variant_tier="normal",
    )
    return sweep, mapping_note + exit_changes + entry_changes + budget_changes, ""


def _review_payload(review: dict[str, Any]) -> dict[str, Any]:
    payload = review.get("payload")
    return payload if isinstance(payload, dict) else {}


def _string_list(value: Any, *, max_items: int = 12) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return out
    for item in value:
        if isinstance(item, dict):
            text = str(item.get("dimension") or item.get("name") or item.get("test") or item.get("hypothesis") or "")
        else:
            text = str(item or "")
        text = text.strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


def _review_dimensions(payload: dict[str, Any]) -> list[str]:
    dims: list[str] = []
    for key in ("next_test_dimensions", "counterfactual_tests", "parameter_hypotheses"):
        for value in _string_list(payload.get(key)):
            if value not in dims:
                dims.append(value)
    return dims


def build_outcome_retest_specs(
    training_rows: Iterable[dict[str, Any]],
    outcome_reviews: Iterable[dict[str, Any]],
    *,
    max_specs: int = 50,
) -> list[OutcomeRetestSpec]:
    rows_by_ref = {str(row.get("training_row_id") or ""): row for row in training_rows}
    specs: list[OutcomeRetestSpec] = []
    seen: set[str] = set()
    for review in outcome_reviews:
        if len(specs) >= max(0, int(max_specs)):
            break
        if str(review.get("role_id") or "") != "outcome_reviewer" or not bool(review.get("accepted")):
            continue
        payload = _review_payload(review)
        actionability = str(payload.get("actionability") or "")
        if actionability not in _RETEST_ACTIONS:
            continue
        source_ref = str(review.get("source_ref") or "")
        row = rows_by_ref.get(source_ref)
        if not row:
            continue
        source_family = str(row.get("family") or "")
        family = _executable_family(source_family)
        dimensions = _review_dimensions(payload)
        if not dimensions:
            dimensions = ["exit_mode_partial_be_vs_fixed"] if actionability == "retest_exit_or_capture" else []
        identity = {
            "source_ref": source_ref,
            "paper_signal_id": row.get("paper_signal_id") or row.get("signal_id"),
            "actionability": actionability,
            "dimensions": dimensions,
        }
        retest_id = stable_id("ort", identity)
        if retest_id in seen:
            continue
        seen.add(retest_id)
        sweep, changes, not_queueable = _sweep_for_row(row, dimensions, retest_id)
        specs.append(
            OutcomeRetestSpec(
                retest_id=retest_id,
                review_id=str(review.get("review_id") or ""),
                source_ref=source_ref,
                paper_signal_id=str(row.get("paper_signal_id") or row.get("signal_id") or ""),
                candidate_id=str(row.get("candidate_id") or row.get("setup_candidate_id") or ""),
                symbol=str(row.get("symbol") or ""),
                timeframe=str(row.get("timeframe") or ""),
                family=family,
                source_family=source_family,
                actionability=actionability,
                outcome_bucket=str(payload.get("outcome_bucket") or ""),
                dimensions=dimensions,
                proposed_changes=changes,
                baseline=_baseline(row),
                queueable=sweep is not None,
                not_queueable_reason=not_queueable,
                sweep_spec=asdict(sweep) if sweep else None,
            )
        )
    return specs


def outcome_retest_jsonl_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "outcome_retest_specs.jsonl"


def outcome_retest_snapshot_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "outcome_retest_specs.json"


def write_outcome_retest_specs(private_root: Path, *, max_specs: int = 50) -> dict[str, Any]:
    all_specs = build_outcome_retest_specs(
        load_training_rows(private_root),
        load_outcome_reviews(private_root),
        max_specs=10_000,
    )
    completed_path = Path(private_root) / "state" / "derived" / "outcome_retest_results.json"
    try:
        completed_payload = json.loads(completed_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        completed_payload = {}
    completed_ids = {
        str(row.get("retest_id") or "")
        for row in completed_payload.get("items") or []
        if isinstance(row, dict) and str(row.get("retest_id") or "")
    }
    specs = [spec for spec in all_specs if spec.retest_id not in completed_ids][: max(0, int(max_specs))]
    by_reason: dict[str, int] = {}
    for spec in specs:
        reason = spec.not_queueable_reason or "queueable"
        by_reason[reason] = by_reason.get(reason, 0) + 1
    payload = {
        "schema": "OutcomeRetestCatalog.v1",
        "specs": len(specs),
        "eligible_total": len(all_specs),
        "completed_total": len(completed_ids),
        "remaining_total": max(0, len(all_specs) - len(completed_ids)),
        "queueable": sum(1 for spec in specs if spec.queueable),
        "by_reason": dict(sorted(by_reason.items())),
        "items": [spec.to_dict() for spec in specs],
        "paper_only": True,
        "execution_allowed": False,
    }
    out_jsonl = outcome_retest_jsonl_path(private_root)
    out_snapshot = outcome_retest_snapshot_path(private_root)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for spec in specs:
            fh.write(json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    out_snapshot.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "jsonl_path": str(out_jsonl), "snapshot_path": str(out_snapshot)}
