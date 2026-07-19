from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.research_lab.adaptive_trial import (
    adaptive_trial_id,
    write_adaptive_trial_record,
)


class AdaptiveTrialTest(unittest.TestCase):
    def test_roles_share_one_trial_and_records_are_idempotent(self) -> None:
        base = {
            "schema": "RoleTaskSpec.v1",
            "kind": "bounded_sweep",
            "subject": {"symbol": "BTC", "family": "momentum_breakout"},
            "source_ref": "source:1",
            "source_content_sha256": "a" * 64,
            "producer_completion_id": "completion-1",
            "generation": 0,
        }
        trial_id = adaptive_trial_id(base)
        self.assertEqual(
            trial_id,
            adaptive_trial_id({**base, "kind": "untouched_validation"}),
        )
        self.assertEqual(
            trial_id,
            adaptive_trial_id({**base, "kind": "paper_replay"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            first = write_adaptive_trial_record(
                Path(directory),
                trial_id=trial_id,
                stage="role_candidate",
                role="farm",
                artifact_id="env-1",
            )
            second = write_adaptive_trial_record(
                Path(directory),
                trial_id=trial_id,
                stage="role_candidate",
                role="farm",
                artifact_id="env-1",
            )
            self.assertEqual(first, second)

    def test_trial_identity_requires_source_content_digest(self) -> None:
        base = {
            "schema": "RoleTaskSpec.v1",
            "kind": "bounded_sweep",
            "subject": {"symbol": "BTC", "family": "momentum_breakout"},
            "source_ref": "source:mutable",
            "generation": 0,
        }
        with self.assertRaisesRegex(ValueError, "source.*digest|content"):
            adaptive_trial_id(base)

        first = adaptive_trial_id({
            **base,
            "source_content_sha256": "a" * 64,
            "producer_completion_id": "completion-1",
        })
        changed = adaptive_trial_id({
            **base,
            "source_content_sha256": "b" * 64,
            "producer_completion_id": "completion-1",
        })
        self.assertNotEqual(first, changed)

    def test_trial_record_requires_content_bound_evidence_refs(self) -> None:
        spec = {
            "schema": "RoleTaskSpec.v1",
            "kind": "bounded_sweep",
            "subject": {"symbol": "BTC", "family": "momentum_breakout"},
            "source_ref": "source:1",
            "source_content_sha256": "a" * 64,
            "producer_completion_id": "completion-1",
            "generation": 0,
        }
        trial_id = adaptive_trial_id(spec)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "evidence.*digest|content"):
                write_adaptive_trial_record(
                    Path(directory),
                    trial_id=trial_id,
                    stage="role_candidate",
                    role="farm",
                    artifact_id="env-1",
                    evidence_refs=["mutable/path.json"],
                )


if __name__ == "__main__":
    unittest.main()
