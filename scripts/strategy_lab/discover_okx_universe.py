# -*- coding: utf-8 -*-
"""Discover live OKX instruments and build a bounded classified universe snapshot.

Fetches the public OKX instruments list (live USDT-margined linear perps), classifies
them into bounded groups (crypto_major / meme_or_high_beta / tokenized_equity /
commodity / crypto_alt / unknown), diffs against the previous snapshot (new / delisted
/ group changes), and (on --apply) persists the snapshot under the private root. The
universe farm loop consumes the snapshot with --discover-okx-universe. The snapshot is
data, not a sweep: the loop's resource policy + symbol caps keep coverage bounded and
never run full-market or 1m sweeps.

    python -m scripts.strategy_lab.discover_okx_universe --dry-run
    python -m scripts.strategy_lab.discover_okx_universe --apply

Safety: public instruments endpoint only; no keys, no orders, no .env, no private data.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.instrument_discovery import (  # noqa: E402
    build_snapshot,
    diff_snapshots,
    is_fresh,
    load_snapshot,
    save_snapshot,
)
from src.research_lab.candle_store import CandleStore  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.providers.okx_instruments import InstrumentsError, OkxPublicInstrumentsProvider  # noqa: E402


def _summary_from_snapshot(snapshot: dict) -> dict:
    return {
        "count": int(snapshot.get("count") or 0),
        "group_sizes": {g: len(v) for g, v in (snapshot.get("groups") or {}).items()},
    }


def _persist_passports(private_root: Path, snapshot: dict, now_ms: int) -> int:
    catalog = snapshot.get("catalog") or snapshot.get("instruments") or {}
    return CandleStore(private_root).upsert_instruments(
        [row for row in catalog.values() if isinstance(row, dict)],
        updated_at_ms=now_ms,
    )


def discover(
    private_root: Path,
    *,
    apply: bool,
    now_ms: int,
    provider=None,
    ttl_seconds: int = 6 * 3600,
    force_refresh: bool = False,
) -> dict:
    old = load_snapshot(private_root)
    if old.get("instruments") and not force_refresh and is_fresh(old, now_ms, ttl_seconds):
        passports = _persist_passports(private_root, old, now_ms) if apply else 0
        summary = _summary_from_snapshot(old)
        return {
            "status": "cached",
            "cached": True,
            "generated_at": str(old.get("generated_at") or ""),
            **summary,
            "diff": {"new": 0, "delisted": 0, "group_changes": 0},
            "new_sample": [],
            "delisted": [],
            "passports_written": passports,
        }
    provider = provider or OkxPublicInstrumentsProvider()
    try:
        raw = provider.fetch_instruments("SWAP")
    except (InstrumentsError, ValueError) as exc:
        return {"status": "fetch_failed", "reason": str(exc)}
    generated_at = dt.datetime.fromtimestamp(now_ms / 1000, tz=dt.timezone.utc).isoformat()
    snapshot = build_snapshot(raw, generated_at=generated_at)
    diff = diff_snapshots(old, snapshot)
    if apply:
        private_root = resolve_private_root(private_root)
        save_snapshot(private_root, snapshot)
        passports = _persist_passports(private_root, snapshot, now_ms)
    else:
        passports = 0
    summary = _summary_from_snapshot(snapshot)
    return {
        "status": "discovered" if apply else "would_discover",
        "cached": False,
        **summary,
        "diff": {"new": len(diff["new_instruments"]), "delisted": len(diff["delisted"]),
                 "group_changes": len(diff["group_changes"])},
        "new_sample": diff["new_instruments"][:10],
        "delisted": diff["delisted"][:10],
        "passports_written": passports,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="fetch + classify + diff, write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="persist the snapshot under the private root")
    ap.add_argument("--ttl-seconds", type=int, default=6 * 3600,
                    help="reuse a fresh persisted snapshot for this many seconds")
    ap.add_argument("--force-refresh", action="store_true", help="ignore a fresh snapshot and call OKX")
    args = ap.parse_args()
    result = discover(
        Path(args.private_root),
        apply=bool(args.apply),
        now_ms=int(time.time() * 1000),
        ttl_seconds=args.ttl_seconds,
        force_refresh=bool(args.force_refresh),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
