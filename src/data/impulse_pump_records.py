from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs" / "impulse_pump"
SIGNALS_PATH = LOG_DIR / "impulse_pump_signals.jsonl"
OUTCOMES_PATH = LOG_DIR / "impulse_pump_outcomes.jsonl"
TRAINING_PATH = LOG_DIR / "impulse_pump_training.jsonl"


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def quality_flags(signal: dict[str, Any], outcome: dict[str, Any] | None = None) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for key in ["entry_price", "stop_price", "impulse_body_pct", "vol_ratio", "entry_lag_pct"]:
        if not finite(signal.get(key)):
            reasons.append(f"bad_signal_{key}")
    if signal.get("entry_price", 0) <= 0 or signal.get("stop_price", 0) <= 0:
        reasons.append("non_positive_levels")
    if abs(float(signal.get("entry_price", 0)) - float(signal.get("stop_price", 0))) <= 0:
        reasons.append("degenerate_stop")
    if outcome is not None:
        for key in ["net_pct", "mfe_pct", "mae_pct", "capture_pct", "hold_min"]:
            if not finite(outcome.get(key)):
                reasons.append(f"bad_outcome_{key}")
    return not reasons, reasons


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_signal(row: dict[str, Any]) -> None:
    valid, reasons = quality_flags(row)
    payload = {**row, "valid": valid, "invalid_reasons": reasons, "recorded_at": utc_now()}
    append_jsonl(SIGNALS_PATH, payload)


def write_outcome(row: dict[str, Any]) -> None:
    valid = row.get("valid", True)
    payload = {**row, "valid": bool(valid), "recorded_at": utc_now()}
    append_jsonl(OUTCOMES_PATH, payload)


def write_training(signal: dict[str, Any], outcome: dict[str, Any]) -> None:
    valid, reasons = quality_flags(signal, outcome)
    payload = {
        "schema": "TrainingRecord.impulse_pump.v1",
        "signal_id": signal["signal_id"],
        "ts": signal["ts"],
        "symbol": signal["symbol"],
        "side": signal["side"],
        "vol_tier": signal.get("vol_tier"),
        "features": {
            "impulse_body_pct": signal.get("impulse_body_pct"),
            "vol_ratio": signal.get("vol_ratio"),
            "trigger_window_sec": signal.get("trigger_window_sec"),
            "entry_lag_pct": signal.get("entry_lag_pct"),
            "adx_1h": signal.get("adx_1h"),
            "price": signal.get("entry_price"),
            "base_distance": signal.get("base_distance"),
            "body_ratio": signal.get("body_ratio"),
            "structure_k": signal.get("structure_k"),
        },
        "outcome": {
            "label": outcome.get("outcome"),
            "net_pct": outcome.get("net_pct"),
            "mfe_pct": outcome.get("mfe_pct"),
            "mae_pct": outcome.get("mae_pct"),
            "capture_pct": outcome.get("capture_pct"),
            "hold_min": outcome.get("hold_min"),
        },
        "valid": valid,
        "invalid_reasons": reasons,
        "recorded_at": utc_now(),
    }
    append_jsonl(TRAINING_PATH, payload)
