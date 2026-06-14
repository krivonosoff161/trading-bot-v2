# -*- coding: utf-8 -*-
"""Run the Strategy Lab worker continuously.

The loop intentionally delegates actual job execution to worker_once.py. That
keeps the single-job boundary testable and makes this script only responsible
for scheduling, sleeping, and private logging.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.resource_policy import load_resource_policy  # noqa: E402
from src.research_lab.stop_intent import is_stop_requested  # noqa: E402


def run_once(
    private_root: Path,
    *,
    allow_public_output: bool = False,
    night_mode: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-X",
        "utf8",
        str(ROOT / "scripts" / "strategy_lab" / "worker_once.py"),
        "--private-root",
        str(private_root),
    ]
    if allow_public_output:
        cmd.append("--allow-public-output")
    if night_mode:
        cmd.append("--night-mode")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def append_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {message.rstrip()}\n")


def loop(
    private_root: Path,
    *,
    sleep_seconds: int = 60,
    error_sleep_seconds: int = 300,
    max_iterations: int = 0,
    allow_public_output: bool = False,
    night_mode: bool = False,
) -> int:
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    log_path = private_root / "logs" / "worker_loop.log"
    append_log(log_path, f"worker_loop started sleep={sleep_seconds}s night_mode={night_mode}")
    iteration = 0
    while True:
        if is_stop_requested(private_root):
            append_log(log_path, "worker_loop stopped by stop intent")
            return 0
        iteration += 1
        result = run_once(private_root, allow_public_output=allow_public_output, night_mode=night_mode)
        if result.stdout.strip():
            append_log(log_path, f"stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            append_log(log_path, f"stderr: {result.stderr.strip()}")
        append_log(log_path, f"iteration={iteration} exit={result.returncode}")
        if max_iterations and iteration >= max_iterations:
            append_log(log_path, "worker_loop stopped by max_iterations")
            return result.returncode
        time.sleep(error_sleep_seconds if result.returncode else sleep_seconds)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Private strategy-lab root",
    )
    ap.add_argument(
        "--sleep-seconds",
        type=int,
        default=None,
        help="Loop sleep between jobs; defaults to resource_policy min_seconds_between_jobs",
    )
    ap.add_argument(
        "--error-sleep-seconds",
        type=int,
        default=int(os.getenv("STRATEGY_LAB_ERROR_SLEEP_SECONDS", "300")),
    )
    ap.add_argument("--max-iterations", type=int, default=0)
    ap.add_argument("--night-mode", action="store_true", help="Opt in to relaxed night-mode resource limits")
    ap.add_argument("--allow-public-output", action="store_true", help="Allow writing under this public repo")
    args = ap.parse_args()
    night_mode = args.night_mode or os.getenv("STRATEGY_LAB_NIGHT_MODE", "").strip().lower() in {"1", "true", "yes"}
    sleep_seconds = args.sleep_seconds
    if sleep_seconds is None:
        env_sleep = os.getenv("STRATEGY_LAB_SLEEP_SECONDS")
        if env_sleep:
            sleep_seconds = int(env_sleep)
        else:
            sleep_seconds = load_resource_policy(night_mode=night_mode).min_seconds_between_jobs
    raise SystemExit(
        loop(
            Path(args.private_root),
            sleep_seconds=max(1, sleep_seconds),
            error_sleep_seconds=max(1, args.error_sleep_seconds),
            max_iterations=max(0, args.max_iterations),
            allow_public_output=args.allow_public_output,
            night_mode=night_mode,
        )
    )


if __name__ == "__main__":
    main()
