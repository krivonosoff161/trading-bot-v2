"""Paper-only impulse pump forward runner.

Run manually:
    python scripts/ws/ws_impulse_pump.py

Safety:
    - paper engine only, never sends orders;
    - requires impulse_pump.auto_trade=false and AUTO_TRADE!=true;
    - wired into start_all.bat; runs only when impulse_pump.enabled=true.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.utils.runtime_root import load_runtime_dotenv  # noqa: E402

if __name__ == "__main__":
    load_runtime_dotenv(ROOT)

from src.data.impulse_pump_config import load_impulse_config  # noqa: E402
from src.data.impulse_pump_engine import ImpulsePumpEngine  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paper impulse pump WS runner")
    parser.add_argument("--check-config", action="store_true", help="load config and exit")
    return parser.parse_args()


def check_config() -> None:
    cfg = load_impulse_config()
    env_auto = os.getenv("AUTO_TRADE", "false").strip().lower()
    if env_auto in {"1", "true", "yes"} or cfg.get("auto_trade") or not cfg.get("paper", True):
        raise SystemExit("impulse_pump config unsafe: keep paper=true and auto_trade=false")
    print(
        "impulse_pump config ok | "
        f"enabled={cfg.get('enabled')} paper={cfg.get('paper')} "
        f"auto_trade={cfg.get('auto_trade')} pairs={len(cfg.get('pairs', []))}"
    )


async def main() -> None:
    args = parse_args()
    if args.check_config:
        check_config()
        return
    engine = ImpulsePumpEngine()
    await engine.run()


if __name__ == "__main__":
    asyncio.run(main())
