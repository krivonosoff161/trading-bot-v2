"""Paper-only main impulse forward runner.

Run manually:
    python scripts/ws/ws_main_impulse.py

Safety:
    - paper engine only, never sends orders;
    - requires main_impulse.auto_trade=false and AUTO_TRADE!=true;
    - separate from ws_main_screener and ws_impulse_pump.
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

from src.data.main_impulse_config import load_main_impulse_config  # noqa: E402
from src.data.main_impulse_engine import MainImpulseEngine  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paper main impulse WS runner")
    parser.add_argument("--check-config", action="store_true", help="load config and exit")
    return parser.parse_args()


def check_config() -> None:
    cfg = load_main_impulse_config()
    env_auto = os.getenv("AUTO_TRADE", "false").strip().lower()
    if env_auto in {"1", "true", "yes"} or cfg.get("auto_trade") or not cfg.get("paper", True):
        raise SystemExit("main_impulse config unsafe: keep paper=true and auto_trade=false")
    print(
        "main_impulse config ok | "
        f"enabled={cfg.get('enabled')} paper={cfg.get('paper')} "
        f"auto_trade={cfg.get('auto_trade')} pairs={len(cfg.get('pairs', []))} "
        f"exit_mode={cfg.get('exit_mode')}"
    )


async def main() -> None:
    args = parse_args()
    if args.check_config:
        check_config()
        return
    engine = MainImpulseEngine()
    await engine.run()


if __name__ == "__main__":
    asyncio.run(main())
