# -*- coding: utf-8 -*-
"""Run the validated-setup paper runtime loop.

Paper/research only: reads setup_library + local prepared candles, writes
paper/paper_trades.jsonl, and never touches orders, .env, Telegram, or private
exchange endpoints.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paper_journal import paper_trades_path  # noqa: E402
from src.research_lab.paper_runtime import run_paper_cycle  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402


def _print_result(out: dict) -> None:
    c = out["counters"]
    print(
        "  "
        + " ".join(
            f"{k}={v}" for k, v in c.items() if isinstance(v, int) and (v or k == "cards")
        )
    )
    readiness = out.get("readiness") or {}
    if readiness:
        print(
            "  readiness: "
            f"checked={readiness.get('checked_cards', 0)} "
            f"paper_ready={readiness.get('paper_forward_ready', 0)} "
            f"plan_ready={readiness.get('plan_ready', 0)} "
            f"local_data_ready={readiness.get('local_data_ready', 0)}"
        )
        hard = readiness.get("by_hard_status") or {}
        if hard:
            print("  hard_status: " + " ".join(f"{k}={v}" for k, v in hard.items()))
        blockers = readiness.get("blocked_reasons") or {}
        if blockers:
            print("  blocked: " + " ".join(f"{k}={v}" for k, v in list(blockers.items())[:6]))
    for row in out.get("results", [])[:20]:
        parts = [str(row.get("setup_id")), str(row.get("status"))]
        if row.get("trade_id"):
            parts.append(str(row["trade_id"]))
        if row.get("outcome"):
            parts.append(f"outcome={row['outcome']}")
        if row.get("net_pct") is not None:
            parts.append(f"net={row['net_pct']}")
        if row.get("reason"):
            parts.append(f"reason={row['reason']}")
        print("  " + " | ".join(parts))


def main() -> None:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan and compute, write nothing")
    mode.add_argument("--apply", action="store_true", help="append paper outcomes")
    run = ap.add_mutually_exclusive_group()
    run.add_argument("--once", action="store_true", help="run one cycle and exit")
    run.add_argument("--loop", action="store_true", help="loop until stop-file/Ctrl+C")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--sleep-seconds", type=int, default=180)
    ap.add_argument("--stop-file", default="")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()

    apply = bool(args.apply)
    private_root = (
        resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
        if apply
        else Path(args.private_root)
    )
    print(
        f"paper_loop mode={'APPLY' if apply else 'DRY-RUN'} "
        f"run={'loop' if args.loop else 'once'} private_root={private_root}"
    )
    print("safety: paper-only; local prepared candles only; no orders / .env / Telegram")
    print(f"journal: {paper_trades_path(private_root)}")

    try:
        while True:
            if args.stop_file and Path(args.stop_file).exists():
                print(f"stop-file present ({args.stop_file}) - exiting")
                break
            out = run_paper_cycle(private_root, apply=apply, limit=args.limit)
            if not args.quiet or any(
                out["counters"].get(k, 0) for k in ("written", "completed", "errors")
            ):
                print(f"\n=== paper cycle @ {int(time.time())} ===")
                _print_result(out)
            if not args.loop:
                break
            time.sleep(max(1, args.sleep_seconds))
    except KeyboardInterrupt:
        print("\ninterrupted - graceful stop")


if __name__ == "__main__":
    main()
