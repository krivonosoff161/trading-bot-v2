# -*- coding: utf-8 -*-
"""Live data-readiness selector — pick the best timeframe for a resolved instId.

Given a resolved OKX ``instId``, this counts how many CONFIRMED candles each
candidate timeframe has on the PUBLIC OKX provider and chooses the finest usable
timeframe for a research job. It is a thin classifier on top of the existing
``MarketDataProvider`` (no new downloader) and the existing inventory/readiness
vocabulary, adding the two states that ``data_readiness`` lacks for live checks:
``fresh_listing_pending`` (instrument live but too new to have history yet) and
``provider_error`` (the public fetch failed — never claim a symbol is missing
when we simply could not check).

Status vocabulary (maps onto data_readiness.READY/MISSING_DATA/TOO_SHORT):
    usable | too_short | missing | fresh_listing_pending | provider_error

Public-only, read-only. Failures are classified, never raised at the selector
boundary, so one bad symbol can never crash a scan or a farm pass.
"""
from __future__ import annotations

import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml

from src.research_lab import data_readiness as DR

_CFG = Path(__file__).resolve().parents[2] / "configs" / "strategy_lab" / "readiness_profiles.yaml"

STATUS_USABLE = "usable"
STATUS_TOO_SHORT = "too_short"
STATUS_MISSING = "missing"
STATUS_FRESH_LISTING_PENDING = "fresh_listing_pending"
STATUS_PROVIDER_ERROR = "provider_error"

# Bar interval in seconds for the provider-supported timeframes.
_TF_SECONDS = {"1m": 60, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
_FRESH_LISTING_STATES = {"preopen", "prelive", "test"}

# Map the live-selector vocabulary onto the existing data_readiness contract so
# both readiness paths report one shared meaning (do not fork a 3rd vocabulary).
LEGACY_STATUS = {
    STATUS_USABLE: DR.READY,
    STATUS_TOO_SHORT: DR.TOO_SHORT,
    STATUS_MISSING: DR.MISSING_DATA,
    STATUS_FRESH_LISTING_PENDING: DR.TOO_SHORT,
    STATUS_PROVIDER_ERROR: DR.MISSING_DATA,
}


class _Provider(Protocol):
    def fetch_ohlcv(self, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> list[dict[str, Any]]: ...


@lru_cache(maxsize=1)
def load_readiness_profiles() -> dict:
    return yaml.safe_load(_CFG.read_text(encoding="utf-8")) or {}


def min_rows_for(timeframe: str) -> int:
    rows = (load_readiness_profiles().get("min_usable_rows") or {}).get(str(timeframe).lower())
    return int(rows) if rows else 60  # 60 == data_inventory MIN_USABLE_ROWS floor


def timeframes_for_asset_class(asset_class: str | None, *, farm_only: bool = False) -> list[str]:
    cfg = load_readiness_profiles()
    if farm_only:
        return list(cfg.get("farm_timeframes") or ["15m", "1h", "4h", "1d"])
    table = cfg.get("asset_class_timeframes") or {}
    return list(table.get(str(asset_class or "unknown"), table.get("unknown", ["15m", "1h", "4h"])))


def farm_timeframes() -> list[str]:
    """Sweepable timeframes for the GPU farm (1m is trigger-only, never swept)."""
    return list(load_readiness_profiles().get("farm_timeframes") or ["15m", "1h", "4h", "1d"])


def _fresh_listing_max_age_hours() -> float:
    return float(load_readiness_profiles().get("fresh_listing_max_age_hours") or 72)


def classify_timeframe(
    provider: _Provider,
    inst_id: str,
    timeframe: str,
    *,
    min_rows: int | None = None,
    now_ms: int | None = None,
    pad: float = 1.5,
) -> dict:
    """Count confirmed candles for one timeframe and classify usable/too_short/missing/provider_error."""
    tf = str(timeframe).lower()
    need = int(min_rows if min_rows is not None else min_rows_for(tf))
    interval = _TF_SECONDS.get(tf)
    if interval is None:
        return {"timeframe": tf, "rows": 0, "status": STATUS_PROVIDER_ERROR, "reason": "unsupported_timeframe"}
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    span_bars = int(need * pad) + 2
    start_ms = now_ms - span_bars * interval * 1000
    try:
        rows = provider.fetch_ohlcv(inst_id, tf, start_ms, now_ms)
    except Exception as exc:  # MarketDataError / ValueError / network — classify, never raise
        return {"timeframe": tf, "rows": 0, "status": STATUS_PROVIDER_ERROR,
                "reason": f"provider:{type(exc).__name__}"}
    n = len(rows)
    if n >= need:
        status = STATUS_USABLE
    elif n == 0:
        status = STATUS_MISSING
    else:
        status = STATUS_TOO_SHORT
    return {"timeframe": tf, "rows": n, "status": status, "reason": f"{n}/{need}_confirmed"}


def _is_fresh_listing(list_time_ms: int | None, state: str | None, now_ms: int) -> bool:
    if str(state or "").lower() in _FRESH_LISTING_STATES:
        return True
    if list_time_ms:
        age_h = (now_ms - int(list_time_ms)) / 3_600_000
        return 0 <= age_h <= _fresh_listing_max_age_hours()
    return False


def select_timeframe(
    provider: _Provider,
    inst_id: str,
    *,
    candidate_timeframes: list[str],
    list_time_ms: int | None = None,
    listing_state: str | None = None,
    now_ms: int | None = None,
) -> dict:
    """Probe candidate timeframes (finest-first) and pick the best for a job.

    Returns {inst_id, selected_timeframe, status, rows, reason, checked}. A live but
    too-new SWAP becomes fresh_listing_pending (recheck) instead of a dead too_short.
    """
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    checked: dict[str, dict] = {}
    for tf in candidate_timeframes:
        checked[tf] = classify_timeframe(provider, inst_id, tf, now_ms=now_ms)

    usable = [tf for tf in candidate_timeframes if checked[tf]["status"] == STATUS_USABLE]
    if usable:
        tf = usable[0]
        return _result(inst_id, tf, STATUS_USABLE, checked[tf]["rows"], checked[tf]["reason"], checked)

    any_data = max(candidate_timeframes, key=lambda t: checked[t]["rows"], default=None)
    had_error = any(checked[t]["status"] == STATUS_PROVIDER_ERROR for t in candidate_timeframes)
    max_rows = checked[any_data]["rows"] if any_data else 0

    if _is_fresh_listing(list_time_ms, listing_state, now_ms) and not (had_error and max_rows == 0):
        tf = any_data or candidate_timeframes[-1]
        return _result(inst_id, tf, STATUS_FRESH_LISTING_PENDING, max_rows,
                       "live_but_insufficient_history", checked)
    if max_rows > 0:
        return _result(inst_id, any_data, STATUS_TOO_SHORT, max_rows, "insufficient_history", checked)
    if had_error:
        return _result(inst_id, None, STATUS_PROVIDER_ERROR, 0, "public_fetch_failed", checked)
    return _result(inst_id, None, STATUS_MISSING, 0, "no_candles_any_timeframe", checked)


def _result(inst_id, timeframe, status, rows, reason, checked) -> dict:
    return {
        "inst_id": inst_id,
        "selected_timeframe": timeframe,
        "status": status,
        "legacy_status": LEGACY_STATUS.get(status, DR.MISSING_DATA),
        "rows": rows,
        "reason": reason,
        "checked": checked,
    }
