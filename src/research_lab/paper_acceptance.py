"""Private acceptance evidence for the continuous paper/research cycle."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any

from src.research_lab.paper_account_ledger import audit_paper_account_ledger
from src.research_lab.paper_evidence_store import PaperEvidenceStore
from src.research_lab.paper_projection_reader import read_projection_view

SCHEMA = "PaperAcceptanceSnapshot.v1"
REPORT_SCHEMA = "PaperAcceptanceReport.v1"
MIN_DURATION_HOURS = 24.0


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _training_integrity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    v2 = [
        row
        for row in rows
        if row.get("lifecycle_schema") == "PaperSignalLifecycle.v2"
        and row.get("immutable_terminal_evidence") is True
    ]
    contradictions = sum(
        row.get("opened_at_bar_ts") not in (None, "") and row.get("result") == "expired_no_entry"
        for row in v2
    )
    negative_hold = sum(int(row.get("bars_held") or 0) < 0 for row in v2)
    return {
        "v2_rows": len(v2),
        "entry_expired_contradictions": contradictions,
        "negative_bars_held": negative_hold,
        "valid": contradictions == 0 and negative_hold == 0,
    }


def _card_history(derived: Path) -> dict[str, Any]:
    payload = _read_json(derived / "paper_telegram_card_ledger.json")
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    charts = [str(item.get("chart_path") or "") for item in items if isinstance(item, dict)]
    scenario_closed = sum(
        str(item.get("consumer_status") or "") == "scenario_closed"
        for item in items
        if isinstance(item, dict)
    )
    return {
        "cards": len(items),
        "scenario_closed_cards": scenario_closed,
        "charts_referenced": sum(bool(path) for path in charts),
        "charts_existing": sum(bool(path) and Path(path).exists() for path in charts),
    }


def _artifact_sizes(derived: Path) -> dict[str, int]:
    names = (
        "paper_signals.jsonl",
        "paper_signal_training.jsonl",
        "paper_account_events.jsonl",
        "trade_thesis_events.jsonl",
        "paper_telegram_card_ledger.json",
        "outcome_retest_results.json",
        "paper_lineage.jsonl",
    )
    return {name: (derived / name).stat().st_size if (derived / name).exists() else 0 for name in names}


def capture_snapshot(
    private_root: Path,
    *,
    now: float | None = None,
    evidence_database_path: Path | str | None = None,
) -> dict[str, Any]:
    private_root = Path(private_root)
    now = time.time() if now is None else float(now)
    derived = private_root / "state" / "derived"
    training = _read_jsonl(derived / "paper_signal_training.jsonl")
    thesis_events = _read_jsonl(derived / "trade_thesis_events.jsonl")
    lineage = _read_json(derived / "paper_lineage.json")
    retests = _read_json(derived / "outcome_retest_results.json")
    retest_specs = _read_json(derived / "outcome_retest_specs.json")
    calibration = _read_json(derived / "trading_policy_calibration.json")
    farm_status = _read_json(private_root / "state" / "farm_loop_status.json")
    generation = read_projection_view(
        private_root,
        "trades",
        legacy_snapshot=derived / "main_paper_trades.json",
        evidence_database_path=evidence_database_path,
    )
    if generation["authority_database_exists"]:
        run_id = str(generation.get("paper_generation_run_id") or "")
        training = [
            row
            for row in training
            if row.get("paper_generation_run_id") == run_id
            and row.get("immutable_terminal_evidence") is True
        ]
        thesis_events = []
        if not (
            lineage.get("current_generation_compatible") is True
            and lineage.get("paper_generation_run_id") == run_id
        ):
            lineage = {}
        account = PaperEvidenceStore.read_account_state(
            generation["authority_database_path"],
            account_generation_id=str(generation.get("account_generation_id") or ""),
        )
    else:
        account = audit_paper_account_ledger(private_root)
    unsafe_env = str(os.environ.get("AUTO_TRADE") or "").strip().lower() in {"1", "true", "yes", "on"}
    return {
        "schema": SCHEMA,
        "captured_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "captured_at_epoch": now,
        "lifecycle": _training_integrity(training),
        "account": account,
        "scenario": {
            "events": len(thesis_events),
            "closed_events": sum(row.get("event_type") == "scenario_closed" for row in thesis_events),
        },
        "cards": _card_history(derived),
        "lineage": {
            "envelopes": int(lineage.get("envelopes") or 0),
            "conflicts": int(lineage.get("conflicts") or 0),
            "main_without_trade": int(lineage.get("main_without_trade") or 0),
            "terminal_without_training": int(lineage.get("terminal_without_training") or 0),
            "valid": bool(lineage.get("valid")),
        },
        "retests": {
            "results": int(retests.get("results") or 0),
            "pending_specs": int(retest_specs.get("specs") or 0),
            "queueable_specs": int(retest_specs.get("queueable") or 0),
        },
        "calibration": {
            "trusted_terminal_rows": int(calibration.get("trusted_terminal_rows") or 0),
            "legacy_rows_excluded": int(calibration.get("legacy_rows_excluded") or 0),
            "ready": bool(calibration.get("calibration_ready")),
        },
        "farm": {
            "stage": str(farm_status.get("stage") or ""),
            "errors": int((farm_status.get("details") or {}).get("errors") or 0),
            "paper_only": farm_status.get("paper_only") is not False,
            "execution_allowed": bool(farm_status.get("execution_allowed")),
        },
        "artifact_sizes": _artifact_sizes(derived),
        "safety": {
            "auto_trade_env_enabled": unsafe_env,
            "paper_only": True,
            "execution_allowed": False,
        },
        "generation": {
            "paper_generation_run_id": str(
                generation.get("paper_generation_run_id") or ""
            ),
            "generation_status": str(generation.get("generation_status") or ""),
            "current_generation_compatible": bool(generation.get("current")),
            "display_only": not bool(generation.get("current")),
        },
    }


def _run_dir(private_root: Path, run_id: str) -> Path:
    return Path(private_root) / "reports" / "paper_acceptance" / run_id


def start_acceptance(
    private_root: Path,
    *,
    hours: float = MIN_DURATION_HOURS,
    now: float | None = None,
    evidence_database_path: Path | str | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else float(now)
    snapshot = capture_snapshot(
        private_root,
        now=now,
        evidence_database_path=evidence_database_path,
    )
    if snapshot["safety"]["auto_trade_env_enabled"]:
        raise RuntimeError("AUTO_TRADE is enabled in the current process environment")
    if snapshot["farm"]["execution_allowed"]:
        raise RuntimeError("farm snapshot reports execution_allowed=true")
    run_id = datetime.fromtimestamp(now, tz=timezone.utc).strftime("acceptance_%Y%m%dT%H%M%SZ")
    run_dir = _run_dir(private_root, run_id)
    run_dir.mkdir(parents=True, exist_ok=False)
    baseline = {
        "run_id": run_id,
        "required_hours": max(MIN_DURATION_HOURS, float(hours)),
        "baseline": snapshot,
        "paper_only": True,
        "execution_allowed": False,
    }
    (run_dir / "baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    active = Path(private_root) / "reports" / "paper_acceptance" / "active.json"
    active.write_text(json.dumps({"run_id": run_id, "run_dir": str(run_dir)}, indent=2) + "\n", encoding="utf-8")
    return baseline


def load_active(private_root: Path) -> tuple[dict[str, Any], Path]:
    active = _read_json(Path(private_root) / "reports" / "paper_acceptance" / "active.json")
    run_dir = Path(str(active.get("run_dir") or ""))
    baseline = _read_json(run_dir / "baseline.json") if run_dir else {}
    if not baseline:
        raise RuntimeError("no active paper acceptance run")
    return baseline, run_dir


def evaluate_acceptance(
    private_root: Path,
    *,
    now: float | None = None,
    evidence_database_path: Path | str | None = None,
) -> dict[str, Any]:
    baseline_doc, run_dir = load_active(private_root)
    baseline = baseline_doc["baseline"]
    current = capture_snapshot(
        private_root,
        now=now,
        evidence_database_path=evidence_database_path,
    )
    duration = (current["captured_at_epoch"] - baseline["captured_at_epoch"]) / 3600.0
    delta_v2 = current["lifecycle"]["v2_rows"] - baseline["lifecycle"]["v2_rows"]
    delta_closed = current["scenario"]["closed_events"] - baseline["scenario"]["closed_events"]
    delta_close_cards = current["cards"]["scenario_closed_cards"] - baseline["cards"]["scenario_closed_cards"]
    artifact_growth = {
        name: int(size) - int((baseline.get("artifact_sizes") or {}).get(name) or 0)
        for name, size in current["artifact_sizes"].items()
    }
    checks = {
        "generation_current": bool(current["generation"]["current_generation_compatible"]),
        "duration_met": duration >= float(baseline_doc["required_hours"]),
        "lifecycle_clean": current["lifecycle"]["valid"] and delta_v2 > 0,
        "account_reconciles": bool(current["account"].get("valid")),
        "scenario_closed": delta_closed > 0,
        "scenario_close_card": delta_close_cards > 0,
        "lineage_agrees": bool(current["lineage"]["valid"])
        and current["lineage"]["conflicts"] == 0
        and current["lineage"]["main_without_trade"] == 0
        and current["lineage"]["terminal_without_training"] == 0,
        "retests_progressed": current["retests"]["results"] > baseline["retests"]["results"]
        or current["retests"]["pending_specs"] == 0,
        "chart_history_reconstructible": current["cards"]["charts_existing"] >= baseline["cards"]["charts_existing"],
        "resource_growth_bounded": current["farm"]["errors"] == 0
        and all(0 <= growth <= 2 * 1024 * 1024 * 1024 for growth in artifact_growth.values()),
        "safety_clean": not current["safety"]["auto_trade_env_enabled"]
        and current["farm"]["paper_only"]
        and not current["farm"]["execution_allowed"],
    }
    report = {
        "schema": REPORT_SCHEMA,
        "run_id": baseline_doc["run_id"],
        "required_hours": baseline_doc["required_hours"],
        "duration_hours": round(duration, 4),
        "checks": checks,
        "passed": all(checks.values()),
        "deltas": {
            "trusted_lifecycle_rows": delta_v2,
            "scenario_closed_events": delta_closed,
            "scenario_closed_cards": delta_close_cards,
            "retest_results": current["retests"]["results"] - baseline["retests"]["results"],
            "artifact_growth_bytes": artifact_growth,
        },
        "baseline": baseline,
        "current": current,
        "paper_only": True,
        "execution_allowed": False,
    }
    (run_dir / "latest_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
