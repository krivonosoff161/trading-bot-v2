# -*- coding: utf-8 -*-
"""Typed contracts for Strategy Lab → honest-backtest hard validation.

This module defines the stable interface between the strategy discovery
pipeline (candidates, trades, equity) and the hard validation layer
(honest-backtest).  Every field here is required or documented; JSON
round-trip is the primary serialization path.

Nothing in this module touches live trading, network, or LLM calls.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "1.0.0"

HARD_STATUSES = {
    "HARD_REJECT",
    "FAILED_OVERFIT",
    "FAILED_COSTS",
    "FAILED_FRAGILITY",
    "FAILED_OOS",
    "FAILED_DATA_QUALITY",
    "REGIME_ONLY",
    "NEEDS_MORE_DATA",
    "PAPER_FORWARD_READY",
}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


@dataclass(frozen=True)
class TradeRecord:
    side: str
    entry_price: float
    exit_price: float
    entry_ts: int
    exit_ts: int
    pnl_pct: float
    r_multiple: float = 0.0
    hold_bars: int = 0
    regime: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EquityPoint:
    ts: int
    value: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataWindow:
    start_ts: int
    end_ts: int
    n_bars: int
    timeframe: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateForValidation:
    candidate_id: str
    source_run_id: str
    symbol: str
    normalized_symbol: str
    timeframe: str
    strategy_id: str
    params: dict[str, Any]
    filters: dict[str, Any]
    fees_bps: float
    slippage_bps: float
    lite_status: str
    lite_reasons: list[str]
    risk_flags: list[str]
    metrics: dict[str, Any]
    trades: list[dict[str, Any]]
    equity_curve: list[dict[str, Any]]
    data_window: dict[str, Any]
    created_at: str
    contract_version: str = CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CandidateForValidation:
        return cls(
            candidate_id=str(d["candidate_id"]),
            source_run_id=str(d["source_run_id"]),
            symbol=str(d["symbol"]),
            normalized_symbol=str(d["normalized_symbol"]),
            timeframe=str(d["timeframe"]),
            strategy_id=str(d["strategy_id"]),
            params=dict(d.get("params") or {}),
            filters=dict(d.get("filters") or {}),
            fees_bps=float(d.get("fees_bps") or 0.0),
            slippage_bps=float(d.get("slippage_bps") or 0.0),
            lite_status=str(d.get("lite_status") or ""),
            lite_reasons=list(d.get("lite_reasons") or []),
            risk_flags=list(d.get("risk_flags") or []),
            metrics=dict(d.get("metrics") or {}),
            trades=list(d.get("trades") or []),
            equity_curve=list(d.get("equity_curve") or []),
            data_window=dict(d.get("data_window") or {}),
            created_at=str(d.get("created_at") or _utc_now()),
            contract_version=str(d.get("contract_version") or CONTRACT_VERSION),
        )


@dataclass(frozen=True)
class HardValidationCheckResult:
    check_name: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HardValidationVerdict:
    candidate_id: str
    hard_status: str
    checks: list[dict[str, Any]]
    failed_checks: list[str]
    reason_codes: list[str]
    created_at: str = ""
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.hard_status not in HARD_STATUSES:
            raise ValueError(
                f"hard_status must be one of {HARD_STATUSES}, got {self.hard_status!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HardValidationVerdict:
        return cls(
            candidate_id=str(d["candidate_id"]),
            hard_status=str(d["hard_status"]),
            checks=list(d.get("checks") or []),
            failed_checks=list(d.get("failed_checks") or []),
            reason_codes=list(d.get("reason_codes") or []),
            created_at=str(d.get("created_at") or ""),
            contract_version=str(d.get("contract_version") or CONTRACT_VERSION),
        )


@dataclass(frozen=True)
class HardValidationReport:
    candidate_id: str
    source_run_id: str
    symbol: str
    timeframe: str
    strategy_id: str
    verdict: dict[str, Any]
    checks_summary: dict[str, Any]
    created_at: str = ""
    contract_version: str = CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HardValidationReport:
        return cls(
            candidate_id=str(d["candidate_id"]),
            source_run_id=str(d.get("source_run_id") or ""),
            symbol=str(d.get("symbol") or ""),
            timeframe=str(d.get("timeframe") or ""),
            strategy_id=str(d.get("strategy_id") or ""),
            verdict=dict(d.get("verdict") or {}),
            checks_summary=dict(d.get("checks_summary") or {}),
            created_at=str(d.get("created_at") or ""),
            contract_version=str(d.get("contract_version") or CONTRACT_VERSION),
        )

    def to_markdown(self) -> str:
        v = self.verdict
        lines = [
            f"# Hard Validation Report: {self.candidate_id}",
            "",
            f"**Symbol:** {self.symbol}  **TF:** {self.timeframe}  "
            f"**Strategy:** {self.strategy_id}",
            "",
            f"**Hard Status:** `{v.get('hard_status', '?')}`",
            "",
            "## Checks",
            "",
        ]
        for check in v.get("checks", []):
            mark = "PASS" if check.get("passed") else "FAIL"
            lines.append(f"- [{mark}] {check.get('check_name', '?')}: "
                         f"{check.get('message', '')}")
        if v.get("failed_checks"):
            lines += ["", "**Failed:** " + ", ".join(v["failed_checks"])]
        lines += [
            "",
            "---",
            "*This report is a research artifact. It does not imply profitability, "
            "readiness for live trading, or approval for real funds.*",
            "",
            f"Contract version: {self.contract_version}",
        ]
        return "\n".join(lines)


@dataclass(frozen=True)
class FailureFeedback:
    candidate_id: str
    symbol: str
    timeframe: str
    strategy_id: str
    hard_status: str
    failed_checks: list[str]
    reason_codes: list[str]
    suggested_next_test_constraints: list[str]
    blocked_parameter_regions: list[dict[str, Any]]
    required_data: list[str]
    priority: str
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FailureFeedback:
        return cls(
            candidate_id=str(d["candidate_id"]),
            symbol=str(d.get("symbol") or ""),
            timeframe=str(d.get("timeframe") or ""),
            strategy_id=str(d.get("strategy_id") or ""),
            hard_status=str(d.get("hard_status") or ""),
            failed_checks=list(d.get("failed_checks") or []),
            reason_codes=list(d.get("reason_codes") or []),
            suggested_next_test_constraints=list(
                d.get("suggested_next_test_constraints") or []
            ),
            blocked_parameter_regions=list(d.get("blocked_parameter_regions") or []),
            required_data=list(d.get("required_data") or []),
            priority=str(d.get("priority") or "normal"),
            created_at=str(d.get("created_at") or _utc_now()),
        )


@dataclass(frozen=True)
class SetupCard:
    setup_id: str
    candidate_id: str
    symbol: str
    timeframe: str
    strategy_id: str
    params: dict[str, Any]
    filters: dict[str, Any]
    data_window: dict[str, Any]
    lite_status: str
    hard_status: str
    checks_summary: dict[str, Any]
    failed_checks: list[str]
    risk_flags: list[str]
    entry_exit_summary: str
    regime_tags: list[str]
    paper_forward_ready: bool = False
    main_engine_ready: bool = False
    execution_contract_version: str = CONTRACT_VERSION
    created_at: str = ""
    updated_at: str = ""
    report_path: str = ""
    original_run_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SetupCard:
        return cls(
            setup_id=str(d["setup_id"]),
            candidate_id=str(d["candidate_id"]),
            symbol=str(d.get("symbol") or ""),
            timeframe=str(d.get("timeframe") or ""),
            strategy_id=str(d.get("strategy_id") or ""),
            params=dict(d.get("params") or {}),
            filters=dict(d.get("filters") or {}),
            data_window=dict(d.get("data_window") or {}),
            lite_status=str(d.get("lite_status") or ""),
            hard_status=str(d.get("hard_status") or ""),
            checks_summary=dict(d.get("checks_summary") or {}),
            failed_checks=list(d.get("failed_checks") or []),
            risk_flags=list(d.get("risk_flags") or []),
            entry_exit_summary=str(d.get("entry_exit_summary") or ""),
            regime_tags=list(d.get("regime_tags") or []),
            paper_forward_ready=bool(d.get("paper_forward_ready") or False),
            main_engine_ready=False,
            execution_contract_version=str(
                d.get("execution_contract_version") or CONTRACT_VERSION
            ),
            created_at=str(d.get("created_at") or ""),
            updated_at=str(d.get("updated_at") or _utc_now()),
            report_path=str(d.get("report_path") or ""),
            original_run_path=str(d.get("original_run_path") or ""),
        )


@dataclass(frozen=True)
class SetupLibraryEntry:
    setup_id: str
    symbol: str
    timeframe: str
    strategy_id: str
    hard_status: str
    paper_forward_ready: bool
    main_engine_ready: bool
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
