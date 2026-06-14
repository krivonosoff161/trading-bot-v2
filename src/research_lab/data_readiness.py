# -*- coding: utf-8 -*-
"""Data readiness check — decide if a job may run BEFORE any TA touches candles.

Thin adapter over the existing inventory: it locates the candle file that matches the
proposal's REQUESTED timeframe (`inspect_file` infers a file's timeframe from its bar
spacing) and, only when a job is event-anchored (`needs_1m_microscope`), the 1m data
(`locate_microscope_data`). Timeframe matching is case-insensitive ("1D" file == "1d"
request). It never reads candles for analysis and never fetches; a missing-for-this-
timeframe / too-short / malformed input yields a clear status + reason, so the cycle
skips the job instead of faking a result or running TA on the wrong timeframe.

Data note: missing 15m/1h/4h/1d files are reported as MISSING_DATA with an
operator prepare command rather than silently running on the wrong timeframe.
"""

from __future__ import annotations

import glob
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.data_inventory import inspect_file
from src.research_lab.event_microscope import locate_microscope_data
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


_PREPARE_1M = ("python -m scripts.strategy_lab.prepare_1m_data "
               "--symbol {symbol} --provider okx-public --apply")
_PREPARE_MARKET = ("python -m scripts.strategy_lab.prepare_market_data "
                   "--timeframe {timeframe} --symbol {symbol} --provider okx-public --apply")


def _symbol_candidate_paths(data_glob: str, symbol: str) -> list[Path]:
    """Every file the experiment loader could pick for this symbol (any timeframe)."""
    normalized = symbol.replace("-", "_").replace("/", "_")
    paths = [Path(p) for p in glob.glob(data_glob.format(symbol=normalized))]
    if not paths:
        paths = [Path(p) for p in glob.glob(data_glob.format(symbol=symbol))]
    return paths


def _prepare_hint(req: StrategyDataRequirement) -> str:
    """An honest next step for the exact missing timeframe."""
    if str(req.timeframe).strip().lower() == "1m":
        return _PREPARE_1M.format(symbol=req.symbol)
    return _PREPARE_MARKET.format(timeframe=req.timeframe, symbol=req.symbol)


def assess(
    req: StrategyDataRequirement,
    *,
    data_glob: str = DEFAULT_DATA_GLOB,
    private_root: Path | None = None,
) -> ReadinessResult:
    """Assess one requirement against the inventory, timeframe-aware. No TA, no fetch."""
    paths = _symbol_candidate_paths(data_glob, req.symbol)
    if not paths:
        return ReadinessResult(MISSING_DATA, req.symbol, req.timeframe,
                               ("primary_file_missing",), suggested_command=_prepare_hint(req))

    infos = [inspect_file(p) for p in paths]
    candle_infos = [i for i in infos if i.get("has_ohlcv")]
    if not candle_infos:
        return ReadinessResult(MALFORMED, req.symbol, req.timeframe, ("primary_file_malformed",))

    want = str(req.timeframe).strip().lower()
    matches = [i for i in candle_infos if str(i.get("timeframe", "")).lower() == want]
    if not matches:
        present = ",".join(sorted({str(i.get("timeframe", "unknown")) for i in candle_infos}))
        return ReadinessResult(MISSING_DATA, req.symbol, req.timeframe,
                               (f"no_file_for_timeframe_{req.timeframe}", f"present:{present}"),
                               suggested_command=_prepare_hint(req))

    info = max(matches, key=lambda i: int(i.get("rows") or 0))
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
            return ReadinessResult(MISSING_DATA, req.symbol, req.timeframe,
                                   (f"1m_{located.status}",),
                                   suggested_command=_PREPARE_1M.format(symbol=req.symbol))

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
    variants = (getattr(proposal, "parameter_grid", {}) or {}).get(family) or [{}]
    results: list[ReadinessResult] = []
    for symbol in getattr(proposal, "symbols", []) or []:
        # Size the requirement on the MOST demanding variant (largest lookback), so a
        # file long enough for the smallest variant but not the largest is not READY.
        reqs = [derive_requirement(family, symbol, timeframe, params=v,
                                   needs_1m_microscope=needs_1m_microscope) for v in variants]
        req = max(reqs, key=lambda r: r.min_rows)
        results.append(assess(req, data_glob=data_glob, private_root=private_root))
    if not results:
        return ProposalReadiness(MISSING_DATA, (), "")
    worst = min(results, key=lambda r: _PRECEDENCE.index(r.status) if r.status in _PRECEDENCE else 99)
    suggested = next((r.suggested_command for r in results if r.suggested_command), "")
    return ProposalReadiness(worst.status, tuple(results), suggested)
