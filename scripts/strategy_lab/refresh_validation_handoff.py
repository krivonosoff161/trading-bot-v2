# -*- coding: utf-8 -*-
"""Read hard-validation artifacts back into farm_results (validation_exported / hard_status).

Run after export_hard_validation_requests --apply and the honest-backtest batch so the
farm result rows know which candidates were exported and what verdict they got. Reads
the private hard_validation/{requests,verdicts} dirs and updates only the research DB.

    python -m scripts.strategy_lab.refresh_validation_handoff

No network, no orders, no .env, no private endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402
from src.research_lab.state_db import connect, default_db_path, init_db  # noqa: E402
from src.research_lab.validation_handoff import refresh_from_artifacts  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    args = ap.parse_args()
    root = Path(args.private_root)
    db_path = default_db_path(root)
    if not db_path.exists():
        print(json.dumps({"status": "no_db", "db": str(db_path)}))
        return
    conn = connect(db_path)
    init_db(conn)
    try:
        result = refresh_from_artifacts(conn, root)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
