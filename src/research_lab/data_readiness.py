# -*- coding: utf-8 -*-
"""Data readiness check — decide if a job may run BEFORE any TA touches candles.

Thin adapter over the existing inventory: it locates the primary-timeframe file
(`choose_symbol_file` + `inspect_file`) and, only when a job is event-anchored
(`needs_1m_microscope`), the 1m data (`locate_microscope_data`). It never reads
candles for analysis and never fetches; a missing/short/malformed input yields a
clear status + reason, so the cycle can skip the job instead of faking a result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.data_inventory import inspect_file
from src.research_lab.event_microscope import locate_microscope_data
from src.research_lab.experiment import choose_symbol_file
from src.research_lab.paths import one_minute_glob
from src.research_lab.proposal_validator import DEFAULT_DATA_GLOB
from src.research_lab.strategy_requirements import StrategyDataRequirement, derive_requirement

# Readiness vocabulary (operator-facing contract)
READY = "READY"
MISSING_DATA = "MISSING_DATA"
TOO_SHORT = "TOO_SHORT"
MALFORMED = "MALFORMED"
PARTIAL_CONTEXT = "PARTIAL_CONTEXT"
OUTSIDE_POLICY = "OUTSIDE_POLICY"
NOT_READY = {MISSING_DATA, TOO_SHORT, MALFORMED, OUTSIDE_POLICY}


@dataclass(frozen=True)
class ReadinessResult:
    status: str
    symbol: str
    timeframe: str
    reasons: tuple[str, ...] = ()
    suggested_command: str = ""

    def is_ready(self) -> bool:
        # PARTIAL_CONTEXT is runnable (optional context missing); the hard-block set is NOT_READY.
        return self.status not in NOT_READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "symbol": self.symbol, "timeframe": self.timeframe,
            "reasons": list(self.reasons), "suggested_command": self.suggested_command,
        }


def assess(
    req: StrategyDataRequirement,
    *,
    data_glob: str = DEFAULT_DATA_GLOB,
    private_root: Path | None = None,
) -> ReadinessResult:
    """Assess one strategy requirement against the local inventory. No TA, no fetch."""
    path = choose_symbol_file(data_glob, req.symbol)
    if path is None:
        return ReadinessResult(MISSING_DATA, req.symbol, req.timeframe, ("primary_file_missing",))
    info = inspect_file(path)
    if info.get("quality_status") == "malformed" or not info.get("has_ohlcv"):
        return ReadinessResult(MALFORMED, req.symbol, req.timeframe, ("primary_file_malformed",))
    rows = int(info.get("rows") or 0)
    if rows < req.min_rows:
        return ReadinessResult(TOO_SHORT, req.symbol, req.timeframe,
                               (f"rows {rows} < min_rows {req.min_rows}",))

    if req.needs_1m_microscope:
        if private_root is None:
            return ReadinessResult(PARTIAL_CONTEXT, req.symbol, req.timeframe,
                                   ("1m_context_uncheckable_no_private_root",))
        located = locate_microscope_data(one_minute_glob(private_root), req.symbol)
        if located.status != "available":
            cmd = (f"python -m scripts.strategy_lab.prepare_1m_data --symbol {req.symbol} "
                   f"--provider okx-public --apply")
            return ReadinessResult(MISSING_DATA, req.symbol, req.timeframe,
                                   (f"1m_{located.status}",), suggested_command=cmd)

    return ReadinessResult(READY, req.symbol, req.timeframe)


@dataclass(frozen=True)
class ProposalReadiness:
    status: str
    per_symbol: tuple[ReadinessResult, ...] = field(default_factory=tuple)
    suggested_command: str = ""

    def is_ready(self) -> bool:
        return self.status not in NOT_READY

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "suggested_command": self.suggested_command,
                "per_symbol": [r.to_dict() for r in self.per_symbol]}


# Worst-status precedence (lower index = worse / higher priority to report).
_PRECEDENCE = (MALFORMED, MISSING_DATA, TOO_SHORT, OUTSIDE_POLICY, PARTIAL_CONTEXT, READY)


def assess_proposal(
    proposal: Any,
    *,
    data_glob: str = DEFAULT_DATA_GLOB,
    private_root: Path | None = None,
    needs_1m_microscope: bool = False,
) -> ProposalReadiness:
    """Assess all symbols of a proposal; the worst per-symbol status wins."""
    family = getattr(proposal, "setup_family", "")
    timeframe = getattr(proposal, "requested_timeframe", "")
    grid = (getattr(proposal, "parameter_grid", {}) or {}).get(family) or [{}]
    params = grid[0] if grid else {}
    results: list[ReadinessResult] = []
    for symbol in getattr(proposal, "symbols", []) or []:
        req = derive_requirement(family, symbol, timeframe, params=params,
                                 needs_1m_microscope=needs_1m_microscope)
        results.append(assess(req, data_glob=data_glob, private_root=private_root))
    if not results:
        return ProposalReadiness(MISSING_DATA, (), "")
    worst = min(results, key=lambda r: _PRECEDENCE.index(r.status) if r.status in _PRECEDENCE else 99)
    suggested = next((r.suggested_command for r in results if r.suggested_command), "")
    return ProposalReadiness(worst.status, tuple(results), suggested)
