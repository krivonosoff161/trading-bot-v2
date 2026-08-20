"""Safe product-progress checkpoints for the canonical paper-only pipeline.

Process heartbeats prove liveness, not useful work.  Producers call this module
only after a real scanner pass or farm cycle completes.  The monitor consumes
bounded aggregates and never reads market rows, recipient identities or secrets.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Mapping


SCHEMA = "ProductProgressCheckpoint.v1"
REPORT_SCHEMA = "ProductProgressReport.v1"
SAFE_COMPONENTS = frozenset(
    {"scanner", "scanner_progress", "farm_progress", "validation_progress", "farm"}
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_present(
    *sources: tuple[Mapping[str, Any], str],
    default: Any,
) -> Any:
    """Return the first explicitly published value, preserving valid zeroes."""

    for source, key in sources:
        if key in source and source[key] is not None:
            return source[key]
    return default


@dataclass(frozen=True)
class ProductProgressSteadyAssessment:
    """Fail-closed interpretation shared by RCC and external canary adapters."""

    state: str
    hard_failure: str | None

    @property
    def transitioning(self) -> bool:
        return self.state == "transitioning" and self.hard_failure is None


def assess_post_t0_product_progress(
    report: Mapping[str, Any],
) -> ProductProgressSteadyAssessment:
    """Classify a post-T+0 report without treating valid work as an outage.

    A validation generation may temporarily make ``ready`` false.  That is a
    safe transition only while both production components still belong to the
    current run, the generation explicitly reports that it is waiting for
    validation, and the monitor has not emitted a real SLO/invariant failure.
    The underlying :class:`ProductProgressMonitor` remains responsible for
    bounding the transition through real completed progress milestones.
    """

    if (
        report.get("schema") != REPORT_SCHEMA
        or report.get("paper_only") is not True
        or report.get("execution_allowed") is not False
    ):
        return ProductProgressSteadyAssessment(
            state="failed",
            hard_failure="product_progress_report_invalid",
        )
    reasons = list(report.get("hard_fail_reasons") or ())
    if reasons:
        return ProductProgressSteadyAssessment(
            state="failed",
            hard_failure=f"product_progress:{str(reasons[0])[:140]}",
        )
    state = str(report.get("state") or "")
    if report.get("ready") is True:
        if state not in {"ready", "degraded"}:
            return ProductProgressSteadyAssessment(
                state="failed",
                hard_failure="product_progress_report_inconsistent",
            )
        return ProductProgressSteadyAssessment(state=state, hard_failure=None)

    components = _mapping(report.get("components"))
    scanner = _mapping(components.get("scanner"))
    farm = _mapping(components.get("farm"))
    farm_metrics = _mapping(farm.get("metrics"))
    if (
        state == "starting"
        and scanner.get("current_run") is True
        and farm.get("current_run") is True
        and farm_metrics.get("paper_generation_waiting") is True
    ):
        return ProductProgressSteadyAssessment(
            state="transitioning",
            hard_failure=None,
        )
    return ProductProgressSteadyAssessment(
        state="failed",
        hard_failure="product_progress_not_ready_after_t0",
    )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        retry_delays = (0.05, 0.1, 0.2, 0.4, 0.8)
        for attempt in range(len(retry_delays) + 1):
            try:
                os.replace(temporary, path)
                return
            except OSError as exc:
                transient = isinstance(exc, PermissionError) or getattr(
                    exc, "winerror", None
                ) in {5, 32, 33}
                if not transient or attempt == len(retry_delays):
                    raise
                time.sleep(retry_delays[attempt])
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


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
    budget_exhausted: bool = False,
    resolver_deferred: int = 0,
    completed_chunks: int = 0,
    pass_elapsed_seconds: float = 0.0,
) -> dict[str, int | bool | float]:
    return {
        "inputs": int(inputs),
        "fresh": int(fresh),
        "cards": int(cards),
        "dropped": int(dropped),
        "llm_failures": int(llm_failures),
        "provider_failures": int(provider_failures),
        "budget_exhausted": bool(budget_exhausted),
        "resolver_deferred": int(resolver_deferred),
        "completed_chunks": int(completed_chunks),
        "pass_elapsed_seconds": float(pass_elapsed_seconds),
    }


def farm_metrics(out: Mapping[str, Any]) -> dict[str, int | bool | str | float]:
    counters = _mapping(out.get("counters"))
    validation = _mapping(counters.get("validation"))
    validation_backlog = _mapping(out.get("validation_backlog"))
    validation_build = _mapping(out.get("validation_generation_build"))
    scanner_intake = _mapping(out.get("scanner_intake"))
    generation = _mapping(out.get("paper_generation_v2"))
    producer_membership = _mapping(generation.get("producer_membership"))
    bridge = _mapping(out.get("main_paper_bridge"))
    queue = _mapping(out.get("main_paper_runtime_queue"))
    observer = _mapping(out.get("main_paper_runtime_observation"))
    preview = _mapping(out.get("paper_telegram_preview"))
    delivery = _mapping(out.get("paper_telegram_delivery"))
    training = _mapping(out.get("paper_signal_training_export"))
    outcome = _mapping(out.get("outcome_retest_results"))
    calculator = _mapping(out.get("calculator_advisor"))
    role_reviews = _mapping(out.get("agent_role_reviews"))
    analyst = _mapping(out.get("system_analyst_feedback"))
    analyst_training = _mapping(analyst.get("training_evidence"))
    memory = _mapping(
        out.get("setup_outcome_memory_backfill")
        or out.get("setup_outcome_memory_refresh")
    )
    reject_memory = _mapping(memory.get("reject_characterization"))
    storage = _mapping(out.get("runtime_storage_maintenance"))
    preview_tiers = _mapping(preview.get("by_validation_tier"))
    outcome_generation = _mapping(outcome.get("training_evidence"))
    run_id = str(generation.get("run_id") or "")
    generation_state = str(generation.get("state") or "")
    generation_waiting = generation_state == "waiting_validation_generation"
    required_generation_refs = tuple(
        str(payload.get("paper_generation_run_id") or "")
        for payload in (
            bridge,
            queue,
            observer,
            preview,
            delivery,
            training,
            outcome_generation,
        )
    )
    generation_consistent = bool(
        run_id and all(reference == run_id for reference in required_generation_refs)
    )
    cycle_errors = tuple(
        error for error in out.get("errors") or () if isinstance(error, Mapping)
    )
    final_product_cycle_complete = out.get("product_cycle_complete") is True
    mandatory_product_cycle_complete = bool(
        out.get("mandatory_product_cycle_complete") is True
        or final_product_cycle_complete
    )
    return {
        "errors": len(cycle_errors),
        "paper_pipeline_errors": sum(
            str(error.get("where") or "") == "paper_signals"
            for error in cycle_errors
        ),
        "events_ingested": int(counters.get("events_ingested") or 0),
        "events_consumed": int(counters.get("events_consumed") or 0),
        "scanner_uningested_events": int(
            scanner_intake.get("uningested_events") or 0
        ),
        "scanner_uningested_remaining": int(
            scanner_intake.get("remaining_after_selection") or 0
        ),
        "scanner_oldest_uningested_age_seconds": float(
            scanner_intake.get("oldest_uningested_age_seconds") or 0.0
        ),
        "validation_active": int(_first_present(
            (validation_backlog, "active"),
            (validation, "active"),
            (validation, "backlog_active"),
            (counters, "validation_backlog_active"),
            default=0,
        )),
        "validation_eligible": int(_first_present(
            (validation_backlog, "eligible"),
            (validation, "eligible"),
            (validation, "backlog_eligible"),
            (counters, "validation_backlog_eligible"),
            default=0,
        )),
        "validation_oldest_age_seconds": float(_first_present(
            (validation_backlog, "oldest_age_seconds"),
            (validation, "oldest_age_seconds"),
            (validation, "backlog_oldest_age_seconds"),
            (counters, "validation_backlog_oldest_age_seconds"),
            default=0.0,
        )),
        "validation_fresh_eligible": int(
            validation_backlog.get("fresh_eligible") or 0
        ),
        "validation_fresh_oldest_age_seconds": float(
            validation_backlog.get("fresh_oldest_eligible_age_seconds") or 0.0
        ),
        "validation_arrival_rate_per_hour": float(
            validation_backlog.get("arrival_rate_per_hour") or 0.0
        ),
        "validation_service_rate_per_hour": float(
            validation_backlog.get("service_rate_per_hour") or 0.0
        ),
        "validation_net_drain_rate_per_hour": float(
            validation_backlog.get("net_drain_rate_per_hour") or 0.0
        ),
        "validation_backlog_slo_seconds": float(
            validation_backlog.get("backlog_slo_seconds")
            or validation.get("backlog_slo_seconds")
            or 3600.0
        ),
        "paper_generation_run_id": run_id,
        "paper_generation_state": generation_state,
        "paper_generation_waiting": generation_waiting,
        "validation_generation_started_at": float(
            generation.get("validation_generation_started_at") or 0.0
        ),
        "validation_generation_build_active": bool(validation_build.get("active")),
        "validation_generation_build_started_at": float(
            validation_build.get("started_at") or 0.0
        ),
        "validation_generation_build_code_status": str(
            validation_build.get("code_status") or "absent"
        ),
        "validation_generation_status": str(
            generation.get("validation_generation_status") or ""
        ),
        "generation_consistent": generation_consistent,
        "producer_active_executable_signals": int(
            producer_membership.get("active_executable_signals") or 0
        ),
        "producer_validation_bound_members": int(
            producer_membership.get("validation_bound_members") or 0
        ),
        "producer_research_only_excluded": int(
            producer_membership.get("research_only_excluded") or 0
        ),
        "bridge_instructions": int(bridge.get("instructions") or 0),
        "queue_items": int(queue.get("queued") or len(queue.get("items") or ())),
        "paper_observed": int(observer.get("observed") or 0),
        "provider_error": int(observer.get("provider_error") or 0),
        "data_gap": int(observer.get("data_gap") or 0),
        "genuine_no_market_data": int(observer.get("genuine_no_market_data") or 0),
        "preview_rendered": int(preview.get("rendered") or 0),
        "validated_setup_cards": int(preview_tiers.get("validated_pfr") or 0),
        "research_observation_cards": int(
            preview.get("research_observation_items")
            or preview_tiers.get("farm_calculated")
            or 0
        ),
        "delivery_sent": int(
            delivery.get("sent_messages") or delivery.get("sent") or 0
        ),
        "delivery_errors": int(
            delivery.get("error_messages") or delivery.get("errors") or 0
        ),
        "delivery_pending": int(delivery.get("pending") or 0),
        "delivery_ack_ambiguous": int(
            delivery.get("external_ack_ambiguous_messages")
            or delivery.get("external_ack_ambiguous")
            or 0
        ),
        "delivery_ack_ambiguous_current": int(
            delivery.get(
                "external_ack_ambiguous_current_attempts",
                delivery.get("external_ack_ambiguous_messages")
                or delivery.get("external_ack_ambiguous")
                or 0,
            )
            or 0
        ),
        "delivery_ack_ambiguous_carried": int(
            delivery.get("external_ack_ambiguous_carried") or 0
        ),
        "analysis_llm_linked": int(preview.get("analysis_llm_linked") or 0),
        "analysis_template": int(preview.get("analysis_template") or 0),
        "analysis_fallback": int(preview.get("analysis_fallback") or 0),
        "calculator_processed": int(calculator.get("processed") or 0),
        "calculator_accepted": int(calculator.get("accepted") or 0),
        "calculator_blocked": int(calculator.get("blocked") or 0),
        "role_reviews_requested": int(role_reviews.get("reviews") or 0),
        "role_reviews_accepted": int(role_reviews.get("accepted") or 0),
        "role_reviews_rejected": int(role_reviews.get("rejected") or 0),
        "analyst_feedback_candidates": int(
            analyst.get("feedback_candidates") or 0
        ),
        "analyst_routed": int(analyst.get("routed") or 0),
        "analyst_input_rows": int(analyst_training.get("eligible_rows") or 0),
        "memory_rows": int(memory.get("product_rows") or 0),
        "memory_backfill_state": str(memory.get("state") or "not_started"),
        "memory_backfill_complete": bool(memory.get("state") == "completed"),
        "memory_terminal_rows": int(memory.get("product_terminal_rows") or 0),
        "memory_reject_cache_hits": int(reject_memory.get("cache_hits") or 0),
        "memory_reject_snapshot_bootstrap_hits": int(
            reject_memory.get("snapshot_bootstrap_hits") or 0
        ),
        "memory_reject_recomputed": int(reject_memory.get("recomputed") or 0),
        "memory_reject_cache_input_state": str(
            reject_memory.get("cache_input_state") or "absent"
        ),
        "memory_reject_cache_complete": bool(
            reject_memory.get("cache_complete")
        ),
        "memory_run_artifacts_reread": int(
            reject_memory.get("run_artifacts_reread") or 0
        ),
        "memory_run_artifacts_unavailable": int(
            reject_memory.get("run_artifacts_unavailable") or 0
        ),
        "storage_maintenance_state": str(storage.get("state") or "unknown"),
        "mandatory_product_cycle_complete": mandatory_product_cycle_complete,
        "product_cycle_complete": final_product_cycle_complete,
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
    farm_startup_max_seconds: float = 600.0
    farm_startup_progress_stale_seconds: float = 60.0
    farm_cycle_max_seconds: float = 1800.0
    scanner_intake_seconds: float = 900.0
    validation_fresh_seconds: float = 900.0
    validation_backlog_observation_seconds: float = 3600.0
    validation_generation_transition_seconds: float = 600.0


class ProductProgressTransitionError(RuntimeError):
    """A green startup sample cannot establish a safe steady-state monitor."""


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
        if not math.isfinite(self.run_started_at) or self.run_started_at <= 0.0:
            raise ValueError("run_started_at must be a finite positive timestamp")
        self.slo = slo
        self.wall_clock = wall_clock

    @classmethod
    def from_green_t0_report(
        cls,
        private_root: Path,
        *,
        t0_report: Mapping[str, Any],
        t0_observed_at: float,
        slo: ProductProgressSlo = ProductProgressSlo(),
        wall_clock: Any = time.time,
    ) -> "ProductProgressMonitor":
        """Continue one run across T+0 without rebasing its time boundary.

        Startup and steady-state adapters are separate processes in the canary
        harness.  The accepted startup report therefore carries the immutable
        launch-time boundary into the steady-state monitor.  Replacing that
        boundary with the later T+0 observation can make the very checkpoint
        that established readiness look pre-run and must fail closed.  A newer
        generation may legitimately enter the already bounded post-T+0
        ``transitioning`` state before this adapter starts; monotonic component
        sequences and the shared steady-state policy distinguish that race from
        a regression or unbounded loss of readiness.
        """

        observed_at = float(t0_observed_at)
        boundary_value = t0_report.get("run_started_at")
        if not isinstance(boundary_value, (int, float)) or isinstance(
            boundary_value, bool
        ):
            raise ProductProgressTransitionError(
                "green T+0 report has no valid run boundary"
            )
        run_started_at = float(boundary_value)
        if (
            not math.isfinite(observed_at)
            or not math.isfinite(run_started_at)
            or run_started_at <= 0.0
            or observed_at < run_started_at
        ):
            raise ProductProgressTransitionError(
                "green T+0 report has an invalid run boundary"
            )
        components = _mapping(t0_report.get("components"))
        if (
            t0_report.get("schema") != REPORT_SCHEMA
            or t0_report.get("ready") is not True
            or t0_report.get("paper_only") is not True
            or t0_report.get("execution_allowed") is not False
            or list(t0_report.get("hard_fail_reasons") or ())
            or _mapping(components.get("scanner")).get("current_run") is not True
            or _mapping(components.get("farm")).get("current_run") is not True
        ):
            raise ProductProgressTransitionError(
                "product progress was not green at T+0"
            )

        verifier = cls(
            private_root,
            run_started_at=run_started_at,
            slo=slo,
            wall_clock=lambda: observed_at,
        )
        current = verifier.sample()
        current_components = _mapping(current.get("components"))
        for component in ("scanner", "farm"):
            baseline_sequence = int(
                _mapping(components.get(component)).get("sequence") or 0
            )
            current_sequence = int(
                _mapping(current_components.get(component)).get("sequence") or 0
            )
            if baseline_sequence < 1 or current_sequence < baseline_sequence:
                raise ProductProgressTransitionError(
                    "product progress regressed during the T+0 transition"
                )
        transition = assess_post_t0_product_progress(current)
        if transition.hard_failure is not None:
            raise ProductProgressTransitionError(
                "product progress changed before the T+0 transition completed"
            )
        return cls(
            private_root,
            run_started_at=run_started_at,
            slo=slo,
            wall_clock=wall_clock,
        )

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
        progress_age = max(0.0, now - progress_at) if progress_at else None
        progress_metrics = _mapping(progress_row.get("metrics"))
        farm_startup_progress_fresh = bool(
            progress_current
            and progress_row.get("status") == "progress"
            and str(progress_metrics.get("stage") or "").strip()
            and str(progress_metrics.get("milestone") or "").strip()
            and progress_age is not None
            and progress_age <= self.slo.farm_startup_progress_stale_seconds
        )
        validation_progress_row = self._read(
            checkpoint_path(self.private_root, "validation_progress")
        )
        validation_progress_at = float(
            validation_progress_row.get("completed_at") or 0.0
        )
        validation_progress_current = bool(
            validation_progress_row.get("schema") == SCHEMA
            and validation_progress_at >= self.run_started_at
        )
        validation_progress_age = (
            max(0.0, now - validation_progress_at)
            if validation_progress_at
            else None
        )
        validation_progress_metrics = _mapping(
            validation_progress_row.get("metrics")
        )
        validation_build_progress_fresh = bool(
            validation_progress_current
            and validation_progress_row.get("status") == "progress"
            and validation_progress_metrics.get("stage") == "validation_maintenance"
            and str(validation_progress_metrics.get("milestone") or "").strip()
            and validation_progress_age is not None
            and validation_progress_age
            <= self.slo.farm_startup_progress_stale_seconds
        )
        successor_build_phase = str(
            validation_progress_metrics.get("successor_build_phase") or ""
        )
        successor_build_started_at = float(
            validation_progress_metrics.get("successor_build_started_at") or 0.0
        )
        successor_code_digest = str(
            validation_progress_metrics.get("successor_code_digest") or ""
        )
        successor_expected_digest = ""
        if successor_build_phase in {"pre_marker", "current_published"}:
            try:
                from src.research_lab.validation_generation import (
                    validation_producer_code_digest,
                )

                successor_expected_digest = validation_producer_code_digest()
            except OSError:
                successor_expected_digest = ""
        successor_code_current = bool(
            successor_expected_digest
            and successor_code_digest == successor_expected_digest
        )
        successor_current_run = bool(
            successor_build_started_at >= self.run_started_at > 0.0
        )
        successor_current_published = bool(
            successor_build_phase == "current_published"
            and str(
                validation_progress_metrics.get("successor_current_code_status") or ""
            )
            == "code_current"
            and successor_code_current
            and successor_current_run
        )
        current_publication_status = "unavailable"
        current_publication_producer_time = 0.0
        try:
            from src.research_lab.validation_generation import (
                current_generation_manifest_status,
                load_current_generation,
            )

            current_publication_status = current_generation_manifest_status(
                self.private_root
            )
            current_publication = load_current_generation(self.private_root) or {}
            current_publication_producer_time = float(
                current_publication.get("producer_time") or 0.0
            )
        except (OSError, TypeError, ValueError):
            current_publication_status = "unavailable"
            current_publication_producer_time = 0.0
        current_publication_exact_run = bool(
            current_publication_status == "code_current"
            and current_publication_producer_time >= self.run_started_at > 0.0
        )
        current_publication_within_deadline = bool(
            current_publication_producer_time > 0.0
            and max(0.0, now - current_publication_producer_time)
            <= self.slo.validation_generation_transition_seconds
        )
        successor_within_deadline = bool(
            successor_build_started_at > 0.0
            and max(0.0, now - successor_build_started_at)
            <= self.slo.validation_generation_transition_seconds
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
                startup_age = now - self.run_started_at
                bounded_farm_progress = bool(
                    component == "farm"
                    and farm_startup_progress_fresh
                    and startup_age
                    <= max(limit, self.slo.farm_startup_max_seconds)
                )
                if startup_age > limit and not bounded_farm_progress:
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
            if component == "scanner" and (
                metrics.get("budget_exhausted") is True
                or int(metrics.get("resolver_deferred") or 0) > 0
            ):
                degraded.append("scanner_bounded_work_deferred")
            if component == "farm" and current:
                if age is not None and age > self.slo.farm_cycle_max_seconds:
                    hard_fail.append("farm_product_cycle_timeout")
                intake_remaining = int(
                    metrics.get("scanner_uningested_remaining") or 0
                )
                intake_age = float(
                    metrics.get("scanner_oldest_uningested_age_seconds") or 0.0
                )
                if (
                    intake_remaining > 0
                    and intake_age > self.slo.scanner_intake_seconds
                ):
                    hard_fail.append("scanner_intake_latency_slo_exceeded")
                oldest = float(metrics.get("validation_oldest_age_seconds") or 0.0)
                backlog_slo = float(
                    metrics.get("validation_backlog_slo_seconds") or 3600.0
                )
                if oldest > backlog_slo:
                    degraded.append("validation_historical_backlog_slo_exceeded")
                    observation_age = max(0.0, now - self.run_started_at)
                    if (
                        int(metrics.get("validation_eligible") or 0) > 0
                        and observation_age
                        > self.slo.validation_backlog_observation_seconds
                    ):
                        service_rate = float(
                            metrics.get("validation_service_rate_per_hour") or 0.0
                        )
                        net_drain_rate = float(
                            metrics.get("validation_net_drain_rate_per_hour") or 0.0
                        )
                        if service_rate <= 0.0:
                            degraded.append("validation_backlog_service_stalled")
                        elif net_drain_rate <= 0.0:
                            degraded.append("validation_backlog_not_draining")
                fresh_eligible = int(metrics.get("validation_fresh_eligible") or 0)
                fresh_oldest = float(
                    metrics.get("validation_fresh_oldest_age_seconds") or 0.0
                )
                if (
                    fresh_eligible > 0
                    and fresh_oldest > self.slo.validation_fresh_seconds
                ):
                    hard_fail.append("validation_fresh_task_latency_slo_exceeded")
                generation_waiting = metrics.get("paper_generation_waiting") is True
                build_active = (
                    metrics.get("validation_generation_build_active") is True
                )
                build_started_at = float(
                    metrics.get("validation_generation_build_started_at") or 0.0
                )
                build_code_status = str(
                    metrics.get("validation_generation_build_code_status") or "absent"
                )
                build_current_run = bool(
                    build_started_at >= self.run_started_at > 0.0
                )
                build_within_deadline = bool(
                    build_started_at > 0.0
                    and max(0.0, now - build_started_at)
                    <= self.slo.validation_generation_transition_seconds
                )
                if generation_waiting:
                    ready = False
                    generation_status = str(
                        metrics.get("validation_generation_status") or ""
                    )
                    if generation_status == "code_stale":
                        if (
                            current_publication_exact_run
                            and current_publication_within_deadline
                        ):
                            degraded.append(
                                "validation_generation_publication_awaiting_farm_checkpoint"
                            )
                        elif current_publication_exact_run:
                            hard_fail.append(
                                "validation_generation_transition_timeout"
                            )
                        elif successor_current_published and successor_within_deadline:
                            degraded.append(
                                "validation_generation_publication_awaiting_farm_checkpoint"
                            )
                        elif successor_current_published:
                            hard_fail.append("validation_generation_build_timeout")
                        elif (
                            build_active
                            and build_code_status == "code_current"
                            and build_current_run
                            and build_within_deadline
                            and validation_build_progress_fresh
                        ):
                            degraded.append(
                                "validation_generation_rebuild_in_progress"
                            )
                        elif not build_active and successor_build_phase == "pre_marker":
                            if not successor_code_current:
                                hard_fail.append(
                                    "validation_generation_successor_not_current"
                                )
                            elif not successor_current_run:
                                hard_fail.append(
                                    "validation_generation_successor_not_current_run"
                                )
                            elif not successor_within_deadline:
                                hard_fail.append(
                                    "validation_generation_build_timeout"
                                )
                            elif not validation_build_progress_fresh:
                                hard_fail.append(
                                    "validation_generation_build_progress_stalled"
                                )
                            else:
                                degraded.append(
                                    "validation_generation_pre_marker_in_progress"
                                )
                        elif not build_active:
                            hard_fail.append("validation_generation_code_stale")
                        elif build_code_status != "code_current":
                            hard_fail.append(
                                "validation_generation_successor_not_current"
                            )
                        elif not build_current_run:
                            hard_fail.append(
                                "validation_generation_successor_not_current_run"
                            )
                        elif not build_within_deadline:
                            hard_fail.append("validation_generation_build_timeout")
                        else:
                            hard_fail.append(
                                "validation_generation_build_progress_stalled"
                            )
                    transition_started_at = float(
                        metrics.get("validation_generation_started_at") or 0.0
                    )
                    if generation_status != "code_stale" and transition_started_at <= 0.0:
                        hard_fail.append(
                            "validation_generation_transition_unbounded"
                        )
                    elif generation_status != "code_stale" and (
                        max(0.0, now - transition_started_at)
                        > self.slo.validation_generation_transition_seconds
                    ):
                        hard_fail.append(
                            "validation_generation_transition_timeout"
                        )
                if build_active:
                    if build_started_at <= 0.0:
                        hard_fail.append("validation_generation_build_unbounded")
                    elif (
                        max(0.0, now - build_started_at)
                        > self.slo.validation_generation_transition_seconds
                    ):
                        hard_fail.append("validation_generation_build_timeout")
                    else:
                        degraded.append("validation_generation_build_in_progress")
                if (
                    not generation_waiting
                    and metrics.get("generation_consistent") is not True
                ):
                    hard_fail.append("paper_generation_stage_mismatch")
                mandatory_complete = metrics.get(
                    "mandatory_product_cycle_complete"
                )
                if mandatory_complete is False or (
                    mandatory_complete is None
                    and metrics.get("product_cycle_complete") is False
                ):
                    ready = False
                if int(metrics.get("operational_rows_retained") or 0):
                    hard_fail.append("technical_outcome_entered_training")
                if int(metrics.get("paper_pipeline_errors") or 0):
                    hard_fail.append("paper_pipeline_cycle_failed")
                if int(metrics.get("delivery_ack_ambiguous_current") or 0):
                    hard_fail.append("telegram_delivery_ack_ambiguous")
                if str(metrics.get("storage_maintenance_state") or "") == "failed":
                    hard_fail.append("runtime_storage_maintenance_failed")
                if int(metrics.get("calculator_blocked") or 0):
                    degraded.append("calculator_advisory_degraded")
                if int(metrics.get("role_reviews_rejected") or 0):
                    degraded.append("agent_role_review_degraded")
                if (
                    int(metrics.get("validated_setup_cards") or 0) == 0
                    and int(metrics.get("research_observation_cards") or 0) > 0
                ):
                    degraded.append("no_current_validated_paper_setup")
                if int(metrics.get("analysis_fallback") or 0):
                    degraded.append("validated_card_llm_advisory_unavailable")
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
            "age_seconds": progress_age,
            "status": str(progress_row.get("status") or "missing"),
            "startup_liveness_eligible": farm_startup_progress_fresh,
            "startup_max_seconds": self.slo.farm_startup_max_seconds,
            "metrics": progress_metrics,
        }
        components["validation_progress"] = {
            "current_run": validation_progress_current,
            "sequence": int(validation_progress_row.get("sequence") or 0),
            "age_seconds": validation_progress_age,
            "status": str(validation_progress_row.get("status") or "missing"),
            "build_liveness_eligible": validation_build_progress_fresh,
            "metrics": validation_progress_metrics,
        }
        return {
            "schema": REPORT_SCHEMA,
            "run_started_at": self.run_started_at,
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
