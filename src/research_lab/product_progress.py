"""Safe product-progress checkpoints for the canonical paper-only pipeline.

Process heartbeats prove liveness, not useful work.  Producers call this module
only after a real scanner pass or farm cycle completes.  The monitor consumes
bounded aggregates and never reads market rows, recipient identities or secrets.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Mapping


SCHEMA = "ProductProgressCheckpoint.v1"
REPORT_SCHEMA = "ProductProgressReport.v1"
SAFE_COMPONENTS = frozenset({"scanner", "farm_progress", "farm"})


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def checkpoint_path(private_root: Path, component: str) -> Path:
    if component not in SAFE_COMPONENTS:
        raise ValueError("unsupported product-progress component")
    return Path(private_root) / "state" / "product_progress" / f"{component}.json"


def publish_checkpoint(
    private_root: Path,
    *,
    component: str,
    sequence: int,
    status: str,
    metrics: Mapping[str, int | float | bool | str],
    completed_at: float | None = None,
) -> dict[str, Any]:
    """Atomically publish one completed, secret-free product milestone."""
    if component not in SAFE_COMPONENTS or int(sequence) < 1:
        raise ValueError("valid component and positive sequence required")
    safe_metrics: dict[str, int | float | bool | str] = {}
    for key, value in metrics.items():
        if not isinstance(key, str) or not isinstance(value, (bool, int, float, str)):
            raise TypeError("product checkpoint metrics must be scalar")
        if any(
            marker in key.lower()
            for marker in ("path", "token", "secret", "recipient", "chat_id")
        ):
            raise ValueError("sensitive metric key is forbidden")
        safe_metrics[key] = value[:120] if isinstance(value, str) else value
    payload = {
        "schema": SCHEMA,
        "component": component,
        "sequence": int(sequence),
        "status": str(status)[:80],
        "completed_at": float(
            completed_at if completed_at is not None else time.time()
        ),
        "paper_only": True,
        "execution_allowed": False,
        "metrics": safe_metrics,
    }
    _atomic_json(checkpoint_path(private_root, component), payload)
    return payload


def scanner_metrics(
    *,
    inputs: int,
    fresh: int,
    cards: int,
    dropped: int,
    llm_failures: int,
    provider_failures: int,
) -> dict[str, int]:
    return {
        "inputs": int(inputs),
        "fresh": int(fresh),
        "cards": int(cards),
        "dropped": int(dropped),
        "llm_failures": int(llm_failures),
        "provider_failures": int(provider_failures),
    }


def farm_metrics(out: Mapping[str, Any]) -> dict[str, int | bool | str | float]:
    counters = _mapping(out.get("counters"))
    validation = _mapping(counters.get("validation"))
    generation = _mapping(out.get("paper_generation_v2"))
    bridge = _mapping(out.get("main_paper_bridge"))
    queue = _mapping(out.get("main_paper_runtime_queue"))
    observer = _mapping(out.get("main_paper_runtime_observation"))
    preview = _mapping(out.get("paper_telegram_preview"))
    delivery = _mapping(out.get("paper_telegram_delivery"))
    training = _mapping(out.get("paper_signal_training_export"))
    outcome = _mapping(out.get("outcome_retest_results"))
    outcome_generation = _mapping(outcome.get("training_evidence"))
    run_id = str(generation.get("run_id") or "")
    required_generation_refs = tuple(
        str(payload.get("paper_generation_run_id") or "")
        for payload in (bridge, queue, observer, preview, training, outcome_generation)
    )
    generation_consistent = bool(
        run_id and all(reference == run_id for reference in required_generation_refs)
    )
    return {
        "errors": len(out.get("errors") or ()),
        "events_ingested": int(counters.get("events_ingested") or 0),
        "events_consumed": int(counters.get("events_consumed") or 0),
        "validation_active": int(
            validation.get("active")
            or validation.get("backlog_active")
            or counters.get("validation_backlog_active")
            or 0
        ),
        "validation_eligible": int(
            validation.get("eligible")
            or validation.get("backlog_eligible")
            or counters.get("validation_backlog_eligible")
            or 0
        ),
        "validation_oldest_age_seconds": float(
            validation.get("oldest_age_seconds")
            or validation.get("backlog_oldest_age_seconds")
            or counters.get("validation_backlog_oldest_age_seconds")
            or 0.0
        ),
        "validation_backlog_slo_seconds": float(
            validation.get("backlog_slo_seconds") or 3600.0
        ),
        "paper_generation_run_id": run_id,
        "generation_consistent": generation_consistent,
        "bridge_instructions": int(bridge.get("instructions") or 0),
        "queue_items": int(queue.get("queued") or len(queue.get("items") or ())),
        "paper_observed": int(observer.get("observed") or 0),
        "provider_error": int(observer.get("provider_error") or 0),
        "data_gap": int(observer.get("data_gap") or 0),
        "genuine_no_market_data": int(observer.get("genuine_no_market_data") or 0),
        "preview_rendered": int(preview.get("rendered") or 0),
        "delivery_sent": int(
            delivery.get("sent_messages") or delivery.get("sent") or 0
        ),
        "delivery_errors": int(
            delivery.get("error_messages") or delivery.get("errors") or 0
        ),
        "delivery_pending": int(delivery.get("pending") or 0),
        "delivery_ack_ambiguous": int(
            delivery.get("external_ack_ambiguous") or 0
        ),
        "outcome_rows": int(
            outcome.get("rows") or len(outcome.get("items") or ())
        ),
        "training_rows": int(training.get("rows") or 0),
        "operational_rows_retained": int(
            training.get("operational_rows_retained") or 0
        ),
    }


@dataclass(frozen=True)
class ProductProgressSlo:
    scanner_seconds: float = 900.0
    farm_seconds: float = 300.0


class ProductProgressMonitor:
    """Assess real completed work separately from process liveness."""

    def __init__(
        self,
        private_root: Path,
        *,
        run_started_at: float,
        slo: ProductProgressSlo = ProductProgressSlo(),
        wall_clock: Any = time.time,
    ) -> None:
        self.private_root = Path(private_root)
        self.run_started_at = float(run_started_at)
        self.slo = slo
        self.wall_clock = wall_clock

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def sample(self) -> dict[str, Any]:
        now = float(self.wall_clock())
        components: dict[str, dict[str, Any]] = {}
        hard_fail: list[str] = []
        degraded: list[str] = []
        ready = True
        progress_row = self._read(
            checkpoint_path(self.private_root, "farm_progress")
        )
        progress_at = float(progress_row.get("completed_at") or 0.0)
        progress_current = bool(
            progress_row.get("schema") == SCHEMA
            and progress_at >= self.run_started_at
        )
        for component, limit in (
            ("scanner", self.slo.scanner_seconds),
            ("farm", self.slo.farm_seconds),
        ):
            row = self._read(checkpoint_path(self.private_root, component))
            completed_at = float(row.get("completed_at") or 0.0)
            current = (
                row.get("schema") == SCHEMA and completed_at >= self.run_started_at
            )
            age = max(0.0, now - completed_at) if completed_at else None
            if not current:
                ready = False
                if now - self.run_started_at > limit:
                    hard_fail.append(f"{component}_product_progress_startup_timeout")
            effective_age = age
            if component == "farm" and progress_current:
                effective_age = max(0.0, now - max(completed_at, progress_at))
            if current and effective_age is not None and effective_age > limit:
                hard_fail.append(f"{component}_product_progress_stale")
            metrics = _mapping(row.get("metrics"))
            if (
                component == "scanner"
                and int(metrics.get("provider_failures") or 0) > 0
            ):
                degraded.append("scanner_provider_degraded")
            if component == "farm" and current:
                oldest = float(metrics.get("validation_oldest_age_seconds") or 0.0)
                backlog_slo = float(
                    metrics.get("validation_backlog_slo_seconds") or 3600.0
                )
                if oldest > backlog_slo:
                    hard_fail.append("validation_backlog_slo_exceeded")
                if metrics.get("generation_consistent") is not True:
                    hard_fail.append("paper_generation_stage_mismatch")
                if int(metrics.get("operational_rows_retained") or 0):
                    hard_fail.append("technical_outcome_entered_training")
            components[component] = {
                "current_run": current,
                "sequence": int(row.get("sequence") or 0),
                "age_seconds": age,
                "effective_progress_age_seconds": effective_age,
                "status": str(row.get("status") or "missing"),
                "metrics": metrics,
            }
        components["farm_progress"] = {
            "current_run": progress_current,
            "sequence": int(progress_row.get("sequence") or 0),
            "age_seconds": max(0.0, now - progress_at) if progress_at else None,
            "status": str(progress_row.get("status") or "missing"),
            "metrics": _mapping(progress_row.get("metrics")),
        }
        return {
            "schema": REPORT_SCHEMA,
            "state": "failed"
            if hard_fail
            else "degraded"
            if degraded
            else "ready"
            if ready
            else "starting",
            "ready": bool(ready and not hard_fail),
            "hard_fail_reasons": hard_fail,
            "degraded_reasons": degraded,
            "components": components,
            "paper_only": True,
            "execution_allowed": False,
        }
