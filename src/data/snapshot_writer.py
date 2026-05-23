"""Append-only writer for full signal snapshots."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_LOG = ROOT / "logs" / "signals" / "signal_snapshot.jsonl"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _get(result: Any, name: str, default: Any = None) -> Any:
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def _ts_ms(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def write_snapshot(
    result: Any,
    signal_id: str,
    source: str,
    detected_on: str,
    payload: Mapping[str, Any] | None = None,
) -> None:
    """Write one full signal snapshot to logs/signals/signal_snapshot.jsonl."""
    payload = dict(payload or {})
    captured_at = payload.get("ts") or _get(result, "captured_at")
    record = {
        "schema_version": 1,
        "signal_id": signal_id,
        "source": source,
        "ts": captured_at,
        "ts_ms": payload.get("ts_ms") or _ts_ms(captured_at),
        "symbol": payload.get("symbol") or _get(result, "symbol"),
        "detected_on": detected_on,
        "entry_signal": payload.get("entry_signal") or _get(result, "entry_signal"),
        "side": payload.get("side") or _get(result, "side"),
        "trade_style": payload.get("trade_style") or _get(result, "trade_style"),
        "regime": payload.get("regime") or _get(result, "regime"),
        "entry": payload.get("entry") if "entry" in payload else _get(result, "entry_price"),
        "sl": payload.get("sl") if "sl" in payload else _get(result, "sl_price"),
        "tp1": payload.get("tp1") if "tp1" in payload else _get(result, "tp1_price"),
        "tp2": payload.get("tp2") if "tp2" in payload else _get(result, "tp2_price"),
        "exit_rule": payload.get("exit_rule") if "exit_rule" in payload else _get(result, "exit_rule"),
        "hold_min": payload.get("hold_min") if "hold_min" in payload else _get(result, "max_hold_min"),
        "drop_reason": _get(result, "drop_reason", ""),
        "context": _get(result, "context", {}) or {},
        "engine_vars": _get(result, "engine_vars", {}) or {},
        "indicators": _get(result, "indicators", {}) or {},
        "microstructure": _get(result, "microstructure", {}) or {},
    }
    SNAPSHOT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SNAPSHOT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_json_safe(record), ensure_ascii=False) + "\n")
