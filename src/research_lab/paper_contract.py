# -*- coding: utf-8 -*-
"""Paper-trading contract: farm-validated setup -> PaperTradePlan -> PaperTradeOutcome.

This module is the typed seam between the calculation farm and a FUTURE paper-trading
runtime. It is PAPER / RESEARCH ONLY and deliberately order-free: there is no field,
method, or import here that can reach a live exchange order, leverage, .env
credentials, Telegram, or the AUTO_TRADE switch.

A PaperTradePlan can be built ONLY from a SetupCard that already passed lite validation
(lite_status == FORWARD_PAPER) and hard validation (hard_status == PAPER_FORWARD_READY,
paper_forward_ready == True). Raw scanner / watch / news / intake rows have NO path into
a plan: ``plan_from_setup_card`` accepts a SetupCard instance and nothing else.

The denylist (src.exchange.okx_client, scripts.auto_execute, main, src.utils.telegram,
src.config, src.scout.scanner_v0, src.data.*_engine) is enforced by the AST
import-boundary test in tests/test_farm_loop_integration.py and tests/test_paper_contract.py.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from src.research_lab.data_fingerprint import compute_fingerprint, params_hash
from src.research_lab.hard_validation_contract import SetupCard

PAPER_CONTRACT_VERSION = "paper.v1"

# The ONLY validation verdicts that may become a paper plan.
REQUIRED_LITE_STATUS = "FORWARD_PAPER"
REQUIRED_HARD_STATUS = "PAPER_FORWARD_READY"

_VALID_DIRECTIONS = {"long", "short"}
_DEFAULT_FEES_BPS = 7.0
_DEFAULT_SLIPPAGE_BPS = 3.0
_DEFAULT_FUNDING_HANDLING = "accrue_public"
_DEFAULT_ENTRY_RULE = "next_bar_open"
_OUTCOME_SCHEMA = "PaperTradeOutcome.v1"


class PaperPlanError(ValueError):
    """Raised when a PaperTradePlan / PaperTradeOutcome cannot be built or is invalid."""


class PaperRuntimeState(str, Enum):
    """Lifecycle states of a single paper position (no look-ahead in the runtime)."""

    PLANNED = "planned"
    ARMED = "armed"
    OPENED = "opened"
    PARTIALLY_CLOSED = "partially_closed"
    CLOSED_TP = "closed_tp"
    CLOSED_SL = "closed_sl"
    CLOSED_TIMEOUT = "closed_timeout"
    CLOSED_INVALIDATION = "closed_invalidation"
    CANCELLED = "cancelled"
    ERROR = "error"


TERMINAL_STATES = frozenset(
    {
        PaperRuntimeState.CLOSED_TP,
        PaperRuntimeState.CLOSED_SL,
        PaperRuntimeState.CLOSED_TIMEOUT,
        PaperRuntimeState.CLOSED_INVALIDATION,
        PaperRuntimeState.CANCELLED,
        PaperRuntimeState.ERROR,
    }
)
_STATE_VALUES = {s.value for s in PaperRuntimeState}


# Field validators (pure; raise PaperPlanError on a missing/invalid value).

def _require_str(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PaperPlanError(f"{name} is required and must be a non-empty string")


def _require_pct_rule(name: str, rule: Any) -> None:
    if not isinstance(rule, dict):
        raise PaperPlanError(f"{name} must be a dict like {{'type':'pct','value':...}}")
    try:
        value = float(rule.get("value"))
    except (TypeError, ValueError):
        raise PaperPlanError(f"{name} needs a numeric 'value'") from None
    if value <= 0:
        raise PaperPlanError(f"{name} 'value' must be positive")


def _require_take_profit(levels: Any) -> None:
    if not isinstance(levels, list) or not levels:
        raise PaperPlanError("take_profit must be a non-empty list of pct rules")
    for level in levels:
        _require_pct_rule("take_profit level", level)


def _require_verdict(verdict: Any) -> None:
    if not isinstance(verdict, dict):
        raise PaperPlanError("source_validation_verdict must be a dict")
    if (
        verdict.get("lite") != REQUIRED_LITE_STATUS
        or verdict.get("hard") != REQUIRED_HARD_STATUS
    ):
        raise PaperPlanError(
            "source_validation_verdict must be "
            f"lite={REQUIRED_LITE_STATUS}, hard={REQUIRED_HARD_STATUS}; got {verdict!r}"
        )


# PaperTradePlan (farm -> paper).

@dataclass(frozen=True)
class PaperTradePlan:
    """An order-free, validated forward-paper plan. Built only via plan_from_setup_card."""

    # identity / join keys
    setup_id: str
    candidate_id: str
    params_hash: str
    data_fingerprint: str
    # what to trade
    symbol: str
    timeframe: str
    family: str
    direction: str
    params: dict[str, Any]
    # rules (price-level form; derived from params at fill, mirroring the farm sim)
    entry_rule: str
    stop_loss: dict[str, Any]
    take_profit: list[dict[str, Any]]
    invalidation: dict[str, Any]
    max_hold: dict[str, Any]
    # provenance gate (must be FORWARD_PAPER + PAPER_FORWARD_READY)
    source_validation_verdict: dict[str, Any]
    # costs / carry (paper model only)
    fees_bps: float = _DEFAULT_FEES_BPS
    slippage_bps: float = _DEFAULT_SLIPPAGE_BPS
    funding_handling: str = _DEFAULT_FUNDING_HANDLING
    risk_limits: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    contract_version: str = PAPER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _require_str("setup_id", self.setup_id)
        _require_str("candidate_id", self.candidate_id)
        _require_str("params_hash", self.params_hash)
        _require_str("data_fingerprint", self.data_fingerprint)
        _require_str("symbol", self.symbol)
        _require_str("timeframe", self.timeframe)
        _require_str("family", self.family)
        _require_str("entry_rule", self.entry_rule)
        if self.direction not in _VALID_DIRECTIONS:
            raise PaperPlanError(f"direction must be long|short, got {self.direction!r}")
        if not isinstance(self.params, dict) or not self.params:
            raise PaperPlanError("params must be a non-empty dict")
        _require_pct_rule("stop_loss", self.stop_loss)
        _require_take_profit(self.take_profit)
        if not isinstance(self.invalidation, dict):
            raise PaperPlanError("invalidation must be a dict")
        if not isinstance(self.max_hold, dict) or int(self.max_hold.get("bars") or 0) <= 0:
            raise PaperPlanError("max_hold must specify a positive 'bars'")
        if self.fees_bps < 0 or self.slippage_bps < 0:
            raise PaperPlanError("fees_bps and slippage_bps must be >= 0")
        _require_verdict(self.source_validation_verdict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PaperTradePlan:
        return cls(
            setup_id=str(d["setup_id"]),
            candidate_id=str(d["candidate_id"]),
            params_hash=str(d["params_hash"]),
            data_fingerprint=str(d["data_fingerprint"]),
            symbol=str(d["symbol"]),
            timeframe=str(d["timeframe"]),
            family=str(d["family"]),
            direction=str(d["direction"]),
            params=dict(d.get("params") or {}),
            entry_rule=str(d["entry_rule"]),
            stop_loss=dict(d.get("stop_loss") or {}),
            take_profit=list(d.get("take_profit") or []),
            invalidation=dict(d.get("invalidation") or {}),
            max_hold=dict(d.get("max_hold") or {}),
            source_validation_verdict=dict(d.get("source_validation_verdict") or {}),
            fees_bps=float(d.get("fees_bps", _DEFAULT_FEES_BPS)),
            slippage_bps=float(d.get("slippage_bps", _DEFAULT_SLIPPAGE_BPS)),
            funding_handling=str(d.get("funding_handling") or _DEFAULT_FUNDING_HANDLING),
            risk_limits=dict(d.get("risk_limits") or {}),
            metadata=dict(d.get("metadata") or {}),
            contract_version=str(d.get("contract_version") or PAPER_CONTRACT_VERSION),
        )


# Builder: the ONLY path into a PaperTradePlan.

def _resolve_direction(params: dict[str, Any]) -> str:
    raw = str(params.get("direction") or params.get("side") or "").lower()
    if raw not in _VALID_DIRECTIONS:
        raise PaperPlanError(
            f"SetupCard params must carry a direction/side (long|short); got {raw!r}"
        )
    return raw


def _require_param_pct(params: dict[str, Any], key: str) -> float:
    try:
        value = float(params[key])
    except (KeyError, TypeError, ValueError):
        raise PaperPlanError(f"params['{key}'] is required and must be numeric") from None
    if value <= 0:
        raise PaperPlanError(f"params['{key}'] must be positive")
    return value


def _require_param_int(params: dict[str, Any], key: str) -> int:
    try:
        value = int(params[key])
    except (KeyError, TypeError, ValueError):
        raise PaperPlanError(f"params['{key}'] is required and must be an int") from None
    if value <= 0:
        raise PaperPlanError(f"params['{key}'] must be positive")
    return value


def _data_fingerprint_from_card(card: SetupCard) -> str:
    window = card.data_window or {}
    explicit = str(window.get("fingerprint") or "")
    if explicit:
        return explicit
    # No explicit fingerprint -> reconstruct it, but only from a complete data window.
    n_bars = int(window.get("n_bars") or 0)
    start_ts = window.get("start_ts")
    end_ts = window.get("end_ts")
    if n_bars <= 0 or start_ts is None or end_ts is None:
        raise PaperPlanError(
            "SetupCard.data_window is empty or incomplete: need a non-empty "
            "'fingerprint', or n_bars>0 with start_ts and end_ts; "
            f"got {window!r}"
        )
    return compute_fingerprint(card.symbol, card.timeframe, n_bars, start_ts, end_ts)


def plan_from_setup_card(
    card: SetupCard,
    *,
    fees_bps: float = _DEFAULT_FEES_BPS,
    slippage_bps: float = _DEFAULT_SLIPPAGE_BPS,
    funding_handling: str = _DEFAULT_FUNDING_HANDLING,
    entry_rule: str = _DEFAULT_ENTRY_RULE,
    risk_limits: dict[str, Any] | None = None,
) -> PaperTradePlan:
    """Build a PaperTradePlan from a PASS SetupCard, or raise PaperPlanError.

    Rejects anything that is not a SetupCard (raw scanner/watch/news/intake) and any
    SetupCard that is not paper-forward-ready. Derives params_hash/data_fingerprint and
    the price-rule form (stop/take/hold) from the validated params; never invents data.
    """
    if not isinstance(card, SetupCard):
        raise PaperPlanError(
            "PaperTradePlan can be built only from a SetupCard; raw intake/scanner/watch "
            f"input is rejected (got {type(card).__name__})"
        )
    if not (
        card.paper_forward_ready
        and card.lite_status == REQUIRED_LITE_STATUS
        and card.hard_status == REQUIRED_HARD_STATUS
    ):
        raise PaperPlanError(
            "SetupCard is not paper-forward-ready: need paper_forward_ready=True, "
            f"lite_status={REQUIRED_LITE_STATUS}, hard_status={REQUIRED_HARD_STATUS}; got "
            f"ready={card.paper_forward_ready}, lite={card.lite_status!r}, "
            f"hard={card.hard_status!r}"
        )
    params = dict(card.params or {})
    direction = _resolve_direction(params)
    stop_pct = _require_param_pct(params, "stop_pct")
    take_pct = _require_param_pct(params, "take_pct")
    hold_bars = _require_param_int(params, "hold_bars")
    return PaperTradePlan(
        setup_id=card.setup_id,
        candidate_id=card.candidate_id,
        params_hash=params_hash(params),
        data_fingerprint=_data_fingerprint_from_card(card),
        symbol=card.symbol,
        timeframe=card.timeframe,
        family=card.strategy_id,
        direction=direction,
        params=params,
        entry_rule=entry_rule,
        stop_loss={"type": "pct", "value": stop_pct},
        take_profit=[{"type": "pct", "value": take_pct, "size_frac": 1.0}],
        invalidation={"type": "none"},
        max_hold={"bars": hold_bars},
        source_validation_verdict={"lite": card.lite_status, "hard": card.hard_status},
        fees_bps=float(fees_bps),
        slippage_bps=float(slippage_bps),
        funding_handling=str(funding_handling),
        risk_limits=dict(risk_limits or {"max_concurrent_per_symbol": 1}),
        metadata={
            "regime_tags": list(card.regime_tags or []),
            "created_at": card.created_at,
            "generated_by": "farm.setup_library",
            "data_window": dict(card.data_window or {}),
        },
    )


# PaperTradeOutcome (paper -> journal / feedback) skeleton record.

@dataclass(frozen=True)
class PaperTradeOutcome:
    """One closed/terminal paper trade. The journal + feedback record (schema v1)."""

    # identity / join keys
    trade_id: str
    setup_id: str
    candidate_id: str
    symbol: str
    timeframe: str
    family: str
    direction: str
    state: str
    # lifecycle / result (defaulted: a skeleton may be stamped incrementally)
    reason: str = ""
    outcome: str = ""
    planned_at: str = ""
    armed_at: str = ""
    opened_at: str = ""
    closed_at: str = ""
    planned_entry: float = 0.0
    actual_sim_entry: float = 0.0
    exit_price: float = 0.0
    fees_pct: float = 0.0
    slippage_pct: float = 0.0
    funding_accrued_pct: float = 0.0
    mae_pct: float = 0.0
    mfe_pct: float = 0.0
    r_multiple: float = 0.0
    net_pct: float = 0.0
    pnl_paper_pct: float = 0.0
    data_quality: str = "ok"
    data_fingerprint: str = ""
    params_hash: str = ""
    valid: bool = True
    invalid_reasons: list[str] = field(default_factory=list)
    recorded_at: str = ""
    schema: str = _OUTCOME_SCHEMA

    def __post_init__(self) -> None:
        _require_str("trade_id", self.trade_id)
        _require_str("setup_id", self.setup_id)
        _require_str("candidate_id", self.candidate_id)
        _require_str("symbol", self.symbol)
        _require_str("timeframe", self.timeframe)
        _require_str("family", self.family)
        _require_str("data_fingerprint", self.data_fingerprint)
        _require_str("params_hash", self.params_hash)
        if self.direction and self.direction not in _VALID_DIRECTIONS:
            raise PaperPlanError(f"direction must be long|short, got {self.direction!r}")
        if self.state not in _STATE_VALUES:
            raise PaperPlanError(
                f"state must be one of {sorted(_STATE_VALUES)}, got {self.state!r}"
            )
        if self.schema != _OUTCOME_SCHEMA:
            raise PaperPlanError(
                f"schema must be {_OUTCOME_SCHEMA!r}, got {self.schema!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PaperTradeOutcome:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def from_plan(
        cls,
        plan: PaperTradePlan,
        *,
        trade_id: str,
        state: PaperRuntimeState | str,
        **overrides: Any,
    ) -> PaperTradeOutcome:
        """Seed a terminal outcome from a plan, carrying the join keys forward."""
        state_value = state.value if isinstance(state, PaperRuntimeState) else str(state)
        base: dict[str, Any] = {
            "trade_id": trade_id,
            "setup_id": plan.setup_id,
            "candidate_id": plan.candidate_id,
            "symbol": plan.symbol,
            "timeframe": plan.timeframe,
            "family": plan.family,
            "direction": plan.direction,
            "state": state_value,
            "data_fingerprint": plan.data_fingerprint,
            "params_hash": plan.params_hash,
        }
        base.update(overrides)
        return cls(**base)
