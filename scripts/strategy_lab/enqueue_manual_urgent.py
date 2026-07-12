"""Queue one explicit urgent research request; no orders and no exchange calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.research_lab.farm_priority import PRIORITY_MANUAL_URGENT  # noqa: E402
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402


def enqueue_manual_urgent(
    private_root: Path,
    *,
    symbol: str,
    timeframe: str,
    reason: str,
    now: float | None = None,
) -> dict:
    now = time.time() if now is None else float(now)
    normalized_symbol = str(symbol).strip().upper().replace("-", "_")
    if not normalized_symbol:
        raise ValueError("symbol is required")
    if not normalized_symbol.endswith("_SWAP"):
        normalized_symbol += "_USDT_SWAP" if "_USDT" not in normalized_symbol else "_SWAP"
    normalized_tf = str(timeframe).strip().lower()
    if normalized_tf not in {"15m", "1h", "4h", "1d"}:
        raise ValueError("timeframe must be one of 15m, 1h, 4h, 1d")
    safe_reason = " ".join(str(reason or "manual urgent research").split())[:160]
    bucket = int(now // 60)
    raw = f"{normalized_symbol}|{normalized_tf}|{safe_reason}|{bucket}"
    event_id = "manual_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    event = {
        "event_id": event_id,
        "symbol": normalized_symbol,
        "source": "manual_urgent",
        "reason": safe_reason,
        "observed_at": now,
        "priority": PRIORITY_MANUAL_URGENT,
        "asset_class": "crypto_alt",
        "suggested_timeframes": [normalized_tf],
        "evidence": {"requested_by": "operator", "paper_only": True},
        "raw_ref": {"manual": True},
    }
    db = FarmTasksDB(tasks_db_path(private_root))
    try:
        _, created = db.upsert_intake_event(event, now=now)
    finally:
        db.close()
    return {
        "schema": "ManualUrgentResearch.v1",
        "event_id": event_id,
        "created": created,
        "symbol": normalized_symbol,
        "timeframe": normalized_tf,
        "priority": PRIORITY_MANUAL_URGENT,
        "paper_only": True,
        "execution_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue a paper-only urgent farm calculation")
    parser.add_argument("symbol")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--reason", default="manual urgent research")
    parser.add_argument("--private-root", default=str(DEFAULT_PRIVATE_ROOT))
    args = parser.parse_args()
    private_root = resolve_private_root(Path(args.private_root), allow_public_output=False)
    print(json.dumps(enqueue_manual_urgent(
        private_root,
        symbol=args.symbol,
        timeframe=args.timeframe,
        reason=args.reason,
    ), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
