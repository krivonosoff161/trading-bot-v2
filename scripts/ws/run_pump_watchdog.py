"""
Pump Orchestrator watchdog — auto-restarts on crash.
Usage: python scripts/ws/run_pump_watchdog.py
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "ws_smart_pump.py"
RESTART_DELAY_SEC = 30
MAX_RESTARTS_PER_HOUR = 10


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def run() -> None:
    restarts: list[float] = []

    print(f"[{_now()}] WATCHDOG started | script={SCRIPT.name}")

    while True:
        now = time.time()
        # Drop restarts older than 1 hour
        restarts = [t for t in restarts if now - t < 3600]

        if len(restarts) >= MAX_RESTARTS_PER_HOUR:
            print(
                f"[{_now()}] WATCHDOG abort | {MAX_RESTARTS_PER_HOUR} restarts in 1h — "
                "likely persistent crash, stopping watchdog"
            )
            sys.exit(1)

        print(f"[{_now()}] WATCHDOG launch | attempt={len(restarts) + 1}")
        proc = subprocess.run([sys.executable, str(SCRIPT)])

        exit_code = proc.returncode
        print(f"[{_now()}] WATCHDOG process exited | code={exit_code}")

        if exit_code == 0:
            print(f"[{_now()}] WATCHDOG clean exit — not restarting")
            break

        restarts.append(time.time())
        print(f"[{_now()}] WATCHDOG restart in {RESTART_DELAY_SEC}s ...")
        time.sleep(RESTART_DELAY_SEC)


if __name__ == "__main__":
    run()
