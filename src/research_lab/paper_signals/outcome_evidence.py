"""Paper outcome evidence taxonomy and training-censor boundary.

Market-data availability is operational evidence, not a paper-trade result.  This
module keeps that distinction deterministic and reusable by producers and every
adaptive consumer.  It performs no I/O and grants no execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

EVIDENCE_MARKET_OUTCOME = "market_outcome"
EVIDENCE_OPERATIONAL_INCIDENT = "operational_incident"

STATUS_USABLE = "usable"
STATUS_PROVIDER_ERROR = "provider_error"
STATUS_DATA_GAP = "data_gap"
STATUS_GENUINE_NO_MARKET_DATA = "genuine_no_market_data"

TECHNICAL_RESULTS = frozenset(
    {
        STATUS_PROVIDER_ERROR,
        STATUS_DATA_GAP,
        STATUS_GENUINE_NO_MARKET_DATA,
        "no_data",  # legacy paper rows produced before the taxonomy existed
        "provider_unavailable",
        "delivery_error",
        "timeout",
        "error",
        "failed",
    }
)
TECHNICAL_DIAGNOSES = frozenset({"data_issue", "provider_error", "data_gap"})
TECHNICAL_REASON_MARKERS = (
    "no_data_repeated",
    "provider_error",
    "provider_failure",
    "data_gap",
    "missing_market_data",
)


@dataclass(frozen=True)
class MarketDataObservation:
    rows: tuple[dict[str, Any], ...]
    status: str
    reason: str

    @property
    def usable(self) -> bool:
        return self.status == STATUS_USABLE


def classify_market_data_rows(
    rows: Iterable[dict[str, Any]] | None,
    *,
    timeframe_ms: int,
) -> MarketDataObservation:
    """Classify one successful public-data response without inventing evidence."""

    materialized = tuple(row for row in (rows or ()) if isinstance(row, dict))
    if not materialized:
        return MarketDataObservation((), STATUS_GENUINE_NO_MARKET_DATA, "provider_returned_no_rows")
    try:
        stamps = [int(row["ts"]) for row in materialized]
    except (KeyError, TypeError, ValueError):
        return MarketDataObservation(materialized, STATUS_DATA_GAP, "invalid_candle_timestamp")
    if stamps != sorted(stamps) or len(stamps) != len(set(stamps)):
        return MarketDataObservation(materialized, STATUS_DATA_GAP, "non_monotonic_candle_timestamps")
    expected = max(1, int(timeframe_ms))
    if any(right - left != expected for left, right in zip(stamps, stamps[1:])):
        return MarketDataObservation(materialized, STATUS_DATA_GAP, "non_contiguous_candle_window")
    return MarketDataObservation(materialized, STATUS_USABLE, "contiguous_market_data")


def provider_failure(reason: str = "public_provider_failure") -> MarketDataObservation:
    return MarketDataObservation((), STATUS_PROVIDER_ERROR, str(reason or "public_provider_failure"))


def evidence_kind(value: Any) -> str:
    """Classify a signal outcome or exported row; ambiguous technical rows fail closed."""

    row = value if isinstance(value, dict) else {}
    explicit = str(row.get("outcome_evidence_kind") or "").strip().lower()
    if explicit == EVIDENCE_OPERATIONAL_INCIDENT:
        return EVIDENCE_OPERATIONAL_INCIDENT
    result = str(row.get("result") or "").strip().lower()
    status = str(row.get("status") or "").strip().lower()
    diagnosis = str(row.get("diagnosis") or "").strip().lower()
    reason = str(row.get("reason") or row.get("terminal_reason") or "").strip().lower()
    data_status = str(row.get("market_data_status") or "").strip().lower()
    if (
        result in TECHNICAL_RESULTS
        or status in TECHNICAL_RESULTS
        or diagnosis in TECHNICAL_DIAGNOSES
        or data_status in TECHNICAL_RESULTS
        or any(marker in reason for marker in TECHNICAL_REASON_MARKERS)
    ):
        return EVIDENCE_OPERATIONAL_INCIDENT
    return EVIDENCE_MARKET_OUTCOME


def is_market_outcome(value: Any) -> bool:
    return evidence_kind(value) == EVIDENCE_MARKET_OUTCOME
