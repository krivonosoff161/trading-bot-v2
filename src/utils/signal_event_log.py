"""Append-only signal decision events for product/research surfaces.

This log is not an execution journal. It records the decision package that was
shown to a user or produced by a product surface so later outcome/review jobs can
connect the exact context, artifacts, provider, and deterministic levels.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SIGNAL_EVENT_PATH = ROOT / "logs" / "signals" / "signal_events.jsonl"
SCHEMA = "signal_event.v1"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _hash_value(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:16]


def _clean_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_list(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)):
        return []
    out: list[float] = []
    for value in values:
        cleaned = _clean_float(value)
        if cleaned is not None:
            out.append(cleaned)
    return out


def _stable_signal_id(*, source: str, symbol: str, mode: str, created_at: str, decision: str) -> str:
    raw = "|".join([source, symbol, mode, created_at, decision])
    return f"{source}_{_hash_value(raw)}"


def record_signal_event(
    *,
    source: str,
    mode: str,
    decision: str,
    symbol: str = "",
    timeframe: str = "",
    signal_id: str = "",
    chat_id: str | None = None,
    message_id: int | None = None,
    created_at: str = "",
    boundary_ts: int | None = None,
    side: str | None = None,
    entry_zone: list[float] | tuple[float, ...] | None = None,
    stop_loss: float | None = None,
    take_profit_plan: list[dict[str, Any]] | None = None,
    invalidation_rule: str = "",
    max_hold_minutes: int | None = None,
    risk_pct: float | None = None,
    reason_codes: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    artifacts: dict[str, str] | None = None,
    status: str = "recorded",
    extra: dict[str, Any] | None = None,
    path: Path | None = None,
) -> Path:
    """Write one sanitized signal event row and return the target path.

    The row intentionally stores artifact references rather than raw prompts,
    screenshots, or long LLM text. Existing per-user/report files remain the
    source for full content when a private training export is built.
    """

    target = path or DEFAULT_SIGNAL_EVENT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    created = created_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sid = signal_id or _stable_signal_id(source=source, symbol=symbol, mode=mode, created_at=created, decision=decision)
    clean_entry_zone = _clean_list(entry_zone or [])
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "ts_ms": _now_ms(),
        "created_at": created,
        "source": str(source),
        "mode": str(mode),
        "decision": str(decision or "").upper(),
        "signal_id": sid,
        "symbol": str(symbol or ""),
        "timeframe": str(timeframe or ""),
        "chat_id": str(chat_id) if chat_id is not None else None,
        "message_id": message_id,
        "boundary_ts": int(boundary_ts) if boundary_ts is not None else None,
        "side": str(side).lower() if side else None,
        "entry_zone": clean_entry_zone,
        "stop_loss": _clean_float(stop_loss),
        "take_profit_plan": take_profit_plan or [],
        "invalidation_rule": str(invalidation_rule or ""),
        "max_hold_minutes": int(max_hold_minutes) if max_hold_minutes is not None else None,
        "risk_pct": _clean_float(risk_pct),
        "reason_codes": [str(x) for x in (reason_codes or []) if str(x)],
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
        "artifacts": artifacts or {},
        "status": str(status),
        "extra": extra or {},
        "paper_only": True,
        "execution_allowed": False,
    }
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return target


def record_manual_analysis_event(
    *,
    chat_id: str,
    symbol: str,
    captured_at: str,
    snapshot: dict[str, Any],
    run_dir: Path,
    summary_path: Path | None = None,
    chart_path: Path | None = None,
    report_path: Path | None = None,
    snapshot_path: Path | None = None,
    message_id: int | None = None,
    path: Path | None = None,
) -> Path:
    """Record one normalized event for Telegram manual analysis."""

    ctx = snapshot.get("llm_context") or {}
    decision = str(ctx.get("entry_signal") or "UNKNOWN").upper()
    side = ctx.get("side")
    entry_price = _clean_float(ctx.get("entry_price"))
    if entry_price is not None:
        entry_zone = [entry_price, entry_price]
    else:
        entry_zone = []
    take_profit_plan: list[dict[str, Any]] = []
    tp1 = _clean_float(ctx.get("tp1_price"))
    tp2 = _clean_float(ctx.get("tp2_price"))
    if tp1 is not None:
        take_profit_plan.append({"label": "tp1", "price": tp1, "size_frac": 0.5})
    if tp2 is not None:
        take_profit_plan.append({"label": "tp2", "price": tp2, "size_frac": 0.5})

    reason_codes = [
        decision,
        str(ctx.get("trade_style") or ctx.get("trade_style_hint") or ""),
        str(ctx.get("regime") or ""),
        str(ctx.get("drop_reason") or ""),
    ]
    artifacts = {
        "run_dir": str(run_dir),
        "summary": str(summary_path) if summary_path else "",
        "chart": str(chart_path) if chart_path else "",
        "report": str(report_path) if report_path else "",
        "snapshot": str(snapshot_path) if snapshot_path else "",
    }
    return record_signal_event(
        source="manual_telegram",
        mode="manual_analysis",
        decision=decision,
        symbol=str(snapshot.get("symbol") or symbol),
        timeframe=str(ctx.get("timeframe") or ctx.get("primary_timeframe") or "15m"),
        signal_id=str(ctx.get("signal_id") or ""),
        chat_id=str(chat_id),
        message_id=message_id,
        created_at=captured_at,
        boundary_ts=ctx.get("boundary_ts"),
        side=str(side).lower() if side else None,
        entry_zone=entry_zone,
        stop_loss=ctx.get("sl_price"),
        take_profit_plan=take_profit_plan,
        invalidation_rule=str(ctx.get("invalidation_rule") or ctx.get("drop_reason") or ""),
        max_hold_minutes=ctx.get("max_hold_minutes") or ctx.get("max_hold_min"),
        risk_pct=ctx.get("risk_pct"),
        reason_codes=reason_codes,
        artifacts=artifacts,
        status="recorded",
        extra={
            "regime": ctx.get("regime"),
            "trade_style": ctx.get("trade_style") or ctx.get("trade_style_hint"),
            "chart_is_visual_context_only": True,
        },
        path=path,
    )
