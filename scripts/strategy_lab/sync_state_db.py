# -*- coding: utf-8 -*-
"""Import private strategy-lab completed runs into the SQLite state DB."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.state_db import default_db_path, import_completed_runs  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--private-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Private strategy-lab root",
    )
    ap.add_argument("--allow-public-output", action="store_true", help="Allow writing under this public repo")
    args = ap.parse_args()
    private_root = resolve_private_root(args.private_root, allow_public_output=args.allow_public_output)
    stats = import_completed_runs(private_root)
    print(
        f"db=strategy-lab/state/{default_db_path(private_root).name} "
        f"seen={stats['seen']} imported={stats['imported']} candidates={stats['candidates']}"
    )


if __name__ == "__main__":
    main()
