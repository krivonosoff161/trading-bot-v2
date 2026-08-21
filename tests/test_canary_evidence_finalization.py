import hashlib
import json
from pathlib import Path

import pytest

from src.research_lab.canary_evidence_finalization import (
    FINAL_REPORT_NAME,
    HANDOFF_NAME,
    MANIFEST_NAME,
    CanaryEvidenceFinalizationError,
    finalize_canary_evidence,
)


def _report() -> dict:
    return {
        "schema": "CanaryFinalReport.v1",
        "paper_only": True,
        "execution_allowed": False,
        "summary": {"state": "graceful_stop"},
    }


def test_finalization_binds_final_report_manifest_and_handoff_exactly(
    tmp_path: Path,
) -> None:
    (tmp_path / "SAFE_STATUS.json").write_text('{"state":"ok"}\n', encoding="utf-8")

    first = finalize_canary_evidence(
        tmp_path,
        _report(),
        artifact_paths=("SAFE_STATUS.json",),
    )
    second = finalize_canary_evidence(
        tmp_path,
        _report(),
        artifact_paths=("SAFE_STATUS.json",),
    )

    report_bytes = (tmp_path / FINAL_REPORT_NAME).read_bytes()
    manifest_bytes = (tmp_path / MANIFEST_NAME).read_bytes()
    handoff = json.loads((tmp_path / HANDOFF_NAME).read_text(encoding="utf-8"))
    manifest = json.loads(manifest_bytes.decode("utf-8"))

    assert first["state"] == "sealed"
    assert second["idempotent"] is True
    assert handoff["final_report_sha256"] == hashlib.sha256(report_bytes).hexdigest()
    assert handoff["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert next(
        row for row in manifest["artifacts"] if row["path"] == FINAL_REPORT_NAME
    )["sha256"] == handoff["final_report_sha256"]


@pytest.mark.parametrize("crash_step", ["report_published", "manifest_published"])
def test_finalization_resume_after_crash_never_falsely_seals(
    tmp_path: Path,
    crash_step: str,
) -> None:
    class SyntheticCrash(RuntimeError):
        pass

    def crash_after(step: str) -> None:
        if step == crash_step:
            raise SyntheticCrash(step)

    with pytest.raises(SyntheticCrash, match=crash_step):
        finalize_canary_evidence(tmp_path, _report(), after_step=crash_after)

    assert not (tmp_path / HANDOFF_NAME).exists()
    resumed = finalize_canary_evidence(tmp_path, _report())
    assert resumed["state"] == "sealed"
    assert (tmp_path / HANDOFF_NAME).exists()


def test_finalization_detects_tamper_after_seal_without_rewriting_report(
    tmp_path: Path,
) -> None:
    finalize_canary_evidence(tmp_path, _report())
    report_path = tmp_path / FINAL_REPORT_NAME
    report_path.write_text('{"tampered":true}\n', encoding="utf-8")

    with pytest.raises(CanaryEvidenceFinalizationError, match="hash mismatch"):
        finalize_canary_evidence(tmp_path, _report())

    assert report_path.read_text(encoding="utf-8") == '{"tampered":true}\n'


def test_finalization_rejects_conflicting_report_and_unsafe_artifact_path(
    tmp_path: Path,
) -> None:
    finalize_canary_evidence(tmp_path, _report())
    with pytest.raises(CanaryEvidenceFinalizationError, match="conflicts"):
        finalize_canary_evidence(
            tmp_path,
            {**_report(), "summary": {"state": "different"}},
        )

    other = tmp_path.parent / "outside.json"
    other.write_text("{}", encoding="utf-8")
    with pytest.raises(CanaryEvidenceFinalizationError, match="unsafe"):
        finalize_canary_evidence(
            tmp_path / "unsealed",
            _report(),
            artifact_paths=(str(other),),
        )
