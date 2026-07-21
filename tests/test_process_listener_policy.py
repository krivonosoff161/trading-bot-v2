from __future__ import annotations

from pathlib import Path, PureWindowsPath

from src.research_lab.process_listener_policy import assess_canonical_rcc_listeners


OLLAMA = PureWindowsPath(r"C:\Users\tester\AppData\Local\Programs\Ollama\ollama.exe")
RUNNER = OLLAMA.parent / "lib" / "ollama" / "llama-server.exe"


def process(pid, ppid, exe, *, started=10.0, command=()):
    return {
        "pid": pid,
        "ppid": ppid,
        "exe": str(exe),
        "create_time": started,
        "cmdline": list(command),
    }


def assess(listeners, processes, *, started=10.0):
    return assess_canonical_rcc_listeners(
        listeners,
        processes,
        ollama_pid=101,
        ollama_started_at=started,
        expected_ollama_executable=OLLAMA,
    )


def listener(pid, port, host="127.0.0.1"):
    return {"pid": pid, "host": host, "port": port}


def canonical_tree(*, runner_command=(str(RUNNER), "--port", "52402")):
    return {
        101: process(101, 50, OLLAMA, command=(str(OLLAMA), "serve")),
        202: process(202, 101, RUNNER, started=20.0, command=runner_command),
    }


def test_loopback_ollama_api_and_exact_runner_are_green() -> None:
    result = assess(
        [listener(101, 11434), listener(202, 52402)],
        canonical_tree(),
    )

    assert result.green
    assert result.public_api_ports == (11434,)
    assert result.internal_runner_ports == (52402,)


def test_runner_may_be_a_deeper_exact_ollama_descendant() -> None:
    rows = canonical_tree()
    rows[303] = process(303, 202, RUNNER, started=21.0, command=(str(RUNNER), "--port=52403"))
    result = assess(
        [listener(101, 11434), listener(303, 52403, "::1")],
        rows,
    )

    assert result.green
    assert result.internal_runner_ports == (52403,)


def test_non_loopback_runner_bind_fails_closed() -> None:
    result = assess(
        [listener(101, 11434), listener(202, 52402, "0.0.0.0")],
        canonical_tree(),
    )

    assert not result.green
    assert "non_loopback_or_invalid_listener" in result.errors


def test_random_python_listener_is_not_an_ollama_runner() -> None:
    rows = canonical_tree()
    rows[202] = process(202, 101, Path("C:/Python/python.exe"), command=("python", "server.py"))
    result = assess([listener(101, 11434), listener(202, 52402)], rows)

    assert not result.green
    assert "unexpected_owned_listener" in result.errors


def test_llama_server_outside_trusted_ollama_tree_fails() -> None:
    rows = canonical_tree()
    rows[202] = process(
        202,
        101,
        Path("C:/Temp/llama-server.exe"),
        command=("C:/Temp/llama-server.exe", "--port", "52402"),
    )
    result = assess([listener(101, 11434), listener(202, 52402)], rows)

    assert not result.green
    assert "unexpected_owned_listener" in result.errors


def test_runner_from_an_unrelated_parent_fails() -> None:
    rows = canonical_tree()
    rows[202] = process(202, 999, RUNNER, command=(str(RUNNER), "--port", "52402"))
    result = assess([listener(101, 11434), listener(202, 52402)], rows)

    assert not result.green
    assert "unexpected_owned_listener" in result.errors


def test_runner_command_must_bind_the_observed_port() -> None:
    rows = canonical_tree(runner_command=(str(RUNNER), "--port", "60000"))
    result = assess([listener(101, 11434), listener(202, 52402)], rows)

    assert not result.green
    assert "unexpected_owned_listener" in result.errors


def test_ollama_root_pid_reuse_or_executable_drift_fails() -> None:
    reused = assess(
        [listener(101, 11434)],
        {101: process(101, 50, OLLAMA, started=11.0, command=(str(OLLAMA), "serve"))},
    )
    wrong_executable = assess(
        [listener(101, 11434)],
        {101: process(101, 50, Path("C:/Other/ollama.exe"), command=("ollama", "serve"))},
    )

    assert not reused.green
    assert "ollama_root_identity_mismatch" in reused.errors
    assert not wrong_executable.green
    assert "ollama_root_identity_mismatch" in wrong_executable.errors


def test_missing_public_api_or_unexpected_dashboard_port_fails() -> None:
    missing = assess([], canonical_tree())
    dashboard = assess(
        [listener(101, 11434), listener(404, 8765)],
        {**canonical_tree(), 404: process(404, 50, Path("C:/Python/python.exe"))},
    )

    assert not missing.green
    assert "ollama_public_api_listener_mismatch" in missing.errors
    assert not dashboard.green
    assert "unexpected_owned_listener" in dashboard.errors


def test_corrupt_listener_and_broken_parent_cycle_fail_closed() -> None:
    rows = canonical_tree()
    rows[202] = process(202, 303, RUNNER, command=(str(RUNNER), "--port", "52402"))
    rows[303] = process(303, 202, RUNNER)
    result = assess(
        [listener(101, 11434), listener(202, 52402), {"pid": "bad"}],
        rows,
    )

    assert not result.green
    assert "unexpected_owned_listener" in result.errors
    assert "corrupt_listener" in result.errors
