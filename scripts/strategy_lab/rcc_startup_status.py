"""Read-only liveness adapter for RCC startup before the first heartbeat."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import time

from src.research_lab.ownership import probe_process_identity
from src.research_lab.rcc_startup_evidence import (
    RccPreheartbeatAssessment,
    RccStartupEvidenceError,
    assess_rcc_preheartbeat_liveness,
    load_rcc_startup_evidence,
)


def assess_status(
    path: Path,
    *,
    revision: str,
    previous_attempt_id: str | None,
    startup_requested_at: float | None = None,
    now: float | None = None,
    new_attempt_grace_seconds: float = 15.0,
) -> RccPreheartbeatAssessment:
    observed_at = float(time.time() if now is None else now)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision.strip()):
        return RccPreheartbeatAssessment(
            "failed", "startup_expected_revision_invalid", None, None
        )
    if (
        not math.isfinite(observed_at)
        or not math.isfinite(float(new_attempt_grace_seconds))
        or float(new_attempt_grace_seconds) <= 0
    ):
        return RccPreheartbeatAssessment(
            "failed", "startup_liveness_policy_invalid", None, None
        )
    if startup_requested_at is not None and (
        not math.isfinite(float(startup_requested_at))
        or observed_at < float(startup_requested_at)
    ):
        return RccPreheartbeatAssessment(
            "failed", "startup_clock_regression", None, None
        )
    try:
        payload = load_rcc_startup_evidence(path)
    except RccStartupEvidenceError:
        if Path(path).exists():
            return RccPreheartbeatAssessment(
                "failed", "startup_evidence_invalid", None, None
            )
        if (
            startup_requested_at is not None
            and observed_at - float(startup_requested_at)
            >= float(new_attempt_grace_seconds)
        ):
            return RccPreheartbeatAssessment(
                "failed", "startup_evidence_missing_after_launch", None, None
            )
        return RccPreheartbeatAssessment(
            "starting", "startup_evidence_pending", None, None
        )
    try:
        identity = probe_process_identity(int(payload["pid"]))
    except Exception:
        return RccPreheartbeatAssessment(
            "failed",
            "rcc_process_identity_probe_failed",
            str(payload["attempt_id"]),
            int(payload["pid"]),
        )
    return assess_rcc_preheartbeat_liveness(
        payload,
        expected_revision=revision,
        previous_attempt_id=previous_attempt_id,
        live_process_started_at=(identity.started_at if identity is not None else None),
        startup_requested_at=startup_requested_at,
        now=observed_at,
        new_attempt_grace_seconds=new_attempt_grace_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--previous-attempt-id", default=None)
    parser.add_argument("--startup-requested-at", type=float, default=None)
    parser.add_argument("--new-attempt-grace-seconds", type=float, default=15.0)
    args = parser.parse_args()
    result = assess_status(
        args.private_root / "state" / "control-center" / "startup.json",
        revision=args.revision,
        previous_attempt_id=args.previous_attempt_id,
        startup_requested_at=args.startup_requested_at,
        new_attempt_grace_seconds=args.new_attempt_grace_seconds,
    )
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 2 if result.state == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
