"""Resource caps and skip/defer taxonomy for the paper/research backbone."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


SKIP_REASONS = {
    "missing_candles",
    "stale_data",
    "window_too_short",
    "spread_too_wide",
    "oi_unavailable",
    "provider_error",
    "llm_disabled",
    "llm_timeout",
    "llm_schema_reject",
    "known_bad_memory",
    "validator_reject",
    "manual_review_required",
    "legacy_unknown_source",
}


@dataclass(frozen=True)
class PipelineCaps:
    max_scanner_events_per_cycle: int = 50
    max_data_packets_per_cycle: int = 50
    max_feature_packets_per_cycle: int = 50
    max_llm_advisor_calls_per_cycle: int = 5
    max_candles_per_packet: int = 512
    max_telegram_previews_per_cycle: int = 20
    max_telegram_sends_per_cycle: int = 0
    max_disk_growth_mb_per_day: int = 512
    max_runtime_seconds_per_stage: int = 180
    stop_file_supported: bool = True
    backoff_defer_supported: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_caps() -> PipelineCaps:
    return PipelineCaps()


def classify_skip(reason: str) -> str:
    token = str(reason or "").strip()
    if token in SKIP_REASONS:
        return token
    if token in {"no_data", "empty_ohlcv_window"}:
        return "missing_candles"
    if token in {"short_history", "insufficient_bars", "too_short"}:
        return "window_too_short"
    if token in {"NEEDS_OI_DATA"}:
        return "oi_unavailable"
    if token in {"known_bad_in_memory", "learned_known_bad"}:
        return "known_bad_memory"
    if token.startswith("failed_validate") or token.startswith("rejected"):
        return "validator_reject"
    if token.startswith("llm_") or token == "provider_not_configured":
        return "llm_disabled"
    return token or "legacy_unknown_source"


def new_stage_counts() -> dict[str, Any]:
    return {
        "processed": 0,
        "skipped": 0,
        "deferred": 0,
        "blocked": 0,
        "reason_counts": {},
    }


def add_reason(counts: dict[str, Any], reason: str, *, bucket: str = "skipped") -> None:
    counts[bucket] = int(counts.get(bucket) or 0) + 1
    reason_counts = counts.setdefault("reason_counts", {})
    classified = classify_skip(reason)
    reason_counts[classified] = int(reason_counts.get(classified) or 0) + 1
