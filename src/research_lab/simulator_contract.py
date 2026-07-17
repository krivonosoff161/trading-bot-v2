# -*- coding: utf-8 -*-
"""Versioned, claim-bounded simulator evidence primitives.

The legacy simulator is a deterministic OHLC fixture.  The v2 helpers make
assumptions and unknowable dimensions explicit; they do not manufacture order-book
or observed-paper evidence from bars.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

MANIFEST_SCHEMA = "SimulatorAssumptionManifest.v2"
LEGACY_MODEL_ID = "deterministic_ohlc_fixture.v1"
SCENARIO_MODEL_ID = "ohlc_bar_plausibility_scenario.v2"
INCREMENTAL_PAPER_MODEL_ID = "incremental_paper_ohlc_lane.v1"
KNOWN_MODEL_IDS = {LEGACY_MODEL_ID, SCENARIO_MODEL_ID, INCREMENTAL_PAPER_MODEL_ID}


def _identity_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in manifest.items() if k != "manifest_id"}


def _manifest_id(manifest: dict[str, Any]) -> str:
    raw = json.dumps(
        _identity_payload(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sim_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def recompute_simulator_manifest_id(manifest: dict[str, Any]) -> str:
    """Recompute the content ID; canonical-policy validation is a separate gate."""
    return _manifest_id(manifest)


def _bind(manifest: dict[str, Any]) -> dict[str, Any]:
    result = dict(manifest)
    result["manifest_id"] = _manifest_id(result)
    return result


def build_simulator_assumption_manifest() -> dict[str, Any]:
    """Return the strict bar-plausibility contract; unsupported facts stay explicit."""
    return _bind({
        "schema": MANIFEST_SCHEMA,
        "simulator_model_id": SCENARIO_MODEL_ID,
        "evidence_tier": "bar_plausibility_scenario",
        "policies": {
            "entry": "next_bar_open_fixture",
            "gap": "adverse_first_tradable_open",
            "same_bar": "explicit_scenario",
            "dual_touch": "scenario_bounds_no_scalar_order",
            "costs": "separate_components",
            "liquidity": "declared_available_quantity_only",
            "funding": "timestamped_events_only",
            "account": "unavailable_without_chronological_allocation",
        },
        "unsupported_dimensions": [
            "observed_intrabar_event_order", "queue_priority", "unmodeled_depth",
            "unmodeled_impact", "observed_fill_calibration", "shared_account_capacity",
        ],
        "claim_ceiling": "bar_plausibility_scenario",
    })


def legacy_fixture_manifest() -> dict[str, Any]:
    """Describe existing scalar/GPU/reference behavior without reinterpreting it."""
    return _bind({
        "schema": MANIFEST_SCHEMA,
        "simulator_model_id": LEGACY_MODEL_ID,
        "evidence_tier": "deterministic_fixture",
        "policies": {
            "entry": "next_bar_open_fixture",
            "gap": "exact_trigger_fixture",
            "same_bar": "entry_bar_included",
            "dual_touch": "stop_first_fixture",
            "costs": "fees_plus_fixed_slippage_once",
            "liquidity": "range_touch_unbounded_fixture",
            "funding": "not_modeled",
            "account": "independent_what_if_trades",
        },
        "unsupported_dimensions": [
            "intrabar_event_order", "gap_executable_price", "spread", "liquidity",
            "partial_fill", "queue_priority", "market_impact", "funding_cashflow",
            "shared_account_capacity", "observed_fill_calibration",
        ],
        "claim_ceiling": "deterministic_fixture",
    })


def incremental_paper_lane_manifest() -> dict[str, Any]:
    """Describe the incremental lane's distinct deferred-entry-bar exit policy."""
    return _bind({
        "schema": MANIFEST_SCHEMA,
        "simulator_model_id": INCREMENTAL_PAPER_MODEL_ID,
        "evidence_tier": "deterministic_fixture",
        "policies": {
            "entry": "range_touch_fixture",
            "gap": "exact_trigger_fixture",
            "same_bar": "entry_bar_exits_deferred",
            "dual_touch": "stop_first_fixture_after_entry_bar",
            "costs": "declared_paper_cost_assumptions",
            "liquidity": "spread_volume_gate_not_fill_quantity",
            "funding": "not_modeled",
            "account": "paper_signal_independent_outcomes",
        },
        "unsupported_dimensions": [
            "entry_bar_post_fill_event_order", "gap_executable_price", "fill_quantity",
            "partial_fill", "queue_priority", "market_impact", "funding_cashflow",
            "shared_account_capacity", "observed_fill_calibration",
        ],
        "claim_ceiling": "deterministic_fixture",
    })


def validate_simulator_assumption_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise TypeError("simulator manifest must be a mapping")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("unsupported simulator manifest schema")
    if not manifest.get("simulator_model_id") or not manifest.get("evidence_tier"):
        raise ValueError("simulator model identity and evidence tier are required")
    if manifest.get("simulator_model_id") not in KNOWN_MODEL_IDS:
        raise ValueError("unknown simulator model identity")
    canonical = {
        LEGACY_MODEL_ID: legacy_fixture_manifest,
        SCENARIO_MODEL_ID: build_simulator_assumption_manifest,
        INCREMENTAL_PAPER_MODEL_ID: incremental_paper_lane_manifest,
    }[manifest["simulator_model_id"]]()
    if _identity_payload(manifest) != _identity_payload(canonical):
        raise ValueError("simulator manifest differs from canonical model contract")
    if not isinstance(manifest.get("policies"), dict):
        raise ValueError("simulator policies are required")
    if not isinstance(manifest.get("unsupported_dimensions"), list):
        raise ValueError("unsupported simulator dimensions are required")
    if manifest.get("manifest_id") != _manifest_id(manifest):
        raise ValueError("simulator manifest identity mismatch")
    return manifest


def resolve_ohlc_exit(
    side: str,
    *,
    entry_price: float,
    bar: dict[str, Any],
    stop_price: float | None,
    take_price: float | None,
) -> dict[str, Any]:
    """Resolve only what OHLC proves; represent dual-touch order as scenarios."""
    side = str(side).lower()
    if side not in {"long", "short"}:
        raise ValueError("side must be long or short")
    open_price = float(bar["open"])
    high, low = float(bar["high"]), float(bar["low"])
    adverse_gap = (
        stop_price is not None
        and ((side == "long" and open_price <= stop_price) or (side == "short" and open_price >= stop_price))
    )
    if adverse_gap:
        return {
            "status": "resolved_adverse_gap",
            "selected": {"outcome": "stop", "price": open_price},
            "scenarios": [],
            "return_bounds_pct": None,
        }
    stop_hit = stop_price is not None and (low <= stop_price if side == "long" else high >= stop_price)
    take_hit = take_price is not None and (high >= take_price if side == "long" else low <= take_price)
    if stop_hit and take_hit:
        direction = 1.0 if side == "long" else -1.0
        scenarios = [
            {"outcome": "stop", "price": float(stop_price)},
            {"outcome": "take", "price": float(take_price)},
        ]
        returns = [direction * (item["price"] / entry_price - 1.0) * 100.0 for item in scenarios]
        return {
            "status": "ambiguous_intrabar_order",
            "selected": None,
            "scenarios": scenarios,
            "return_bounds_pct": [round(min(returns), 10), round(max(returns), 10)],
        }
    if stop_hit:
        selected = {"outcome": "stop", "price": float(stop_price)}
    elif take_hit:
        selected = {"outcome": "take", "price": float(take_price)}
    else:
        selected = None
    return {"status": "resolved_single_event" if selected else "open", "selected": selected,
            "scenarios": [], "return_bounds_pct": None}


def maker_fill(
    requested_quantity: float, available_quantity: float, touch_order: str
) -> dict[str, Any]:
    requested = float(requested_quantity)
    available = float(available_quantity)
    if requested <= 0 or available < 0:
        raise ValueError("quantities must be positive/non-negative")
    if touch_order != "entry_before_exit":
        return {"status": "not_attainable_from_declared_order", "filled_quantity": 0.0,
                "remaining_quantity": requested}
    filled = min(requested, available)
    status = "no_fill" if filled == 0 else ("full_fill" if filled == requested else "partial_fill")
    return {"status": status, "filled_quantity": filled, "remaining_quantity": requested - filled}


def build_cost_ledger(
    *, fees_bps: float = 0.0, spread_bps: float = 0.0, slippage_bps: float = 0.0,
    impact_bps: float = 0.0, funding_pct: float = 0.0,
) -> dict[str, Any]:
    values = [fees_bps, spread_bps, slippage_bps, impact_bps, funding_pct]
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError("cost components must be finite")
    components = {
        "fees": round(float(fees_bps) / 100.0, 10),
        "spread": round(float(spread_bps) / 100.0, 10),
        "slippage": round(float(slippage_bps) / 100.0, 10),
        "impact": round(float(impact_bps) / 100.0, 10),
        "funding": round(float(funding_pct), 10),
    }
    return {
        "schema": "TradeCostLedger.v2",
        "components_pct": components,
        "total_pct": round(sum(components.values()), 10),
        "attribution_status": "declared_assumptions_not_observed",
    }


def build_legacy_combined_cost_ledger(cost_pct_fraction: float) -> dict[str, Any]:
    """Label an already-combined legacy deduction without inventing attribution."""
    total_pct = float(cost_pct_fraction) * 100.0
    if not math.isfinite(total_pct) or total_pct < 0:
        raise ValueError("legacy combined cost must be finite and non-negative")
    return {
        "schema": "TradeCostLedger.v2",
        "components_pct": {"legacy_fees_plus_slippage_combined": round(total_pct, 10)},
        "total_pct": round(total_pct, 10),
        "attribution_status": "legacy_combined_not_separable",
    }


def funding_cashflow(open_ts: int, close_ts: int, events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    if int(close_ts) < int(open_ts):
        raise ValueError("close_ts precedes open_ts")
    applied = [dict(event) for event in events if int(open_ts) <= int(event["ts"]) < int(close_ts)]
    total = sum(float(event["rate_pct"]) for event in applied)
    return {"schema": "FundingCashflow.v2", "events": applied, "total_pct": round(total, 10)}


def reconcile_partial_fills(
    entry_quantity: float, fills: Iterable[dict[str, Any]], *, entry_price: float,
    side: str = "long",
) -> dict[str, Any]:
    entry = float(entry_quantity)
    entry_px = float(entry_price)
    if entry <= 0 or entry_px <= 0:
        raise ValueError("entry quantity and price must be positive")
    if side not in {"long", "short"}:
        raise ValueError("side must be long or short")
    normalized = [dict(fill) for fill in fills]
    if not normalized:
        raise ValueError("at least one fill is required")
    quantities = [float(fill["quantity"]) for fill in normalized]
    if any(quantity <= 0 for quantity in quantities):
        raise ValueError("fill quantity must be positive")
    closed = sum(quantities)
    if closed > entry + 1e-12:
        raise ValueError("closed quantity exceeds entry quantity")
    for fill in normalized:
        rate = float(fill.get("cost_pct") or 0.0)
        price = float(fill["price"])
        if rate < 0 or price <= 0:
            raise ValueError("fill price and cost rate must be valid")
        fill["gross_proceeds"] = float(fill["quantity"]) * price
        fill["cost_amount"] = float(fill["quantity"]) * entry_px * rate / 100.0
        fill["net_proceeds"] = fill["gross_proceeds"] - fill["cost_amount"]
    gross_proceeds = sum(float(fill["gross_proceeds"]) for fill in normalized)
    cost_amount = sum(float(fill["cost_amount"]) for fill in normalized)
    net_proceeds = sum(float(fill["net_proceeds"]) for fill in normalized)
    closed_cost_basis = closed * entry_px
    direction = 1.0 if side == "long" else -1.0
    gross_pnl = direction * (gross_proceeds - closed_cost_basis)
    net_pnl = gross_pnl - cost_amount
    return {"schema": "QuantityReconciliation.v2", "entry_quantity": entry,
            "closed_quantity": closed, "remaining_quantity": round(entry - closed, 12),
            "entry_price": entry_px, "side": side,
            "cost_basis": "entry_notional",
            "closed_cost_basis": round(closed_cost_basis, 12),
            "gross_proceeds": round(gross_proceeds, 12),
            "cost_amount": round(cost_amount, 12),
            "net_proceeds": round(net_proceeds, 12),
            "gross_pnl": round(gross_pnl, 12), "net_pnl": round(net_pnl, 12),
            "net_return_pct": round(net_pnl / closed_cost_basis * 100.0, 10) if closed else None,
            "fills": normalized}


def build_trade_quantity_ledger(
    *, entry_quantity: float = 1.0, closed_legs: Iterable[float] = (1.0,),
) -> dict[str, Any]:
    """Bind unit-normalized fixture quantity without claiming market availability."""
    entry = float(entry_quantity)
    legs = [float(value) for value in closed_legs]
    if entry <= 0 or any(value <= 0 for value in legs):
        raise ValueError("quantity ledger values must be positive")
    closed = sum(legs)
    if closed > entry + 1e-12:
        raise ValueError("closed quantity exceeds entry quantity")
    return {
        "schema": "TradeQuantityLedger.v2",
        "basis": "normalized_unit_fixture_not_market_size",
        "entry_quantity": entry,
        "closed_legs": legs,
        "closed_quantity": closed,
        "remaining_quantity": round(entry - closed, 12),
        "availability_status": "unmodeled",
    }


def validate_trade_contract(
    trade: dict[str, Any], expected_manifest: dict[str, Any]
) -> None:
    manifest = validate_simulator_assumption_manifest(
        dict(trade.get("simulator_manifest") or {})
    )
    if manifest["manifest_id"] != expected_manifest["manifest_id"]:
        raise ValueError("trade simulator manifest mismatch")
    if trade.get("simulator_model_id") != manifest["simulator_model_id"]:
        raise ValueError("trade simulator model mismatch")
    if list(trade.get("unsupported_simulator_dimensions") or []) != manifest["unsupported_dimensions"]:
        raise ValueError("trade unsupported simulator dimensions mismatch")
    cost = dict(trade.get("cost_ledger") or {})
    if cost.get("schema") != "TradeCostLedger.v2" or not isinstance(cost.get("components_pct"), dict):
        raise ValueError("trade cost ledger is required")
    component_map = dict(cost["components_pct"])
    component_names = set(component_map)
    standard_names = {"fees", "spread", "slippage", "impact", "funding"}
    legacy_names = {"legacy_fees_plus_slippage_combined"}
    attribution_status = str(cost.get("attribution_status") or "")
    if component_names == standard_names:
        if attribution_status != "declared_assumptions_not_observed":
            raise ValueError("trade cost attribution status is not canonical")
        if manifest["policies"].get("costs") in {
            "fees_plus_fixed_slippage_once",
            "declared_paper_cost_assumptions",
        } and any(float(component_map[name]) != 0.0 for name in ("spread", "impact", "funding")):
            raise ValueError("trade cost components exceed simulator assumptions")
    elif component_names == legacy_names:
        if attribution_status != "legacy_combined_not_separable":
            raise ValueError("legacy combined cost attribution status is not canonical")
        if manifest["policies"].get("costs") != "fees_plus_fixed_slippage_once":
            raise ValueError("legacy combined costs contradict simulator policy")
    else:
        raise ValueError("trade cost component names are not canonical")
    components = [float(value) for value in component_map.values()]
    if any(not math.isfinite(value) for value in components):
        raise ValueError("trade cost components must be finite")
    total_cost = float(cost.get("total_pct") or 0.0)
    if not math.isfinite(total_cost) or abs(sum(components) - total_cost) > 1e-9:
        raise ValueError("trade cost ledger does not reconcile")
    quantity = dict(trade.get("quantity_ledger") or {})
    if quantity.get("schema") != "TradeQuantityLedger.v2":
        raise ValueError("trade quantity ledger is required")
    if (
        quantity.get("basis") != "normalized_unit_fixture_not_market_size"
        or quantity.get("availability_status") != "unmodeled"
    ):
        raise ValueError("trade quantity claims exceed normalized fixture evidence")
    entry = float(quantity.get("entry_quantity") or 0.0)
    closed = float(quantity.get("closed_quantity") or 0.0)
    remaining = float(quantity.get("remaining_quantity") or 0.0)
    legs = [float(value) for value in quantity.get("closed_legs") or []]
    if (
        entry <= 0
        or closed <= 0
        or remaining < 0
        or any(value <= 0 for value in legs)
        or abs(sum(legs) - closed) > 1e-9
        or abs(closed + remaining - entry) > 1e-9
    ):
        raise ValueError("trade quantity ledger does not reconcile")
    fraction = float(trade.get("partial_exit_fraction") or 0.0)
    reconciliation = trade.get("fill_reconciliation")
    if fraction > 0 and not isinstance(reconciliation, dict):
        raise ValueError("partial trade fill reconciliation is required")
    if isinstance(reconciliation, dict):
        if reconciliation.get("schema") != "QuantityReconciliation.v2":
            raise ValueError("invalid partial fill reconciliation")
        fills = list(reconciliation.get("fills") or [])
        canonical = reconcile_partial_fills(
            float(reconciliation.get("entry_quantity") or 0.0),
            [
                {
                    "quantity": fill.get("quantity"),
                    "price": fill.get("price"),
                    "cost_pct": fill.get("cost_pct"),
                }
                for fill in fills
            ],
            entry_price=float(reconciliation.get("entry_price") or 0.0),
            side=str(reconciliation.get("side") or ""),
        )
        if canonical != reconciliation:
            raise ValueError("partial fill reconciliation is not canonical")
        if (
            abs(float(canonical["entry_quantity"]) - entry) > 1e-9
            or [float(fill["quantity"]) for fill in canonical["fills"]] != legs
            or abs(float(canonical["closed_quantity"]) - closed) > 1e-9
            or abs(float(canonical["remaining_quantity"]) - remaining) > 1e-9
        ):
            raise ValueError("partial fill quantities do not match trade quantity ledger")
        reconciled_cost = (
            float(canonical["cost_amount"]) / float(canonical["closed_cost_basis"]) * 100.0
        )
        if abs(reconciled_cost - total_cost) > 1e-9:
            raise ValueError("partial fill costs do not match trade cost ledger")
        gross_return = (
            float(canonical["gross_pnl"]) / float(canonical["closed_cost_basis"]) * 100.0
        )
        if abs(float(canonical["net_return_pct"]) - float(trade["net_pct"])) > 1e-3:
            raise ValueError("partial fill return does not reconcile")
    else:
        entry_price = trade.get("entry", trade.get("entry_price"))
        exit_price = trade.get("exit", trade.get("exit_price"))
        if entry_price is not None and exit_price is not None and trade.get("side") in {"long", "short"}:
            entry_value, exit_value = float(entry_price), float(exit_price)
            if not all(math.isfinite(value) and value > 0 for value in (entry_value, exit_value)):
                raise ValueError("trade prices must be positive and finite")
            direction = 1.0 if trade["side"] == "long" else -1.0
            gross_return = direction * (exit_value / entry_value - 1.0) * 100.0
        elif trade.get("gross_pct") is not None:
            gross_return = float(trade["gross_pct"])
            if not math.isfinite(gross_return):
                raise ValueError("trade gross return must be finite")
        else:
            raise ValueError("trade gross return evidence is required")
    if (
        trade.get("gross_pct") is not None
        and abs(float(trade["gross_pct"]) - gross_return) > 1e-3
    ):
        raise ValueError("trade declared gross return does not reconcile")
    net_return = float(trade.get("net_pct"))
    if not math.isfinite(net_return) or abs((gross_return - total_cost) - net_return) > 1e-3:
        raise ValueError("trade net return does not reconcile to gross return and costs")


def chronological_compounded_metrics(allocation: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(allocation, dict) or allocation.get("schema") != "SharedAccountAllocation.v2":
        raise ValueError("shared account allocation evidence is required")
    capacity = float(allocation.get("account_capacity") or 0.0)
    if capacity <= 0:
        raise ValueError("account capacity must be positive")
    manifest = validate_simulator_assumption_manifest(
        dict(allocation.get("simulator_manifest") or {})
    )
    identity_payload = {k: v for k, v in allocation.items() if k != "allocation_id"}
    expected_id = "alloc_" + hashlib.sha256(json.dumps(
        identity_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if allocation.get("allocation_id") != expected_id:
        raise ValueError("shared account allocation identity mismatch")
    decisions = list(allocation.get("decisions") or [])
    replay = allocate_shared_account(
        [
            {
                "id": item.get("id"),
                "entry_ts": item.get("entry_ts"),
                "exit_ts": item.get("exit_ts"),
                "requested_capacity": item.get("requested_capacity"),
                "return_pct": item.get("return_pct"),
            }
            for item in decisions
        ],
        simulator_manifest=manifest,
        account_capacity=capacity,
    )
    if replay != allocation:
        raise ValueError("shared account allocation policy mismatch")
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    curve = [equity]
    accepted = sorted(
        (dict(item) for item in decisions if item.get("status") == "accepted"),
        key=lambda item: (int(item["exit_ts"]), int(item["entry_ts"]), str(item.get("id") or "")),
    )
    events: list[dict[str, Any]] = []
    for item in accepted:
        weight = float(item["allocated_capacity"]) / capacity
        value = float(item["return_pct"])
        equity *= 1.0 + weight * value / 100.0
        curve.append(equity)
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)
        events.append({"id": item.get("id"), "exit_ts": int(item["exit_ts"]),
                       "account_weight": weight, "return_pct": value, "equity": equity})
    return {"schema": "ChronologicalEquityMetrics.v2", "allocation_policy": allocation.get("policy"),
            "account_model_id": allocation.get("account_model_id"),
            "allocation_id": allocation["allocation_id"],
            "simulator_manifest": manifest,
            "unsupported_simulator_dimensions": manifest["unsupported_dimensions"],
            "equity_curve": curve, "events": events,
            "ending_equity": round(equity, 10),
            "total_return_pct": round((equity - 1.0) * 100.0, 10),
            "max_drawdown_pct": round(max_drawdown, 10)}


def allocate_shared_account(
    requests: Iterable[dict[str, Any]], *, simulator_manifest: dict[str, Any],
    account_capacity: float = 1.0,
) -> dict[str, Any]:
    """Chronologically reserve one shared capacity; insufficient requests are rejected."""
    capacity = float(account_capacity)
    if not math.isfinite(capacity) or capacity <= 0:
        raise ValueError("account capacity must be positive and finite")
    manifest = validate_simulator_assumption_manifest(simulator_manifest)
    active: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    ordered = sorted((dict(item) for item in requests), key=lambda item: (int(item["entry_ts"]), str(item.get("id") or "")))
    for request in ordered:
        entry_ts, exit_ts = int(request["entry_ts"]), int(request["exit_ts"])
        requested = float(request["requested_capacity"])
        return_pct = float(request.get("return_pct") or 0.0)
        if (
            exit_ts <= entry_ts
            or requested <= 0
            or not math.isfinite(requested)
            or not math.isfinite(return_pct)
        ):
            raise ValueError("account request interval and capacity must be valid")
        active = [item for item in active if int(item["exit_ts"]) > entry_ts]
        reserved = sum(float(item["allocated_capacity"]) for item in active)
        available = max(0.0, capacity - reserved)
        accepted = requested <= available + 1e-12
        allocated = requested if accepted else 0.0
        decision = {
            "id": str(request.get("id") or ""), "entry_ts": entry_ts, "exit_ts": exit_ts,
            "requested_capacity": requested, "available_capacity": round(available, 12),
            "allocated_capacity": allocated,
            "status": "accepted" if accepted else "rejected_insufficient_capacity",
            "return_pct": return_pct,
        }
        decisions.append(decision)
        if accepted:
            active.append(decision)
    result = {"schema": "SharedAccountAllocation.v2",
              "account_model_id": "shared_capacity_reject_if_insufficient.v2",
              "policy": "reject_if_insufficient", "account_capacity": capacity,
              "simulator_manifest": manifest, "decisions": decisions}
    raw = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result["allocation_id"] = "alloc_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return result


def profit_factor_state(returns_pct: Iterable[float]) -> dict[str, Any]:
    values = [float(value) for value in returns_pct]
    if not values:
        state, value = "insufficient_data", None
    else:
        gross_win = sum(value for value in values if value > 0)
        gross_loss = abs(sum(value for value in values if value < 0))
        if gross_loss > 0:
            state, value = "finite", round(gross_win / gross_loss, 10)
        elif gross_win > 0:
            state, value = "positive_infinity", None
        else:
            state, value = "undefined", None
    return {"schema": "ProfitFactorState.v2", "state": state, "value": value}
