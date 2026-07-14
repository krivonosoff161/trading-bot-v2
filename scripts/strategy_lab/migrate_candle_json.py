# -*- coding: utf-8 -*-
"""Report or apply the non-destructive JSON candle migration."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.candle_migration import migrate_json_candles  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Import into SQLite. JSON sources are still retained.",
    )
    parser.add_argument("--report", help="Optional path for the JSON report")
    args = parser.parse_args()
    report = migrate_json_candles(args.private_root, apply=args.apply)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
