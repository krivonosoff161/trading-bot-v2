# -*- coding: utf-8 -*-
"""Write Obsidian farm-memory notes (daily run / symbol / family / candidate).

Builds deterministic notes from farm_results + the candidate registry under the
private root's obsidian-vault/Farm. Research labels only; no network, no secrets.

    python -m scripts.strategy_lab.write_farm_obsidian
    python -m scripts.strategy_lab.write_farm_obsidian --day 2026-06-18
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.farm_obsidian import write_farm_notes  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--day", default="", help="UTC date label (default: today)")
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()
    day = args.day or dt.datetime.now(dt.timezone.utc).date().isoformat()
    result = write_farm_notes(Path(args.private_root), day=day, allow_public_output=args.allow_public_output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
