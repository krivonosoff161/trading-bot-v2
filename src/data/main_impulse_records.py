from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs" / "main_impulse"
SIGNALS_PATH = LOG_DIR / "main_impulse_signals.jsonl"
OUTCOMES_PATH = LOG_DIR / "main_impulse_outcomes.jsonl"
TRAINING_PATH = LOG_DIR / "main_impulse_training.jsonl"


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def quality_flags(signal: dict[str, Any], outcome: dict[str, Any] | None = None) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for key in ("entry", "stop", "max_hold_min"):
        if not finite(signal.get(key)):
            reasons.append(f"bad_signal_{key}")
    if signal.get("entry", 0) <= 0 or signal.get("stop", 0) <= 0:
        reasons.append("non_positive_levels")
    if signal.get("entry") == signal.get("stop"):
        reasons.append("degenerate_stop")
    if signal.get("exit_rule", {}).get("type") not in {"ride", "scaled"}:
        reasons.append("unsupported_exit_rule")
    if outcome is not None:
        for key in ("net_pct", "mfe_pct", "mae_pct", "hold_min"):
            if not finite(outcome.get(key)):
                reasons.append(f"bad_outcome_{key}")
    return not reasons, reasons


def write_signal(row: dict[str, Any]) -> None:
    valid, reasons = quality_flags(row)
    append_jsonl(SIGNALS_PATH, {**row, "valid": valid, "invalid_reasons": reasons, "recorded_at": utc_now()})


def write_outcome(row: dict[str, Any]) -> None:
    append_jsonl(OUTCOMES_PATH, {**row, "recorded_at": utc_now()})


def write_training(signal: dict[str, Any], outcome: dict[str, Any]) -> None:
    valid, reasons = quality_flags(signal, outcome)
    append_jsonl(
        TRAINING_PATH,
        {
            "schema": "TrainingRecord.main_impulse.v1",
            "signal_id": signal["signal_id"],
            "ts": signal["ts"],
            "symbol": signal["pair"],
            "side": signal["side"],
            "features": {
                "entry": signal["entry"],
                "stop": signal["stop"],
                "impulse_body_pct": signal.get("metadata", {}).get("impulse_body_pct"),
                "body_ratio": signal.get("metadata", {}).get("body_ratio"),
                "vol_ratio": signal.get("metadata", {}).get("vol_ratio"),
                "trigger_lag_pct": signal.get("metadata", {}).get("trigger_lag_pct"),
                "exit_rule": signal.get("exit_rule"),
            },
            "outcome": {
                "label": outcome.get("outcome"),
                "net_pct": outcome.get("net_pct"),
                "mfe_pct": outcome.get("mfe_pct"),
                "mae_pct": outcome.get("mae_pct"),
                "hold_min": outcome.get("hold_min"),
            },
            "valid": valid,
            "invalid_reasons": reasons,
            "recorded_at": utc_now(),
        },
    )
