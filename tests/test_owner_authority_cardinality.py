from __future__ import annotations

from src.research_lab.ownership import ProcessIdentity, assess_canonical_farm_authority


NOW = 100.0


def identity(
    pid: int = 101,
    *,
    started_at: float = 10.0,
    executable: str = "C:/Python/python.exe",
    command_digest: str = "sha256:farm-command",
) -> ProcessIdentity:
    return ProcessIdentity(pid, started_at, executable, command_digest)


def row(
    resource_id: str,
    role_id: str,
    owner_id: str,
    process: ProcessIdentity,
    *,
    fence: int,
    expires_at: float = 200.0,
) -> dict[str, object]:
    return {
        "resource_id": resource_id,
        "role_id": role_id,
        "owner_id": owner_id,
        "pid": process.pid,
        "started_at": process.started_at,
        "executable": process.executable,
        "command_digest": process.command_digest,
        "lease_expires_at": expires_at,
        "next_fence": fence,
    }


def assess(rows, live, **kwargs):
    return assess_canonical_farm_authority(
        rows,
        identity_probe=lambda pid: live.get(pid),
        now=NOW,
        **kwargs,
    )


def test_same_process_canonical_and_nested_worker_are_one_authority() -> None:
    process = identity()
    result = assess(
        [
            row("canonical_farm", "farm", "farm-owner", process, fence=10),
            row("strategy_lab_worker", "compute_worker", "worker-owner", process, fence=3),
        ],
        {process.pid: process},
    )

    assert result.green
    assert result.distinct_process_authorities == 1
    assert result.canonical_owner_id == "farm-owner"
    assert result.canonical_fence == 10
    assert result.resources == ("canonical_farm", "strategy_lab_worker")


def test_idle_canonical_farm_without_nested_worker_is_green() -> None:
    process = identity()
    result = assess(
        [row("canonical_farm", "farm", "farm-owner", process, fence=10)],
        {process.pid: process},
    )

    assert result.green
    assert result.distinct_process_authorities == 1
    assert result.resources == ("canonical_farm",)


def test_distinct_worker_pid_is_competing_process_authority() -> None:
    farm = identity()
    worker = identity(202)
    result = assess(
        [
            row("canonical_farm", "farm", "farm-owner", farm, fence=10),
            row("strategy_lab_worker", "compute_worker", "worker-owner", worker, fence=3),
        ],
        {farm.pid: farm, worker.pid: worker},
    )

    assert not result.green
    assert result.distinct_process_authorities == 2
    assert "distinct_process_authority" in result.errors


def test_pid_reuse_with_different_start_identity_fails_closed() -> None:
    farm = identity()
    reused = identity(started_at=11.0)
    result = assess(
        [
            row("canonical_farm", "farm", "farm-owner", farm, fence=10),
            row("strategy_lab_worker", "compute_worker", "worker-owner", reused, fence=3),
        ],
        {farm.pid: farm},
    )

    assert not result.green
    assert "distinct_process_authority" in result.errors
    assert "process_identity_mismatch" in result.errors


def test_same_pid_with_different_command_generation_fails_closed() -> None:
    farm = identity()
    different_command = identity(command_digest="sha256:other-command")
    result = assess(
        [
            row("canonical_farm", "farm", "farm-owner", farm, fence=10),
            row("strategy_lab_worker", "compute_worker", "worker-owner", different_command, fence=3),
        ],
        {farm.pid: farm},
    )

    assert not result.green
    assert "distinct_process_authority" in result.errors
    assert "process_identity_mismatch" in result.errors


def test_unexpected_writer_resource_fails_even_for_same_process() -> None:
    process = identity()
    result = assess(
        [
            row("canonical_farm", "farm", "farm-owner", process, fence=10),
            row("strategy_lab_worker_loop", "compute_worker_loop", "loop-owner", process, fence=7),
        ],
        {process.pid: process},
    )

    assert not result.green
    assert result.distinct_process_authorities == 1
    assert "unexpected_writer_authority" in result.errors


def test_canonical_owner_or_fence_generation_change_fails_closed() -> None:
    process = identity()
    rows = [row("canonical_farm", "farm", "new-owner", process, fence=11)]
    result = assess(
        rows,
        {process.pid: process},
        prior_canonical_owner_id="old-owner",
        prior_fences={"canonical_farm": 10},
    )

    assert not result.green
    assert "canonical_generation_changed" in result.errors


def test_nested_worker_fence_may_advance_but_never_regress() -> None:
    process = identity()
    rows = [
        row("canonical_farm", "farm", "farm-owner", process, fence=10),
        row("strategy_lab_worker", "compute_worker", "worker-owner", process, fence=4),
    ]
    advanced = assess(
        rows,
        {process.pid: process},
        prior_canonical_owner_id="farm-owner",
        prior_fences={"canonical_farm": 10, "strategy_lab_worker": 3},
    )
    regressed = assess(
        rows,
        {process.pid: process},
        prior_canonical_owner_id="farm-owner",
        prior_fences={"canonical_farm": 10, "strategy_lab_worker": 5},
    )

    assert advanced.green
    assert not regressed.green
    assert "fence_regression" in regressed.errors


def test_live_process_identity_mismatch_fails_closed() -> None:
    persisted = identity()
    live = identity(started_at=12.0)
    result = assess(
        [row("canonical_farm", "farm", "farm-owner", persisted, fence=10)],
        {persisted.pid: live},
    )

    assert not result.green
    assert "process_identity_mismatch" in result.errors


def test_expired_or_corrupt_authority_never_counts_green() -> None:
    process = identity()
    expired = assess(
        [row("canonical_farm", "farm", "farm-owner", process, fence=10, expires_at=NOW)],
        {process.pid: process},
    )
    corrupt_row = row("canonical_farm", "farm", "farm-owner", process, fence=10)
    corrupt_row["owner_id"] = None
    corrupt = assess([corrupt_row], {process.pid: process})

    assert not expired.green
    assert "expired_process_authority" in expired.errors
    assert not corrupt.green
    assert "corrupt_process_authority" in corrupt.errors


def test_task_claim_authority_uses_canonical_owner_not_nested_worker_owner() -> None:
    process = identity()
    result = assess(
        [
            row("canonical_farm", "farm", "farm-claim-owner", process, fence=10),
            row("strategy_lab_worker", "compute_worker", "compute-owner", process, fence=3),
        ],
        {process.pid: process},
    )

    assert result.green
    assert result.canonical_owner_id == "farm-claim-owner"
    assert result.canonical_owner_id != "compute-owner"
