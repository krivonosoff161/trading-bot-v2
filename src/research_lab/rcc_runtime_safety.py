"""Fail-closed owner-authority state for the canonical paper RCC.

The monitor deliberately separates startup absence from authority loss.  An
old expired row is not a writer, so startup may wait for the canonical farm to
acquire a fresh lease.  Once one exact owner/fence generation has been
observed, any disappearance, expiry, competing identity, unexpected resource,
or generation change is a hard failure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from src.research_lab.canary_checkpoint_policy import CanaryMonitorHardFailure
from src.research_lab.ownership import (
    CanonicalAuthorityAssessment,
    IdentityProbe,
    ProcessIdentity,
    assess_canonical_farm_authority,
)


AuthorityRowsReader = Callable[[], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class CanonicalOwnerSafetySample:
    state: str
    ready: bool
    owner_id: str | None
    canonical_fence: int | None
    process_identity: ProcessIdentity | None
    resources: tuple[str, ...]


class CanonicalOwnerSafetyMonitor:
    """Track one canonical farm generation across startup and steady state."""

    def __init__(
        self,
        *,
        rows_reader: AuthorityRowsReader,
        identity_probe: IdentityProbe,
        startup_budget_seconds: float = 600.0,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if startup_budget_seconds <= 0:
            raise ValueError("positive owner startup budget is required")
        self._rows_reader = rows_reader
        self._identity_probe = identity_probe
        self._startup_budget_seconds = float(startup_budget_seconds)
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._started_at = float(monotonic())
        self._owner_id: str | None = None
        self._fences: dict[str, int] = {}
        self._identity: ProcessIdentity | None = None

    @property
    def ready(self) -> bool:
        return self._owner_id is not None

    def sample(self) -> CanonicalOwnerSafetySample:
        wall_now = float(self._wall_clock())
        active_rows = tuple(
            row
            for row in self._rows_reader()
            if self._is_active_row(row, now=wall_now)
        )
        assessment = assess_canonical_farm_authority(
            active_rows,
            identity_probe=self._identity_probe,
            now=wall_now,
            prior_canonical_owner_id=self._owner_id,
            prior_fences=self._fences if self._owner_id is not None else None,
        )
        if assessment.green:
            self._accept_initial_or_verify(assessment, active_rows)
            return CanonicalOwnerSafetySample(
                state="ready",
                ready=True,
                owner_id=assessment.canonical_owner_id,
                canonical_fence=assessment.canonical_fence,
                process_identity=assessment.process_identity,
                resources=assessment.resources,
            )

        if self._owner_id is None and not active_rows:
            elapsed = max(0.0, float(self._monotonic()) - self._started_at)
            if elapsed < self._startup_budget_seconds:
                return CanonicalOwnerSafetySample(
                    state="process_starting",
                    ready=False,
                    owner_id=None,
                    canonical_fence=None,
                    process_identity=None,
                    resources=(),
                )
            raise CanaryMonitorHardFailure("owner_startup_timeout")

        reason = ",".join(assessment.errors) or "owner_authority_unknown"
        raise CanaryMonitorHardFailure(f"owner_authority:{reason}")

    @staticmethod
    def _is_active_row(row: Mapping[str, Any], *, now: float) -> bool:
        try:
            return bool(row["owner_id"]) and float(row["lease_expires_at"]) > now
        except (KeyError, TypeError, ValueError):
            # Corrupt rows must reach the canonical assessment and fail closed.
            return True

    def _accept_initial_or_verify(
        self,
        assessment: CanonicalAuthorityAssessment,
        rows: Sequence[Mapping[str, Any]],
    ) -> None:
        owner_id = assessment.canonical_owner_id
        if owner_id is None:
            raise CanaryMonitorHardFailure("owner_authority:canonical_owner_missing")
        current_fences = {
            str(row["resource_id"]): int(row["next_fence"])
            for row in rows
        }
        if self._owner_id is None:
            self._owner_id = owner_id
            self._fences = current_fences
            self._identity = assessment.process_identity
            return
        if assessment.process_identity != self._identity:
            raise CanaryMonitorHardFailure(
                "owner_authority:process_generation_changed"
            )
        for resource_id, fence in current_fences.items():
            self._fences[resource_id] = max(
                fence,
                self._fences.get(resource_id, fence),
            )
